# Задание: интеграция testum ↔ nervum (SDN-controller)

> Бриф для команды/AI-агента, который будет реализовывать
> интеграционный коннектор **со стороны testum**. Полный контракт —
> в [`testum.md`](testum.md); этот документ — короткое
> «с чего начать и что построить».

## Кто мы и зачем

**testum** — наша внешняя management-платформа: web UI, RBAC,
провижининг узлов через SSH, GitOps, backups, host-снапшоты.
**nervum** — параллельный specialized control-plane для SDN:
declarative networks, IPAM, planner/reconciler/drift/operations.
testum **не** управляет nervum'ом — они соседние потребители общего
state'а.

Твоя задача: научить testum **синхронизироваться с nervum** так,
чтобы в UI testum'а в режиме «как есть сейчас» отображались сети/
узлы из nervum'а и события об их изменениях.

## Что у тебя уже есть

* Полный REST-API контракт + событийный канал nervum'а описан в
  [`docs/integrations/testum.md`](testum.md) репозитория nervum.
  **Это твой основной источник правды — открой и прочитай первым.**
* Контроллер уже умеет:
  * выпускать service account'ы и токены (Bearer);
  * принимать заголовки `X-Source-Task-Id` / `X-Request-Id`;
  * отдавать snapshot контроллера + outbox-tail
    (`/events/snapshot`, `/events?since=`);
  * писать webhook'и с HMAC-SHA256 подписью (заголовок
    `X-SDN-Signature: sha256=<hex>`);
  * auto-disable подписки после N подряд неудач;
  * rate-limit'ить (429) и блокировать параллельные apply'и одной
    сети (409).

## Что построить со стороны testum

### 1. Connector-модуль `nervum-sync`

Отдельный сервис (или background-worker внутри основного
приложения) с **тремя ответственностями**:

1. **Bootstrap.** При первом запуске или после долгого простоя
   тянет `GET /api/v1/events/snapshot`, заливает networks/nodes в
   локальную реплику-таблицу `nervum_*`, запоминает `event_id` как
   watermark.
2. **Live.** Подписан на webhook'и nervum'а (создание подписки —
   `POST /api/v1/webhooks` с `event_types=["*"]`). На каждое
   событие валидирует HMAC, обновляет реплику, двигает watermark.
3. **Recovery.** Если watermark отстал от reality (например, после
   простоя или потери webhook'ов) — догоняет через
   `GET /api/v1/events?since=<watermark>` пока `head_event_id`
   не сравняется с последним полученным.

### 2. HTTP-endpoint для приёма webhook'ов

* Путь и port — на твой выбор (например, `POST /webhooks/nervum`).
* Должен:
  * Принимать тело максимум 64 KB (отрезай больше).
  * Верифицировать `X-SDN-Signature` через `hmac.compare_digest`.
    Секрет берёшь из своего SecretStore по `subscription_id`
    (см. ниже).
  * Дедуплицировать по `X-SDN-Event-Id` (или по
    `X-SDN-Delivery-Id`, если хранишь полную историю delivery).
  * Возвращать `2xx` за ≤ 5 секунд. Иначе nervum поставит failure
    и через 10 подряд отключит подписку.
  * **Не блокировать обработку**: положи событие в очередь
    (внутреннюю или Redis/RabbitMQ — что у вас есть), верни 200,
    обработай асинхронно.

### 3. SecretStore для webhook-секретов

При `POST /api/v1/webhooks` nervum возвращает `secret_plaintext`
**ровно один раз**. Сохрани его связку `{subscription_id: secret}`
в нормальный secret manager (Vault / k8s secret / у вас уже есть
бэкенд?). **Не** клади в БД в открытом виде.

### 4. Реплика-схема в БД testum

Минимум — две таблицы, отражающие соответствующий state nervum'а:

```sql
CREATE TABLE nervum_networks (
  id             VARCHAR PRIMARY KEY,
  name           VARCHAR NOT NULL,
  type           VARCHAR NOT NULL,
  vni            INT,
  vlan_id        INT,
  mtu            INT,
  intent_version INT NOT NULL,
  spec_hash      VARCHAR NOT NULL,
  node_ids       JSONB,
  raw            JSONB,           -- сырое тело snapshot/event для UI
  updated_at     TIMESTAMPTZ
);

CREATE TABLE nervum_nodes (
  id             VARCHAR PRIMARY KEY,
  name           VARCHAR NOT NULL,
  mgmt_ip        VARCHAR NOT NULL,
  status         VARCHAR NOT NULL,
  agent_version  VARCHAR,
  raw            JSONB,
  updated_at     TIMESTAMPTZ
);

CREATE TABLE nervum_sync_state (
  id             INT PRIMARY KEY DEFAULT 1,
  watermark      BIGINT NOT NULL,        -- max processed event_id
  last_synced_at TIMESTAMPTZ
);
```

(Точные имена — на твой стиль. Главное — отдельные таблицы, чтобы
было видно, что это **зеркало**, а не source-of-truth.)

### 5. Correlation

Все исходящие запросы testum → nervum проставляют:

* `X-Source-Task-Id: <id текущей testum-задачи>` — попадёт в audit
  nervum'а как `actor = sa:testum-sync+testum:<task_id>`, и
  оператор найдёт цепочку «их task → наш operation».
* `X-Request-Id: <uuid>` — твой собственный correlation id.

## Acceptance criteria (как проверить)

* [ ] При первом подключении к чистому nervum'у синхронизируется
      пустой snapshot, дальше идут события — UI testum'а
      отображает networks и nodes по мере их создания.
* [ ] Killing connector на 5 минут, потом restart → reconnect
      догоняет всю дельту через `/events?since=`, состояние
      сходится.
* [ ] Подмена HMAC-байта → endpoint возвращает `401`, событие
      не применяется к реплике.
* [ ] Дубль webhook'а с тем же `X-SDN-Event-Id` → реплика
      не двигается (idempotency).
* [ ] При apply сети, инициированном из nervum-side, в audit
      nervum'а видно `actor=sa:<sa>+testum:<task_id>`, если ты
      проставил `X-Source-Task-Id`.

## Подводные камни

1. **HMAC body = raw bytes.** Не json.loads → json.dumps —
   подпись слетит. Контроллер пишет canonical-форму
   (`sort_keys=True, separators=(",",":")`); ты её получаешь
   as-is, проверяешь как есть.
2. **Cold-start order matters.** Сначала snapshot, **потом**
   подписка на webhook'и. Иначе live-события придут до того, как
   реплика загрузится, и ты потеряешь несколько событий
   (cursor подписки стартует с head outbox'а в момент create).
3. **Auto-disable.** Если твой endpoint месяц лежит — подписка
   уйдёт в `state=disabled`. Контроллер сам её не разморозит:
   пересоздавай (новый `secret`, новый `cursor=head`) + догоняй
   delta через `/events?since=`.
4. **At-least-once.** Дедупликация — твоя ответственность. Храни
   `max(event_id)` либо `seen_delivery_ids` (TTL = retry-окно).
5. **Rate-limit.** На tail (`/events?since=`) тоже
   распространяется. Live-канал — webhook'и; на tail ходи только
   при cold-start / recovery.
6. **Spec_hash, не intent_version.** При сверке «что мы видим vs
   что говорит nervum» сравнивай `spec_hash` (hash desired-state),
   не `intent_version` (счётчик мутаций). Версия растёт даже на
   no-op-update'ах.

## Что нужно от nervum-команды

* Service account `testum-sync` с ролью `admin` (для
  `webhook:write` + `webhook:read`). Получи токен через bootstrap-
  admin или существующего админа.
* URL контроллера (`https://controller.example` либо ваш
  staging-endpoint).
* Если нужны дополнительные event_type'ы (например, IPAM,
  operations status) — попроси, добавим. Список текущих
  event_type'ов: см. таблицу в [`testum.md`](testum.md) §2.2.

## Связь

* Тех-вопросы по контракту, расширение event-каталога —
  [#sdn-controller или ваш канал].
* Issue по интеграции — заводи в репозитории testum с тегом
  `area/nervum-sync`.
