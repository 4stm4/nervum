# Nervum SDN Controller

Декларативный SDN-контроллер с архитектурой портов и адаптеров. Принимает
*намерение* через REST API, хранит *desired state*, reconciler'ом приводит
узлы к нему через **NetOS Agent**, который локально управляет OVS, nftables,
dnsmasq и CoreDNS.

```
Операторы / CI / UI
        │  REST API + Bearer
        ▼
┌─────────────────────────┐
│     SDN Controller      │  FastAPI · SQLAlchemy · Alembic
│  desired state · IPAM   │  SQLite (MVP) / PostgreSQL
│  reconciler · audit     │  Python 3.12+
└──────────┬──────────────┘
           │  HTTPS + mTLS (enrollment-token)
           ▼
┌─────────────────────────┐
│      NetOS Agent        │  FastAPI (southbound)
│  OVS · nftables         │  на каждом compute-узле
│  dnsmasq · CoreDNS      │
└─────────────────────────┘
```

---

## Возможности

| Блок | Что реализовано |
|------|----------------|
| **N0 — Мультитенантность** | Projects, RBAC (роли: admin / member / viewer), Service Accounts, Bootstrap admin-token, audit outbox |
| **N1 — L2-ресурсы** | LogicalPort (IPAM, MAC, lifecycle), SecurityGroup, AddressPool, ServiceObject, QoSPolicy |
| **N2 — Безопасность** | SecurityPolicy (правила ingress/egress, компиляция → nftables, apply/verify/drift), TrunkPort 802.1Q |
| **N3 — L3-маршрутизация** | Router (статические маршруты, IPv6/SLAAC/DHCPv6), FloatingIP, BGP Peer, HA (VRRP) |
| **N4 — Управление** | Quotas, Preflight-checks, Snapshots, GatewayBond, RetentionPolicy, LoadBalancer (L4) |
| **N5 — Расширенные** | ApplySchedule (cron), MirrorSession (port-mirroring), VPN-туннели |
| **Инфраструктура** | Operations state machine, Topology view, Drift detection, Backup/Restore, Webhooks, Enrollment tokens, TLS/mTLS, OpenTelemetry tracing, Prometheus metrics, Rate limiting, Fernet-encrypted SecretStore |

---

## Быстрый старт (разработка)

```bash
# 1. Окружение
make install          # создаёт .venv и устанавливает зависимости + dev extras

# 2. БД (SQLite по умолчанию)
make migrate          # применяет все Alembic-миграции

# 3. Контроллер
SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN=dev-token make run
# → http://127.0.0.1:8080

# 4. Проверка
curl -H "Authorization: Bearer dev-token" http://127.0.0.1:8080/api/v1/readyz
```

### PostgreSQL

```bash
pip install -e ".[postgres]"
export SDN_PERSISTENCE=postgres
export SDN_DATABASE_URL=postgresql+asyncpg://user:pass@localhost/sdn
make migrate && make run
```

---

## Конфигурация

Все переменные имеют префикс `SDN_`. Значения по умолчанию безопасны для
локальной разработки.

| Переменная | По умолчанию | Описание |
|------------|-------------|----------|
| `SDN_HTTP_HOST` | `0.0.0.0` | Адрес прослушивания |
| `SDN_HTTP_PORT` | `8080` | Порт |
| `SDN_ENV` | `dev` | Окружение: `dev` / `staging` / `prod` |
| `SDN_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `SDN_LOG_FORMAT` | `json` | `json` / `console` |
| `SDN_PERSISTENCE` | `sqlite` | `memory` / `sqlite` / `postgres` |
| `SDN_DATABASE_URL` | `sqlite+aiosqlite:///./sdn_controller.db` | URL подключения к БД |
| `SDN_AUTH_ENABLED` | `true` | Включить проверку Bearer-токенов |
| `SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN` | — | Одноразовый admin-токен при первом запуске |
| `SDN_AGENT_MTLS_ENABLED` | `false` | mTLS между контроллером и агентами |
| `SDN_AGENT_MTLS_CA_CERT_PATH` | — | CA для верификации агентов |
| `SDN_AGENT_MTLS_CLIENT_CERT_PATH` | — | Клиентский сертификат контроллера |
| `SDN_AGENT_MTLS_CLIENT_KEY_PATH` | — | Приватный ключ |
| `SDN_TLS_ENABLED` | `false` | TLS на northbound API |
| `SDN_TLS_CERT_FILE` | — | Сертификат сервера |
| `SDN_TLS_KEY_FILE` | — | Приватный ключ сервера |
| `SDN_BACKGROUND_TASKS_ENABLED` | `false` | Reconciler + webhooks dispatcher + reaper |
| `SDN_RECONCILER_INTERVAL_SECONDS` | `300` | Период автоматического reconcile |
| `SDN_RECONCILER_AUTO_APPLY` | `false` | Применять план без ручного подтверждения |
| `SDN_RATELIMIT_PER_MINUTE` | `0` | Rate limit (req/min per principal; 0 = выключено) |
| `SDN_SECRET_STORE_BACKEND` | `memory` | `memory` / `file` (Fernet-шифрование) |
| `SDN_SECRET_STORE_KEY` | — | Fernet-ключ (32 байта, base64-url) |
| `SDN_OTEL_ENABLED` | `false` | OpenTelemetry tracing |
| `SDN_OTEL_EXPORTER_OTLP_ENDPOINT` | — | URL OTLP-collector (HTTP/protobuf) |
| `SDN_AUDIT_RETENTION_DAYS` | `365` | Хранение audit-событий |
| `SDN_OPERATION_RETENTION_DAYS` | `90` | Хранение операций |

---

## REST API

Базовый путь: `/api/v1`. OpenAPI-схема: `GET /openapi.json`.

| Ресурс | Путь |
|--------|------|
| Healthz / Readyz | `GET /health` · `/api/v1/readyz` |
| Метрики Prometheus | `GET /metrics` |
| **Проекты** | `/api/v1/projects` |
| **Узлы** | `/api/v1/nodes` |
| **Сети** | `/api/v1/networks` |
| IPAM: подсети / пулы / аллокации | `/api/v1/subnets` · `/api/v1/networks/{id}/subnets` · `/api/v1/allocations` |
| **Логические порты** | `/api/v1/logical-ports` |
| Группы безопасности | `/api/v1/security-groups` |
| Пулы адресов | `/api/v1/address-pools` |
| Сервис-объекты | `/api/v1/service-objects` |
| QoS-политики | `/api/v1/qos-policies` |
| **Политики безопасности** | `/api/v1/security-policies` |
| Транк-порты 802.1Q | `/api/v1/trunk-ports` |
| **Маршрутизаторы** | `/api/v1/routers` |
| Floating IP | `/api/v1/floating-ips` |
| BGP Peers | `/api/v1/bgp-peers` |
| Gateway Bonds | `/api/v1/gateway-bonds` |
| Load Balancers | `/api/v1/load-balancers` · `/api/v1/lb-listeners` · `/api/v1/lb-pools` · `/api/v1/lb-members` |
| Health Monitors | `/api/v1/health-monitors` |
| Квоты | `/api/v1/quotas` |
| Preflight | `/api/v1/preflight` |
| Расписания | `/api/v1/schedules` |
| Mirror-сессии | `/api/v1/mirror-sessions` |
| VPN-туннели | `/api/v1/vpn-tunnels` |
| **Операции** | `/api/v1/operations` |
| Топология | `/api/v1/topology` |
| Снимки | `/api/v1/snapshots` |
| Аудит | `/api/v1/audit-events` |
| Outbox-события | `/api/v1/events` |
| Вебхуки | `/api/v1/webhooks` |
| Service Accounts | `/api/v1/service-accounts` |
| Backup / Restore | `/api/v1/backup` |
| Агент (прокси) | `/api/v1/agent` |

---

## CLI (`sdnctl`)

```bash
sdnctl --help
sdnctl --url http://sdn:8080 --token $TOKEN nodes list
sdnctl networks create --name prod --type vxlan --vni 10100
sdnctl operations watch <operation-id>
sdnctl topology show
sdnctl drift scan --network-id <id>
sdnctl backup export > backup.json
sdnctl backup import < backup.json
sdnctl audit events --since 24h
```

---

## NetOS Agent

Агент запускается на каждом compute-узле. Принимает план от контроллера и
применяет его локально.

```bash
# Переменные с префиксом NETOS_AGENT_
NETOS_AGENT_OVS_BACKEND=subprocess \
NETOS_AGENT_FIREWALL_BACKEND=nftables \
netos-agent --host 0.0.0.0 --port 9100
```

Southbound API агента (используется контроллером):

| Маршрут | Описание |
|---------|----------|
| `GET /v1/node` | Информация об узле (CPU, ОС, возможности) |
| `GET /v1/ovs/state` | Состояние OVS (мосты, порты, версия) |
| `POST /v1/network/apply` | Применить план шагов |
| `GET /v1/system/stats` | Системная статистика |
| `GET /health` | Healthcheck |

---

## Тестирование

```bash
# Unit + integration (848 тестов, ~130 сек)
make test

# С покрытием
make cov

# Только unit
pytest tests/unit

# Только integration
pytest tests/integration
```

### E2E на rpi4-codex (QEMU)

Тесты запускаются на удалённом хосте `rpi4-codex` в QEMU-виртуальной машине
с реальным Nervum. Требует SSH-доступ к хосту и готовый QEMU-образ.

```bash
# Синхронизация кода на rpi4-codex, запуск QEMU + pytest там же
# Логи остаются на rpi4-codex: /tmp/nervum-e2e-qemu/logs/run.log

# N0 + N1 + N2 + N3 (73 теста)
ssh rpi4-codex "bash /tmp/nervum-e2e-qemu/repo/scripts/e2e/qemu-n3.sh"

# Слежение за логами
ssh rpi4-codex "tail -f /tmp/nervum-e2e-qemu/logs/run.log"
```

Маркеры тестов: `n0`, `n1`, `n2`, `n3`, `dp` (dataplane: OVS + nftables).

---

## Структура репозитория

```
src/
├─ sdn_controller/
│  ├─ core/          # чистая доменная логика (entities, value_objects, services, use_cases)
│  ├─ ports/         # Protocol-интерфейсы (persistence, agent, secret_store, …)
│  ├─ adapters/      # реализации: http_api (FastAPI), sql (SQLAlchemy), memory, netos_agent
│  ├─ cli/           # sdnctl
│  ├─ migrations/    # 18 Alembic-версий
│  └─ app/           # точка входа, DI-контейнер, конфигурация
│
└─ netos_agent/
   ├─ core/          # доменная логика агента
   ├─ ports/         # интерфейсы: ovsdb, firewall, dhcp, dns, system, snapshots
   ├─ adapters/      # ovsdb_subprocess, firewall_nftables, dhcp_dnsmasq, dns_coredns, …
   └─ app/           # точка входа агента

tests/
├─ unit/             # 502 теста, без I/O
├─ integration/      # 346 тестов, TestClient + in-memory
├─ e2e_qemu/         # 73 теста, реальный Nervum в QEMU (N0–N3)
└─ e2e_dp/           # 14 тестов, реальный OVS + nftables в QEMU (dataplane)
```

---

## Зависимости

| Компонент | Библиотеки |
|-----------|-----------|
| HTTP-сервер | FastAPI ≥ 0.115, Uvicorn |
| Валидация | Pydantic v2, pydantic-settings |
| БД | SQLAlchemy[asyncio] ≥ 2.0, aiosqlite, asyncpg (опционально) |
| Миграции | Alembic ≥ 1.13 |
| HTTP-клиент | httpx |
| Async | anyio |
| Логирование | structlog |
| Метрики | prometheus-client |
| Шифрование | cryptography (Fernet) |
| Трассировка | opentelemetry-api/sdk (экспортёр в extra `[otel]`) |

---

## Документация

| Документ | Описание |
|---------|---------|
| [`docs/deployment.md`](docs/deployment.md) | Установка с нуля: TLS, mTLS, PostgreSQL, DR |
| [`docs/user-guide.md`](docs/user-guide.md) | Эксплуатация: CLI, типовые сценарии, RBAC, troubleshooting |
| [`docs/testing/e2e-qemu.md`](docs/testing/e2e-qemu.md) | E2E-стенд на QEMU: структура, запуск, отладка |
| [`docs/sdn-controller-plan.md`](docs/sdn-controller-plan.md) | Архитектурный план разработки |

---

## Лицензия

GNU Affero General Public License v3.0 — см. [LICENSE](LICENSE).
