# SDN Controller — руководство оператора

Документ для **оператора** уже развёрнутого контроллера: что в нём
есть, как им пользоваться через CLI `sdnctl` и через REST API,
типовые сценарии и шаблоны устранения проблем.

Если вы ставите контроллер с нуля — сначала
[deployment.md](deployment.md).

## Содержание

1. [Концепции](#1-концепции)
2. [Подключение к контроллеру](#2-подключение-к-контроллеру)
3. [CLI `sdnctl`](#3-cli-sdnctl)
4. [Типовые сценарии](#4-типовые-сценарии)
5. [RBAC: роли и права](#5-rbac-роли-и-права)
6. [REST API и OpenAPI](#6-rest-api-и-openapi)
7. [Observability: метрики, логи, аудит](#7-observability-метрики-логи-аудит)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. Концепции

* **Узел (node)** — хост с агентом `netos-agent`, на котором живёт
  OVS, dnsmasq, CoreDNS, nftables. Контроллер не вмешивается в
  кернел узла напрямую — все действия идут через агента.
* **Сеть (network)** — *интент*, описание желаемого состояния (тип,
  vlan/vni, MTU, подсеть, NAT, firewall). Сама по себе сеть ничего
  не делает; она «привязывается» к набору узлов и потом «apply» эту
  привязку реализует.
* **Операция (operation)** — каждое мутирующее действие
  (`create network`, `assign-nodes`, `apply`, `subnet upsert`,
  `register node` …) создаёт `Operation` с собственным state
  machine'ом (`accepted → planning → running → verifying →
  succeeded/failed`). По operation_id можно посмотреть, что
  происходило, и watch'нуть до завершения.
* **Apply — это reconcile**: контроллер наблюдает (`observe`)
  состояние узла через агент, считает diff между desired и observed
  и пушит план шагов (создание моста, VXLAN-туннелей, DHCP-области,
  записи nftables…). После apply пишется снимок `observed_state`.
* **Topology** — read-only снимок графа (узлы, сети, наблюдённые мосты,
  рёбра node↔network и vxlan-туннели). Не дёргает агентов.
* **Drift** — read-only сравнение desired и кэшированного observed.
  Возвращает структурный дрейф на OVS-уровне (мост пропал, vxlan-порт
  поменялся, etc.); edge-сервисы (DHCP/DNS/NAT/FW) в drift намеренно
  не входят — их идемпотентность владеет агент.

## 2. Подключение к контроллеру

### 2.1. Адрес

CLI и API лежат на одном HTTP-эндпоинте, по умолчанию
`http://127.0.0.1:8080`. Переопределить можно либо переменной
окружения, либо флагом:

```bash
export SDN_CONTROLLER_URL=https://sdn.example.internal
# либо
sdnctl --url https://sdn.example.internal nodes list
```

### 2.2. Аутентификация

Контроллер с включённым auth (production) ждёт `Authorization: Bearer
<token>`. Токен оператор получает от admin'а через выпуск service
account'а (см. ниже [«Создание service account и токена»](#создание-service-account-и-токена)).

Передать токен в CLI:

```bash
export SDN_TOKEN=abc...64hex...
# либо
sdnctl --token abc... networks list
```

В REST-вызове напрямую:

```bash
curl -H "Authorization: Bearer $SDN_TOKEN" $SDN_CONTROLLER_URL/api/v1/networks
```

### 2.3. Запросы и `X-Request-Id`

На каждый ответ контроллер возвращает `X-Request-Id`. Если задать
свой заголовок `X-Request-Id` при запросе — он вернётся обратно и
попадёт во все серверные логи и audit-event'ы по этому запросу.
Удобно для корреляции из CI/скриптов.

## 3. CLI `sdnctl`

`sdnctl --help` — корневая справка, `sdnctl <команда> --help` —
по подкомандам. Глобальные опции:

| Опция            | Назначение                                                              |
|------------------|-------------------------------------------------------------------------|
| `--url URL`      | Адрес контроллера (или `$SDN_CONTROLLER_URL`)                           |
| `--token TOKEN`  | Bearer-токен (или `$SDN_TOKEN`)                                         |
| `--output table\|json`<br/>(`-o`) | Формат вывода. Дефолт — таблица; `json` нужен для пайплайнов (jq) |

Exit-коды:
* `0` — успех;
* `1` — клиентская ошибка (4xx);
* `2` — серверная ошибка (5xx) или транспортная (DNS, TLS, timeout).

### 3.1. Узлы

```bash
sdnctl nodes list
sdnctl nodes register node-a --mgmt-ip 10.0.0.1
sdnctl nodes register node-b --mgmt-ip 10.0.0.2 --label region=eu --label rack=r1
sdnctl nodes enroll-token <node-id>       # одноразовый bearer для агента
sdnctl nodes remove <node-id>
```

### 3.2. Сети

```bash
sdnctl networks list
sdnctl networks show prod                  # принимает id или name

sdnctl networks create prod \
  --type vxlan --vni 10100 --mtu 1450 \
  --cidr 10.100.0.0/24 --gateway 10.100.0.1 \
  --node <node-a-id> --node <node-b-id>

sdnctl networks assign-nodes prod --node <node-c-id>   # заменяет membership
sdnctl networks apply prod                              # observe → diff → push → verify
```

### 3.3. Операции

```bash
sdnctl operations list --limit 20
sdnctl operations show op_…
sdnctl operations watch op_…              # поллит до succeeded/failed
```

`watch` подходит для CI-скриптов: код возврата равен `0` для
`succeeded`, `1` для `failed/cancelled/rolled_back`, `2` для
таймаута.

### 3.4. Topology и drift

```bash
sdnctl topology                            # человеко-читаемая таблица
sdnctl topology -o json | jq '.edges'      # машино-читаемо

sdnctl drift scan                          # exit 0 если чисто, 1 при дрейфе
```

### 3.5. Audit

```bash
sdnctl audit list --limit 50
sdnctl audit list --actor ops-ci --action network.create
sdnctl audit list --resource-type network --resource-id net_prod
```

### 3.6. Backup и snapshot узла

```bash
# Контроллер
sdnctl backup export -f /var/backups/sdn-2026-05-19.json
sdnctl backup import /var/backups/sdn-2026-05-19.json   # в пустую БД!

# Узел: снапшот OVS/edge-state на агенте
sdnctl snapshots take <node-id> --label pre-upgrade
sdnctl snapshots list <node-id>
sdnctl snapshots restore <snapshot-id>
```

## 4. Типовые сценарии

### 4.1. Завести новый узел

1. Зарегистрировать в pending:
   ```bash
   sdnctl nodes register node-c --mgmt-ip 10.0.0.3
   ```
   Запишите `node_id` из ответа.
2. Выпустить одноразовый enrollment-токен:
   ```bash
   sdnctl nodes enroll-token <node_id>
   ```
3. Отдать токен агенту (см. deployment.md). После того как агент
   стучится в `/api/v1/agent/enroll`, узел переходит в `online`.
4. Проверить:
   ```bash
   sdnctl nodes list
   ```

### 4.2. Создать VXLAN-сеть на нескольких узлах

```bash
sdnctl networks create prod \
  --type vxlan --vni 10100 --mtu 1450 \
  --node node_a --node node_b --node node_c

sdnctl networks apply prod
```

Контроллер опросит каждый узел, посчитает diff и создаст:
* мост `br-prod` с `external_ids={"owner":"sdn-controller","network_id":"net_prod"}`;
* VXLAN-порт на каждый remote-узел (полный mesh);
* DHCP/DNS/NAT/FW на «edge»-узле (первый по `node_ids`), если они
  заданы.

`sdnctl operations watch <op_id>` покажет шаги; `sdnctl drift scan`
после apply должен быть чистым.

### 4.3. Изменить сеть

PATCH-семантика: указанные поля заменяются, отсутствующие — нет.

```bash
# Через CLI пока есть только массовый assign-nodes — остальные поля
# через REST:
curl -H "Authorization: Bearer $SDN_TOKEN" \
     -H 'Content-Type: application/json' \
     -X PATCH "$SDN_CONTROLLER_URL/api/v1/networks/net_prod" \
     -d '{"mtu": 9000, "firewall_policy": {"default_action":"drop","rules":[]}}'
sdnctl networks apply prod
```

Каждое изменение бьёт `intent_version` и `spec_hash` — это видно в
`sdnctl networks show`.

### 4.4. Добавить subnet с DHCP и DNS

```bash
curl -H "Authorization: Bearer $SDN_TOKEN" \
     -H 'Content-Type: application/json' \
     -X POST "$SDN_CONTROLLER_URL/api/v1/networks/net_prod/subnet" \
     -d '{
       "cidr": "10.100.0.0/24",
       "gateway": "10.100.0.1",
       "dns_servers": ["10.100.0.2"],
       "allocation_pools": [{"start":"10.100.0.10","end":"10.100.0.250"}],
       "dhcp": {"range_start":"10.100.0.50","range_end":"10.100.0.200","domain_name":"prod.lan"},
       "dns_zone": "prod.lan"
     }'

sdnctl networks apply prod
```

### 4.5. Выдать/освободить IP

```bash
curl -H "Authorization: Bearer $SDN_TOKEN" \
     -H 'Content-Type: application/json' \
     -X POST "$SDN_CONTROLLER_URL/api/v1/subnets/<subnet_id>/allocations" \
     -d '{"kind":"dynamic","owner":{"type":"vm","id":"vm-42"}}'

# Пин конкретного адреса
curl ... -d '{"kind":"reservation","ip_address":"10.100.0.5",
              "owner":{"type":"vm","id":"vm-42"}}'

# Освободить
curl -X DELETE ".../api/v1/allocations/<allocation_id>"
```

### 4.6. Создание service account и токена

(нужно право `service_account:write` + `service_token:write` — у
admin'а есть всё).

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
     -H 'Content-Type: application/json' \
     -X POST "$SDN_CONTROLLER_URL/api/v1/service-accounts" \
     -d '{"name":"ops-ci","role":"automation"}'

curl ... -X POST ".../api/v1/service-accounts/<sa_id>/tokens" \
        -d '{"ttl_seconds":2592000,"label":"ops-ci pipeline"}'
```

`plaintext` в ответе **показывается ровно один раз** — сохраняйте
сразу. Отозвать токен:

```bash
curl -X POST ".../api/v1/service-tokens/<tok_id>/revoke"
```

Заблокировать аккаунт целиком (отрубает все его токены):

```bash
curl -X POST ".../api/v1/service-accounts/<sa_id>/disable"
```

### 4.7. Восстановление после катастрофы

1. Поднять чистую БД (`sdn_controller.db` или Postgres со схемой 0001-0008).
2. Импортировать снапшот:
   ```bash
   sdnctl backup import /var/backups/sdn-2026-05-19.json
   ```
3. Поднять контроллер с включённым auth, выпустить bootstrap-admin
   токен заново (см. deployment.md).
4. Для каждого узла: либо переподключить агент с тем же
   `tls_thumbprint`, либо выпустить ему свежий enrollment-токен и
   заэнрольнить заново.

> Bundle намеренно **не** включает plaintext'ы enrollment/service-токенов —
> после restore их нужно выпустить заново. Только хэши не помогают.

## 5. RBAC: роли и права

Доступные роли:

| Роль                | Что разрешено                                                                                              |
|---------------------|------------------------------------------------------------------------------------------------------------|
| `admin`             | Всё. Управляет service account'ами и токенами, удаляет узлы, делает backup/restore и restore снапшотов.    |
| `network_operator`  | Управляет сетями + IPAM + apply; читает топологию/drift/operations. Не управляет учётками, не удаляет узлы. |
| `automation`        | Идентично `network_operator` — для CI/инфра-пайплайнов.                                                    |
| `viewer`            | Только чтение (networks, nodes, ipam, topology, drift, operations). Audit и service-токены не видит.       |

Атомарные права (`<ресурс>:<действие>`): `network:read|write|apply`,
`node:read|write|admin`, `ipam:read|write`, `operation:read`,
`topology:read`, `drift:read`, `service_account:read|write`,
`service_token:read|write`, `audit:read`, `backup:export|import`,
`snapshot:read|write`.

Любая попытка действия без права → `403 Forbidden` с
`code=forbidden`.

## 6. REST API и OpenAPI

* OpenAPI-схема: `GET /api/v1/openapi.json`.
* Swagger UI: `GET /docs`.
* ReDoc: `GET /redoc`.

Все мутирующие эндпоинты возвращают `OperationEnvelope`
(`{operation_id, status, resource, links}`), кроме узко-специфичных
(allocation create отдаёт сам ресурс). `links.self` ведёт к
`/api/v1/operations/{id}`, `links.events` — к стриму событий
операции.

Стандартный envelope ошибок:

```json
{"error": {"code": "validation_error", "message": "...", "details": {}}}
```

## 7. Observability: метрики, логи, аудит

* **Prometheus**: `GET /metrics` (без auth, по стандарту). Метрики:
  * `sdn_http_requests_total{method,path,status}` — счётчик запросов;
  * `sdn_http_request_duration_seconds{method,path}` — гистограмма
    (бакеты 5 мс — 10 с);
  * `sdn_auth_failures_total{reason}` — 401/403 raw-count.
* **Логи**: structlog в JSON. В каждой записи `request_id`,
  `http_method`, `http_path`, при наличии — `operation_id`,
  `node_id`, `network_id`.
* **Аудит**: `GET /api/v1/audit-events` (admin). Лента
  append-only — оператор не может удалить запись. Фильтры:
  `actor`, `action`, `resource_type`, `resource_id`, `since`, `limit`.

## 8. Troubleshooting

| Симптом                                              | Причина и решение                                                                                                                            |
|------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------|
| `401 unauthorized` на каждой команде                 | Токен не задан, истёк или отозван. Получите новый через admin'а.                                                                             |
| `403 forbidden`, ясно вижу право в плане             | Принципалу не хватает permission'а. `sdnctl audit list --actor <вы>` покажет последнее действие; роль смотрите в service account.            |
| `apply` уходит в `failed`, в events видно `apply_failed` | Агент вернул ошибку на шаге. Откройте `sdnctl operations show <op_id>` — в `events.payload.failed_steps` будут детали (action + message).    |
| `apply` `verify_failed`                              | После apply diff всё ещё не пуст. Проверьте `sdnctl drift scan` — обычно бывает, когда между observe и verify состояние реально не сошлось. |
| Узел `stale`/`offline`                               | Агент не шлёт heartbeat. Проверьте на самом узле: `systemctl status netos-agent` и сетевую связность.                                        |
| `409 conflict` при создании сети                     | Имя занято. `sdnctl networks list` найдёт коллизию.                                                                                          |
| Бекап-импорт отдал `409`                             | БД не пустая. Это by design — мердж пока не поддерживается. Импортируйте в свежую БД.                                                        |
| `drift scan` показывает `stale_nodes`                | На этих узлах ни разу не было `apply`. Сделайте `sdnctl networks apply <name>` хотя бы один раз.                                             |
