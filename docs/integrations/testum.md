# nervum × testum — контракт интеграции

> **Аудитория:** разработчики testum, реализующие двустороннюю интеграцию
> с SDN Controller'ом (nervum). Документ описывает северный (REST) API и
> южный канал доставки событий (webhook + snapshot/tail).

## 0. Принципы

* **nervum и testum — параллельные платформы.** testum не «над»
  nervum'ом и не «под» — он соседний потребитель state'а. Контроллер
  принимает *намерение*, рассказывает «как реально получилось» через
  события; testum же отображает/валидирует это в своих сценариях.
* **CLI (sdnctl) — для людей и debug'а.** Интеграция идёт **только**
  через REST + webhooks. Никаких exec-вызовов или скрапинга вывода
  CLI.
* **At-least-once, monotonic, idempotent.** Любое событие может
  прийти повторно; ordering гарантируется монотонным
  `event_id`; payload содержит достаточно полей, чтобы подписчик
  легко дедуплицировал.
* **Recovery всегда возможно через snapshot.** Подписка может
  пропустить часть событий (рестарт, network partition, disabled
  после ошибок) — `GET /events/snapshot` отдаёт полное состояние
  с актуальным watermark, дальше подписчик читает delta через
  `GET /events?since=`.

---

## 1. Аутентификация и роли

Интеграция использует service account c ролью **`admin`** (этого
требует `webhook:write`) либо специальный аккаунт, у которого вручную
выданы `webhook:read` + `webhook:write` + те read-права, которые
нужны для bootstrap-сценариев (`network:read`, `node:read`, ...).

```bash
# Создать SA с админ-ролью (через bootstrap-токен)
curl -X POST https://controller.example/api/v1/service-accounts \
  -H "Authorization: Bearer $BOOTSTRAP_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "testum-sync", "role": "admin", "description": "testum integration"}'

# Выпустить токен
curl -X POST https://controller.example/api/v1/service-accounts/$SA_ID/tokens \
  -H "Authorization: Bearer $BOOTSTRAP_ADMIN_TOKEN" \
  -d '{"label": "production"}'
# → {"plaintext": "<save_this_once>", ...}
```

Plaintext возвращается **ровно один раз**. Хранить — в SecretStore
testum'а; для нашего auth-слоя достаточно SHA-256 хэша.

Все mutating-запросы testum'а (если они есть) должны прокидывать
заголовок `X-Source-Task-Id: <task_id>` — он попадёт и в audit log
(`actor = sa:testum-sync+testum:<task_id>`), и в payload audit-events,
и testum сможет найти свою цепочку.

---

## 2. Контракт событий

### 2.1. Event-payload envelope

Все события (как в webhook'е, так и в `/events`) сериализуются в
одинаковую структуру:

```json
{
  "event_id": 42,                          // monotonic int (PK в outbox)
  "id": "outbox_a3f...",                   // string id для логов
  "event_type": "network.applied",         // <resource>.<verb>
  "resource_type": "network",
  "resource_id": "net_b5c...",             // nullable для глобальных событий
  "occurred_at": "2026-05-19T12:34:56Z",   // ISO-8601 UTC
  "payload": { ... }                        // event-specific, см. 2.3
}
```

> **Канонический формат подписи (см. 3.3):** `json.dumps(envelope,
> sort_keys=True, separators=(",", ":"))`. Любые отличия от этой
> канонизации сломают HMAC.

### 2.2. Каталог `event_type`

| event_type                  | resource_type | Когда                                           |
|-----------------------------|--------------|--------------------------------------------------|
| `network.created`           | `network`    | `POST /networks` succeeded                       |
| `network.updated`           | `network`    | `PATCH /networks/{id}` (только при changed)      |
| `network.nodes_assigned`    | `network`    | `POST /networks/{id}/nodes` (если diff != 0)     |
| `network.applied`           | `network`    | `POST /networks/{id}/apply` → succeeded          |
| `network.apply_failed`      | `network`    | `POST /networks/{id}/apply` → failed             |
| `node.registered`           | `node`       | `POST /nodes`                                    |
| `node.enrolled`             | `node`       | `POST /agent/enroll` (агент впервые активен)     |
| `node.removed`              | `node`       | `DELETE /nodes/{id}`                             |

Список расширяется; новые типы добавляются с обратной совместимостью
(никогда не переименовываются, новые поля только опциональны).

### 2.3. Поля `payload` по типам

```jsonc
// network.created
{
  "name": "tenant-a",
  "type": "vxlan",
  "intent_version": 1,
  "spec_hash": "acc1def6...",
  "node_ids": ["node_xxx", "node_yyy"]
}

// network.updated
{
  "name": "tenant-a",
  "intent_version": 2,
  "spec_hash": "..."
}

// network.nodes_assigned
{
  "name": "tenant-a",
  "intent_version": 3,
  "node_ids": ["node_xxx", "node_yyy", "node_zzz"]
}

// network.applied / network.apply_failed
{
  "name": "tenant-a",
  "intent_version": 3,
  "spec_hash": "...",
  "operation_id": "op_123...",
  "node_count": 3,
  "ok": true               // false для network.apply_failed
}

// node.registered
{
  "name": "edge-1",
  "mgmt_ip": "10.0.0.1",
  "status": "pending"
}

// node.enrolled
{
  "name": "edge-1",
  "mgmt_ip": "10.0.0.1",
  "agent_version": "0.5.0",
  "status": "online"
}

// node.removed
{
  "name": "edge-1"
}
```

Никаких секретов (plaintext tokens, hash'и) в payload **не попадает**.
Если нужны более богатые сведения о ресурсе — testum дёргает
соответствующий read-endpoint по `resource_id` (`GET /networks/{id}`
и т.д.).

---

## 3. Webhook subscriptions

### 3.1. Создание

```bash
curl -X POST https://controller.example/api/v1/webhooks \
  -H "Authorization: Bearer $TESTUM_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "target_url": "https://testum.internal/sdn-events",
    "event_types": ["*"],                  // или ["network.applied", "node.enrolled"]
    "description": "production-sync",
    "labels": {"env": "prod"}
  }'
```

Ответ (HTTP 201):

```json
{
  "subscription": {
    "id": "whsub_...",
    "target_url": "https://testum.internal/sdn-events",
    "event_types": ["*"],
    "state": "active",
    "cursor": 17,                          // head outbox'а в момент create
    "failure_count": 0,
    ...
  },
  "secret_plaintext": "<random-32-bytes-urlsafe>"
}
```

* `secret_plaintext` показывается **ровно один раз**. Контроллер
  хранит только SHA-256 хэш. Утрата — пересоздание подписки.
* `cursor` стартует с текущего head'а: новая подписка не получит
  «весь архив» с момента запуска контроллера, только новые события.

### 3.2. Доставка

Каждое событие летит отдельным POST'ом на `target_url`:

```http
POST /sdn-events HTTP/1.1
Host: testum.internal
Content-Type: application/json
X-SDN-Event-Id: 42
X-SDN-Event-Type: network.applied
X-SDN-Delivery-Id: 7b3a91c5fc2d8e64
X-SDN-Signature: sha256=2b3f...
Content-Length: 312

{"event_id":42,"event_type":"network.applied",...}
```

* `X-SDN-Event-Id` дублирует `event_id` из тела — удобно для
  быстрой фильтрации без парсинга JSON.
* `X-SDN-Delivery-Id` — уникальный id одной попытки доставки; при
  retry контроллер сгенерирует новый. Подписчик использует его для
  idempotent-обработки если хранит «уже видел».
* Контроллер ждёт `2xx` (200/202/204) ≤ `webhook_request_timeout`
  (default 5s). Всё прочее — failure.

### 3.3. Верификация HMAC

```python
import hmac, hashlib

def verify_signature(body: bytes, header: str, secret: str) -> bool:
    if not header.startswith("sha256="):
        return False
    received = header.removeprefix("sha256=")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(received, expected)
```

**body** — raw bytes, как пришли в HTTP-теле. Не нормализовывать,
не reparse-ить JSON. Контроллер уже формирует canonical-форму
(`sort_keys=True, separators=(",", ":")`).

### 3.4. Retry, disable, recovery

* После каждого failure'а `failure_count` инкрементируется. Cursor
  **не двигается**, пока не пройдёт ok.
* Когда `failure_count >= webhook_max_failures` (default 10),
  подписка автоматически переводится в `state="disabled"`.
* Disabled подписки **не получают событий**. Их видно в
  `GET /webhooks` с `state="disabled"` и `last_delivery_status`,
  объясняющим причину.
* **Recovery sequence:**
  1. `GET /webhooks/{id}` — смотрим `cursor` и `state`.
  2. Если `state=disabled`: пересоздаём (`POST /webhooks`) с тем же
     `target_url` — получаем новый secret и `cursor = head`. Хвост
     **до этого момента** дочитываем через `GET /events?since=`
     (см. §4).
  3. Если `state=active` (просто rate-limit или таймауты):
     контроллер сам продолжит после следующего dispatcher-tick'а.

---

## 4. Snapshot + tail (catch-up без webhook'ов)

### 4.1. Snapshot

```bash
curl https://controller.example/api/v1/events/snapshot \
  -H "Authorization: Bearer $TESTUM_TOKEN"
```

```json
{
  "event_id": 123,
  "networks": [ /* NetworkOut ... */ ],
  "nodes":    [ /* NodeOut    ... */ ]
}
```

* `event_id` — watermark. Все события с `event_id <= 123` уже
  отражены в snapshot'е.
* Snapshot минимален: только networks + nodes. IPAM, service
  accounts и operations подписчик читает через свои read-эндпоинты
  по необходимости.

### 4.2. Tail

```bash
curl 'https://controller.example/api/v1/events?since=123&limit=200' \
  -H "Authorization: Bearer $TESTUM_TOKEN"
```

```json
{
  "head_event_id": 145,
  "items": [
    {"event_id": 124, "event_type": "network.applied", ...},
    {"event_id": 125, "event_type": "node.enrolled",   ...},
    ...
  ]
}
```

* `head_event_id` — текущая верхушка. Если `head_event_id ==
  last_item.event_id` — testum «догнал», можно прекращать polling.
* Параметры: `since` (default `0`, не включает), `limit`
  (default `200`, max `1000`).

### 4.3. Канонический паттерн «cold start»

```python
# 1. Снапшот.
snap = GET("/events/snapshot")
state.reset_with(snap.networks, snap.nodes)
cursor = snap.event_id

# 2. Поднять live-канал (webhook), отметив свой cursor.
sub = POST("/webhooks", {"target_url": ..., "event_types": ["*"]})

# 3. Догнать дельту между snapshot и live-cursor.
while True:
    page = GET(f"/events?since={cursor}&limit=200")
    for ev in page.items:
        apply(ev)
        cursor = ev.event_id
    if cursor >= page.head_event_id:
        break

# 4. Live-обработка идёт через webhook; cursor обновляется по event_id.
```

Этот же цикл — рецепт recovery после длительного простоя.

---

## 5. Идемпотентность и ordering

* **Ordering:** глобальный, по `event_id` (monotonically increasing
  int). Контроллер никогда не отдаст событие с меньшим `event_id`
  после большего.
* **Дедупликация:** подписчик хранит max(event_id) и игнорирует
  всё `<=`. Альтернативно — `X-SDN-Delivery-Id` как ключ
  idempotency.
* **Связь intent → applied:** каждое `network.applied` несёт
  `operation_id` и `spec_hash`. Если testum триггерит apply (или
  читает события от чужого триггера), он сопоставляет состояние
  через `spec_hash` (или, при инициированном им apply'е, через
  `operation_id`, который вернулся в ответ ручки).
* **Connect-after-disconnect:** see §4.3 — recovery всегда через
  snapshot или `since=cursor`.

---

## 6. Correlation headers (M13 — SDN-056)

Северные ручки принимают и возвращают набор заголовков для трассировки:

| Заголовок          | Направление | Назначение                                          |
|--------------------|-------------|-----------------------------------------------------|
| `X-Request-Id`     | client → server, server → response | Произвольный uuid; если не задан — генерируем. |
| `X-Source-Task-Id` | client → server | id-связка с задачей в testum (попадает в audit/operations) |
| `X-Operation-Id`   | server → response | id асинхронной операции, если ручка её создала |

testum'у рекомендуется проставлять оба:
- `X-Request-Id` = id своего внешнего запроса
- `X-Source-Task-Id` = id своей таски, инициировавшей действие

---

## 7. Rate limits

* `SDN_RATELIMIT_PER_MINUTE > 0` — token bucket per principal
  (sha256-префикс bearer'а), refill `N/60` токенов в секунду.
* Превышение → `429 Too Many Requests` с заголовком `Retry-After:
  60` и телом:
  ```json
  {"error": {"code": "rate_limited", "message": "...",
             "details": {"retry_after_seconds": 60}}}
  ```
* `GET /events?since=` не освобождён от rate-limit'а. testum
  должен **жить на webhook'ах** и обращаться к tail-ручке только
  при cold-start / recovery.

---

## 8. Прочее, что полезно знать

* **HTTPS (SDN-036).** Северный API в production должен выставляться
  только по HTTPS (reverse-proxy или native uvicorn-TLS). Контроллер
  никогда не передаёт plaintext-секреты вне ручки create (SDN-043).
* **Per-network apply lock (SDN-037).** Два параллельных apply'я
  одной сети — невозможны: второй получает `409 Conflict` с
  `code="apply_already_running"` и `details.holder_operation_id`,
  по которому можно watch'нуть завершение через
  `GET /operations/{id}`.
* **Auto-disable webhook'а.** При длительной недоступности подписчика
  контроллер «вырубит сам себя» (sequence: failure, failure, …,
  disabled). Это намеренное поведение: лучше явно сигнализировать
  оператору, чем тихо копить очередь.
* **Лимиты payload'а.** Контроллер не лимитирует размер исходящего
  webhook-тела сейчас, но события компактны (< 4 KB как правило).
  Подписчику тем не менее стоит читать запрос ограниченно (e.g.
  16 KB cap), чтобы не словить out-of-memory при недобросовестном
  proxy.
* **Версионирование контракта.** Поля только **добавляются**,
  никогда не переименовываются и не удаляются. Семантическое
  изменение → новый `event_type` (например, `network.applied.v2`).
  Подписчику следует игнорировать незнакомые `event_type` и
  незнакомые поля внутри payload'а.

---

## 9. Чеклист готовности подписчика

- [ ] Service account создан, токен лежит в SecretStore testum'а.
- [ ] Endpoint `target_url` HTTPS, возвращает `2xx` ≤ 5s,
      верифицирует `X-SDN-Signature` через HMAC-SHA256.
- [ ] Подписчик хранит max(event_id) и дропает дубли.
- [ ] Реализован cold-start (`/events/snapshot` + `/events?since=`).
- [ ] При 429 / временных ошибках подписчик не паникует —
      контроллер сам ретраит webhook'и; при `state=disabled`
      срабатывает recovery-флоу.
- [ ] В `X-Request-Id` и `X-Source-Task-Id` пробрасываются ваши
      внутренние корреляторы.
