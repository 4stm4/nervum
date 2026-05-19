# SDN Controller — руководство по внедрению

Документ для **администратора**, который ставит контроллер и
агентов с нуля. Описывает архитектуру, установку, конфигурацию,
обновление и DR.

Для оператора уже развёрнутой системы — см.
[user-guide.md](user-guide.md).

## Содержание

1. [Архитектура](#1-архитектура)
2. [Требования](#2-требования)
3. [Установка контроллера](#3-установка-контроллера)
4. [Конфигурация контроллера (Settings)](#4-конфигурация-контроллера-settings)
5. [Bootstrap admin-токена](#5-bootstrap-admin-токена)
6. [Запуск контроллера](#6-запуск-контроллера)
7. [Установка агента (`netos-agent`)](#7-установка-агента-netos-agent)
8. [mTLS между контроллером и агентами](#8-mtls-между-контроллером-и-агентами)
9. [Observability](#9-observability)
10. [Backup и DR](#10-backup-и-dr)
11. [Обновление](#11-обновление)
12. [Безопасность: чек-лист «в прод»](#12-безопасность-чек-лист-в-прод)
13. [Troubleshooting на стороне инфраструктуры](#13-troubleshooting-на-стороне-инфраструктуры)

---

## 1. Архитектура

```
┌─────────────────────────┐    Bearer (northbound)    ┌──────────────┐
│   Operators / CI / UI   │ ───────────────────────▶ │              │
└─────────────────────────┘                            │              │
                                                       │   SDN        │
┌─────────────────────────┐    /metrics, OpenAPI       │  Controller  │
│  Prometheus / Grafana   │ ───────────────────────▶ │              │
└─────────────────────────┘                            │              │
                                                       │   FastAPI    │
                                                       │   + SQLAlchemy
                                                       │   + Alembic  │
                                                       │              │
                                                       └──────┬───────┘
                                                              │
                          mTLS + enrollment-token │ HttpAgentClient
                                                              │
                                ┌─────────────────────────────┘
                                ▼
                  ┌───────────────────────────┐
                  │  netos-agent  (на узле)   │
                  │                           │
                  │  OVSDB ─┐                 │
                  │         ├─► OVS bridges,  │
                  │         ├─► VXLAN ports,  │
                  │  DHCP ──┤   nftables,     │
                  │  DNS  ──┤   dnsmasq /     │
                  │  FW   ──┘   CoreDNS       │
                  └───────────────────────────┘
```

Контроллер сам не лезет в kernel/OVS — все мутации проходят через
агента по plan-протоколу. Контроллер хранит desired-state (сети,
сабнеты, аллокации) и snapshot последнего наблюдённого OVS-state.

## 2. Требования

### Контроллер

* Linux/macOS, **Python 3.12+**.
* Доступ исходящий до агентов на их HTTPS-порту (по умолчанию 8443
  у `netos-agent`).
* Открытый порт `8080/tcp` для northbound API.
* Persistence — на выбор:
  * **SQLite** (`sqlite+aiosqlite:///./sdn_controller.db`) — MVP,
    одиночный процесс, никаких отдельных сервисов;
  * **PostgreSQL** (`postgresql+asyncpg://…`) — для multi-writer и
    больших аудит-таблиц. Требует extra `pip install
    'sdn-controller[postgres]'`.
* Любой Prometheus-scraper для `/metrics`.

### Агент (на каждом узле)

* Linux с включёнными:
  * `openvswitch-switch` (>= 3.x);
  * `nftables`;
  * `dnsmasq` (если используется backend `dnsmasq` для DHCP);
  * `coredns` (если используется backend `coredns` для DNS).
* root/cap_net_admin для манипуляций OVS/nftables.

## 3. Установка контроллера

```bash
git clone <repo> sdn-controller
cd sdn-controller
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e .                 # для SQLite
# либо
pip install -e '.[postgres]'     # для Postgres
```

После установки доступны три entry point'а:

| Команда           | Что это                                           |
|-------------------|---------------------------------------------------|
| `sdn-controller`  | Запуск northbound HTTP-сервиса (uvicorn внутри)   |
| `netos-agent`     | Запуск агента на узле                             |
| `sdnctl`          | CLI клиент к northbound API                       |

Применить миграции БД:

```bash
export SDN_DATABASE_URL=sqlite+aiosqlite:///./sdn_controller.db
alembic upgrade head     # пройдёт 0001 → 0008
```

При смене на Postgres достаточно поменять URL и пройти `upgrade head`
заново — модели и миграции переносятся между бэкендами.

## 4. Конфигурация контроллера (Settings)

Все настройки через переменные окружения с префиксом `SDN_`. Файл
`.env` в рабочем каталоге тоже подхватывается (см.
`sdn_controller/app/config.py`).

| Переменная                              | Дефолт                                       | Назначение                                                                                            |
|-----------------------------------------|----------------------------------------------|-------------------------------------------------------------------------------------------------------|
| `SDN_ENV`                               | `dev`                                        | `dev` / `staging` / `prod`                                                                            |
| `SDN_HTTP_HOST`                         | `0.0.0.0`                                    | Bind-адрес для northbound API                                                                         |
| `SDN_HTTP_PORT`                         | `8080`                                       | TCP-порт                                                                                              |
| `SDN_LOG_LEVEL`                         | `INFO`                                       | `DEBUG\|INFO\|WARNING\|ERROR`                                                                         |
| `SDN_LOG_FORMAT`                        | `json`                                       | `json` (прод) / `console` (dev)                                                                       |
| `SDN_PERSISTENCE`                       | `sqlite`                                     | `memory` / `sqlite` / `postgres`                                                                      |
| `SDN_DATABASE_URL`                      | `sqlite+aiosqlite:///./sdn_controller.db`    | SQLAlchemy URL                                                                                        |
| `SDN_DATABASE_ECHO`                     | `false`                                      | Логировать каждый SQL — оставлять только для отладки                                                  |
| `SDN_ENROLLMENT_TOKEN_TTL_SECONDS`      | `3600`                                       | TTL одноразового токена для агента                                                                    |
| `SDN_NODE_STALE_AFTER_SECONDS`          | `90`                                         | Порог `online → stale` (нет heartbeat'а)                                                              |
| `SDN_NODE_OFFLINE_AFTER_SECONDS`        | `300`                                        | Порог `stale → offline`                                                                               |
| `SDN_AUTH_ENABLED`                      | `true`                                       | Включает Bearer-auth и RBAC. В тестах/dev можно `false`                                               |
| `SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN`        | —                                            | Plaintext bootstrap-токена admin'а; при старте идемпотентно регистрируется в БД                       |
| `SDN_AUTH_BOOTSTRAP_ADMIN_NAME`         | `bootstrap-admin`                            | Имя service account'а для bootstrap'а                                                                 |
| `SDN_AGENT_MTLS_ENABLED`                | `false`                                      | Требовать mTLS при ходе к агентам                                                                     |
| `SDN_AGENT_MTLS_CA_CERT_PATH`           | —                                            | CA, которому верит контроллер при проверке сертификата агента                                          |
| `SDN_AGENT_MTLS_CLIENT_CERT_PATH`       | —                                            | Клиентский сертификат контроллера                                                                     |
| `SDN_AGENT_MTLS_CLIENT_KEY_PATH`        | —                                            | Приватный ключ контроллера                                                                            |

> При `SDN_AGENT_MTLS_ENABLED=true` контроллер **отказывается стартовать**,
> если хотя бы один из путей не задан или файл не существует — это
> failed-closed: лучше упасть, чем тихо ходить по plaintext'у.

## 5. Bootstrap admin-токена

Контроллер при старте идемпотентно регистрирует service account
`bootstrap-admin` (имя меняется через
`SDN_AUTH_BOOTSTRAP_ADMIN_NAME`) и привязывает к нему plaintext из
`SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN`. Это «второе ключевое отверстие»:

1. Сгенерируйте 256-битный токен заранее:
   ```bash
   python -c 'import secrets; print(secrets.token_hex(32))'
   ```
2. Запишите его в `SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN` (передавайте через
   secret manager — это не env-var для дефолта).
3. После старта войдите этим токеном, выпустите реальные service
   account'ы для людей/CI:
   ```bash
   sdnctl --token "$BOOTSTRAP" service-accounts ...
   # (или через REST, см. user-guide §4.6)
   ```
4. **Отзовите** bootstrap-токен (или удалите env-var и перезапустите —
   на старте без переменной bootstrap-аккаунт остаётся, но новых
   токенов не получит).

Если переменная пустая, bootstrap пропускается — это допустимо, если
вы знаете другой способ создать первый admin-токен (например,
прокинуть `auth_enabled=False` на момент инициализации, что
обоснованно только для одноразового скрипта).

## 6. Запуск контроллера

### 6.1. Локально

```bash
export SDN_PERSISTENCE=sqlite
export SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN=$(python -c 'import secrets; print(secrets.token_hex(32))')
echo $SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN > /etc/sdn/bootstrap-admin.token
chmod 600 /etc/sdn/bootstrap-admin.token

alembic upgrade head
sdn-controller     # запускает uvicorn на $SDN_HTTP_HOST:$SDN_HTTP_PORT
```

### 6.2. systemd-юнит

`/etc/systemd/system/sdn-controller.service`:

```ini
[Unit]
Description=SDN Controller
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=sdn
Group=sdn
EnvironmentFile=/etc/sdn/controller.env
WorkingDirectory=/var/lib/sdn-controller
ExecStartPre=/opt/sdn/.venv/bin/alembic upgrade head
ExecStart=/opt/sdn/.venv/bin/sdn-controller
Restart=on-failure
RestartSec=3

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/sdn-controller
CapabilityBoundingSet=
AmbientCapabilities=

[Install]
WantedBy=multi-user.target
```

`/etc/sdn/controller.env`:

```env
SDN_ENV=prod
SDN_LOG_FORMAT=json
SDN_LOG_LEVEL=INFO
SDN_PERSISTENCE=postgres
SDN_DATABASE_URL=postgresql+asyncpg://sdn:***@db:5432/sdn
SDN_AUTH_ENABLED=true
SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN=...
SDN_AGENT_MTLS_ENABLED=true
SDN_AGENT_MTLS_CA_CERT_PATH=/etc/sdn/tls/ca.crt
SDN_AGENT_MTLS_CLIENT_CERT_PATH=/etc/sdn/tls/controller.crt
SDN_AGENT_MTLS_CLIENT_KEY_PATH=/etc/sdn/tls/controller.key
```

### 6.3. Контейнер

Контроллер — обычный ASGI-сервис, можно завернуть в любую uvicorn-
совместимую обёртку (`uvicorn sdn_controller.app.main:app`). Образ
должен проходить миграции на старте (`alembic upgrade head`); в
рантайме user должен быть непривилегированный.

## 7. Установка агента (`netos-agent`)

На каждом узле:

```bash
pip install -e '.'              # тот же пакет
```

Конфигурация — `NETOS_*` env-vars (см. `netos_agent/app/config.py`):

| Переменная                       | Назначение                                                                                       |
|----------------------------------|---------------------------------------------------------------------------------------------------|
| `NETOS_HTTP_HOST`/`NETOS_HTTP_PORT` | Bind агента (по умолчанию `127.0.0.1:8443` под mTLS)                                              |
| `NETOS_DHCP_BACKEND`             | `fake` для тестов / `dnsmasq`                                                                     |
| `NETOS_DNS_BACKEND`              | `fake` / `coredns`                                                                                |
| `NETOS_FIREWALL_BACKEND`         | `fake` / `nftables`                                                                                |
| `NETOS_DNSMASQ_PATH`             | Путь к `dnsmasq` (по умолчанию `/usr/sbin/dnsmasq`)                                              |
| `NETOS_COREDNS_PATH`             | Путь к `coredns`                                                                                  |
| `NETOS_NFT_PATH`                 | Путь к `nft`                                                                                       |
| `NETOS_OVSDB_BACKEND`            | `fake` (для интеграции без OVS) / `subprocess` (через `ovs-vsctl`/`ovs-ofctl`)                   |

Запуск аналогичен контроллеру (можно systemd-юнит, но требуется
`CapabilityBoundingSet=CAP_NET_ADMIN` для OVS/nftables).

После старта агент:

1. Принимает enrollment-токен на `POST /v1/node/enroll` и сохраняет
   `node_id`.
2. Отдаёт `agent.snapshot()` / `agent.apply_plan()` контроллеру.

### Регистрация узла на контроллере

```bash
# 1. Оператор регистрирует узел в pending
sdnctl nodes register node-prod-1 --mgmt-ip 10.0.0.11

# 2. Оператор выпускает токен
sdnctl nodes enroll-token <node_id>
# token: enroll_…
# expires_at: 2026-05-19T13:00:00+00:00

# 3. Оператор передаёт token агенту (через secret manager).
# 4. Агент дёргает POST /api/v1/agent/enroll и отдаёт свой
#    serverous tls_thumbprint (sha256 hex) для pinning.
```

При успешном enroll'е статус узла становится `online`.

## 8. mTLS между контроллером и агентами

Контроллер ↔ агент в продакшене ходят по mTLS. Минимальная схема:

1. Сгенерируйте корневой CA — он подписывает:
   * **серверный** сертификат каждого агента (CN/SAN = mgmt_ip/DNS);
   * **клиентский** сертификат контроллера.
2. На каждом агенте включите HTTPS-listener с этим сертификатом и
   приватным ключом.
3. Контроллер запускайте с:
   ```env
   SDN_AGENT_MTLS_ENABLED=true
   SDN_AGENT_MTLS_CA_CERT_PATH=/etc/sdn/tls/ca.crt
   SDN_AGENT_MTLS_CLIENT_CERT_PATH=/etc/sdn/tls/controller.crt
   SDN_AGENT_MTLS_CLIENT_KEY_PATH=/etc/sdn/tls/controller.key
   ```
4. Агент при enroll'е передаёт SHA-256 thumbprint своего серверного
   сертификата в поле `tls_thumbprint` (`POST /api/v1/agent/enroll`).
   Контроллер запоминает thumbprint в `nodes.tls_thumbprint` — это
   pinned identity, по нему сверяется серверный серт на будущих
   запросах.

### Ротация сертификатов

* **Серверного у агента** — переподнимите агент с новой парой и
  заэнрольте его повторно (контроллер обновит `tls_thumbprint`).
* **Клиентского у контроллера** — замените файлы по путям из env;
  агентам ничего обновлять не нужно (доверие у них на CA, не на
  thumbprint контроллера).
* **CA** — замена корневого CA = полная переэмиссия + переэнрол.
  Делайте это «по сценарию» с предварительным фростом писательских
  ручек.

## 9. Observability

### Prometheus-метрики

Эндпоинт `GET /metrics` (без auth — стандарт для scraper'а).
Минимальный набор для дашборда:

```promql
# rate ошибок 5xx
rate(sdn_http_requests_total{status=~"5.."}[5m])

# p95 latency по эндпоинту
histogram_quantile(0.95,
  sum(rate(sdn_http_request_duration_seconds_bucket[5m]))
    by (le, path))

# rate отказов аутентификации
rate(sdn_auth_failures_total[5m])
```

### Структурные логи

`SDN_LOG_FORMAT=json` отдаёт JSON-line на stdout/stderr. Поля:

| Поле           | Значение                                              |
|----------------|-------------------------------------------------------|
| `timestamp`    | ISO-8601 UTC                                          |
| `level`        | `INFO\|WARNING\|ERROR`                                |
| `event`        | Имя события (`domain_error`, `audit_write_failed`)    |
| `request_id`   | Корреляционный id из заголовка `X-Request-Id`         |
| `http_method`  | GET/POST/...                                          |
| `http_path`    | URL                                                   |
| `code`         | Доменный код ошибки (если применимо)                  |

Подсуньте в любой log shipper (Vector, Fluent Bit) — он не требует
лишних трансформаций.

### Audit

`audit_events` — append-only таблица в SQL, доступна через
`GET /api/v1/audit-events` (admin). На каждый mutating-запрос
audit middleware пишет запись с `actor`/`action`/`resource`/`request_id`.
Включайте retention politik в БД-плане (например, перенос старше 1
года в холодное хранилище).

## 10. Backup и DR

### Контроллер: bundle JSON

Снять полный снимок состояния:

```bash
sdnctl --token "$ADMIN" backup export -f /var/backups/sdn-$(date +%F).json
```

В bundle входит то, что нужно для disaster recovery: service accounts,
nodes, networks (вместе с сабнетами), ip_allocations, audit-журнал.
**Не входит**: plaintext'ы enrollment/service-токенов (после restore
выдаются заново), `observed_state` (восстановится при следующем
`apply`).

Регулярная задача (cron / systemd timer):

```bash
0 2 * * *  /usr/local/bin/sdnctl --token "$ADMIN" backup export \
              -f /var/backups/sdn-$(date +\%F).json
find /var/backups -name 'sdn-*.json' -mtime +30 -delete
```

### Контроллер: восстановление

1. Поднимите чистую БД (пустая таблица — иначе импорт упадёт с 409).
2. `alembic upgrade head`.
3. Импорт:
   ```bash
   sdnctl --token "$ADMIN" backup import /var/backups/sdn-2026-05-19.json
   ```
4. Выпустите новые service tokens (старые plaintext'ы не вернутся).
5. Узлам выпустите новые enrollment-токены (или переподключите
   агентов с теми же `tls_thumbprint`).

### Агент: snapshot/restore состояния узла

`sdnctl snapshots take <node_id> --label pre-upgrade` фиксирует на
агенте OVS-state + edge-services и оставляет на контроллере
указатель (`agent_snapshot_id`, `state_hash`, label).
`sdnctl snapshots restore <snapshot_id>` откатывает узел к этому
состоянию. Делайте перед апгрейдом OVS/nftables/dnsmasq на узле.

## 11. Обновление

Минор-релизы:

1. `git pull` + `pip install -e '.'`.
2. `alembic upgrade head` (миграции 0001-0008 идемпотентны; новые
   будут с номера 0009).
3. `systemctl restart sdn-controller`.

Перед мажорным или ломающим обновлением:

1. Снимите bundle (см. §10).
2. Заскейлите критичных операторов в read-only — `viewer` токены не
   страдают; писательских можно временно `service-accounts/<id>/disable`.
3. Возьмите snapshot ключевых узлов.

Алёмбик-migration 0009+ должны быть **обратимыми** — если решите
откатиться, `alembic downgrade -1` должно работать. Тесты в проекте
гоняют forward-migrations на каждом интеграционном прогоне; downgrade
тестируется руками для каждого нового revision.

## 12. Безопасность: чек-лист «в прод»

* [ ] `SDN_AUTH_ENABLED=true` (никогда не оставляйте `false` в проде).
* [ ] `SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN` задан и хранится в secret
      manager; после первого старта токен **отозван**.
* [ ] `SDN_AGENT_MTLS_ENABLED=true` + все три пути заданы; контроллер
      падает на старте без них (failed-closed).
* [ ] Production-роль:
      * у CI-агентов — `automation`, а не `admin`;
      * у людей — `network_operator` или `viewer`, а не `admin`;
      * `admin`-токенов 1-2 на компанию.
* [ ] Service-токены имеют `ttl_seconds` (≤ 30 дней — best practice).
* [ ] Регулярный backup (см. §10) и тест restore хотя бы раз в
      квартал.
* [ ] systemd-юнит контроллера запущен под непривилегированным
      пользователем (`User=sdn`), `NoNewPrivileges=true`,
      `ProtectSystem=strict`.
* [ ] Prometheus scraper'у разрешён доступ к `/metrics`, но не к
      `/api/v1/*` без токена.
* [ ] HTTPS-терминация перед контроллером (nginx/envoy) — северный
      API лучше не выставлять plain-HTTP, даже если auth включён.
* [ ] Audit-стрим направлен в WORM-хранилище (s3 object-lock,
      lvm-snapshot, что есть).

## 13. Troubleshooting на стороне инфраструктуры

| Симптом                                              | Что проверить                                                                                                                                |
|------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------|
| Контроллер не стартует, `ValidationError: agent_mtls_*` | Включён `SDN_AGENT_MTLS_ENABLED=true`, но один из путей пустой или файла нет. Сверяйте `ls -l /etc/sdn/tls/`.                                |
| `alembic upgrade head` падает на ревизии 0006        | На существующей БД остались записи без `created_at` или `service_accounts.labels`. Восстановите из bundle или допишите defaults вручную.    |
| Агент возвращает `agent.snapshot` 500                | Скорее всего, у `netos-agent` нет cap_net_admin или OVSDB-сокет недоступен. `journalctl -u netos-agent` покажет конкретику.                  |
| `sdnctl networks apply` уходит в `failed`            | См. user-guide §8: `sdnctl operations show <op_id>` — `events.payload.failed_steps[].message`.                                                |
| `/metrics` отдаёт 401                                | Перед контроллером стоит обратный прокси, который требует auth ко всему. У `/metrics` исключение должно быть на уровне прокси.              |
| Prom-кардинальность взрывается                       | Скорее всего, кто-то прокинул конкретный URL вместо шаблона маршрута. Метрика label `path` берётся из `request.scope["route"].path` — проверьте, что нет своих middleware'ов, ломающих scope. |
| BD-Postgres падает с `cannot insert into generated column` | Используется неподдерживаемая версия asyncpg. Закрепите `asyncpg>=0.30`.                                                                       |
