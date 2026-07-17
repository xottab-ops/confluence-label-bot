# confluence-label-bot

Бот-демон для Confluence **Server / Data Center**. Периодически находит страницы
с заданным лейблом внутри поддерева одной страницы и переносит их под другую страницу.

## Как работает

1. Через CQL ищет страницы: `space=… and type=page and label=… and ancestor=SOURCE_PAGE_ID`.
   Оператор `ancestor` охватывает **всё поддерево** источника на любой глубине.
2. Каждую найденную страницу переносит под `TARGET_PAGE_ID` — сменой родителя
   (`PUT /rest/api/content/{id}` с новым `ancestors` и версией +1). Дочерние
   страницы переносятся вместе с ней.
3. Повторяет цикл каждые `POLL_INTERVAL_SECONDS` секунд.

Операция идемпотентна: после переноса страница выходит из поддерева источника и
на следующих проходах уже не выбирается.

## Настройка

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env   # затем заполнить .env
```

Ключевые переменные `.env`:

| Переменная | Назначение |
|---|---|
| `CONFLUENCE_BASE_URL` | URL инсталляции, без завершающего `/` |
| `CONFLUENCE_PAT` | Personal Access Token (рекомендуется) |
| `CONFLUENCE_USERNAME` / `CONFLUENCE_PASSWORD` | Basic-авторизация, если нет PAT |
| `CONFLUENCE_VERIFY_SSL` | Проверять SSL (`false` для самоподписанных сертификатов) |
| `CONFLUENCE_CA_CERT_DIR` | Папка с корневыми (Root/CA) сертификатами для доверенной проверки TLS |
| `CONFLUENCE_SPACE_KEY` | Ключ пространства |
| `SOURCE_PAGE_ID` | ID страницы-источника (откуда переносим поддерево) |
| `TARGET_PAGE_ID` | ID страницы-назначения (куда переносим) |
| `MOVE_LABEL` | Лейбл-триггер |
| `POLL_INTERVAL_SECONDS` | Интервал проверки |
| `DRY_RUN` | `true` — только логировать, ничего не менять |

> ID страницы виден в URL (`…/pages/viewpage.action?pageId=12345`) либо в
> «Page Information» → «Page ID».

### Корневые сертификаты

Если Confluence подписан внутренним корневым CA, положите его сертификаты
(`.pem` / `.crt` / `.cer` / `.der`) в папку (по умолчанию `certs/`, уже в
`.gitignore`) и укажите её в `CONFLUENCE_CA_CERT_DIR`. При старте все сертификаты
из папки собираются в единый бандл (вместе с `certifi`) и используются для
проверки TLS — отключать `CONFLUENCE_VERIFY_SSL` не нужно.

## Запуск

Активируйте venv (`.venv\Scripts\activate` / `source .venv/bin/activate`), затем:

```bash
python -m confluence_label_bot --check   # проверить доступ и выйти
python -m confluence_label_bot --once    # один проход и выйти
python -m confluence_label_bot           # демон (по интервалу)
```

Начните с `DRY_RUN=true` и `--once`, чтобы увидеть, какие страницы будут перенесены,
без внесения изменений.

## Запуск как службы (systemd, пример)

```ini
[Unit]
Description=Confluence label mover bot
After=network-online.target

[Service]
WorkingDirectory=/opt/confluence-label-bot
ExecStart=/opt/confluence-label-bot/.venv/bin/python -m confluence_label_bot
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```