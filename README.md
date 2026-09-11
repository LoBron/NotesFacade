# Notes Facade

Notes Facade — локальный MCP-сервис для контролируемой работы AI-агентов с заметками
Obsidian. Фасад принимает непрозрачный `project_id`, не раскрывает список проектов и не
даёт прямого доступа к файловой системе. Публичных MCP tools ровно семь: `capture`,
`find`, `read_note`, `patch`, `move_note`, `review_queue`, `validate`. Delete tool
намеренно отсутствует.

Система запускает Obsidian и Notes Facade в Docker. Все опубликованные порты привязаны
к `127.0.0.1`.

## Быстрый запуск из чистого клона

Поддерживаются Ubuntu, Debian и WSL2 на их основе.

```bash
git clone <URL_РЕПОЗИТОРИЯ> NotesFacade
cd NotesFacade
./scripts/bootstrap.sh
```

Также доступна обёртка:

```bash
make bootstrap
```

Git нужен для клонирования. Скрипт проверяет Bash, `curl`, `sha256sum`, Docker Engine,
Docker Compose v2 и доступ к daemon. Если Docker/Compose, `curl` или `coreutils`
отсутствуют, скрипт сначала запрашивает подтверждение. Только после согласия он
использует `sudo apt`: Docker устанавливается из официального репозитория Docker для
текущей Ubuntu/Debian. При отказе системные пакеты не меняются.

### Вопросы bootstrap

При первом запуске скрипт спрашивает:

1. имя единственного начального проекта и slug (по умолчанию `personal`);
2. абсолютный путь пользовательского vault (по умолчанию
   `$HOME/Vaults/<slug>`);
3. timezone;
4. UID/GID (по умолчанию текущий пользователь);
5. пароль Obsidian UI — скрыто, с повторным подтверждением;
6. подтверждение несекретного резюме перед созданием файлов, сменой владельца и
   запуском контейнеров.

Slug должен соответствовать `[a-z0-9][a-z0-9_-]{0,62}`, путь должен быть абсолютным,
UID/GID — неотрицательными числами, timezone — существовать в `/usr/share/zoneinfo`,
пароль — содержать 8–128 символов.

Автоматически генерируются:

- UUID `project_id`;
- 256-битный `OBSIDIAN_API_KEY`;
- случайный id записи vault в глобальном реестре Obsidian.

API key и пароль не выводятся в терминал.

## Что настраивается автоматически

Bootstrap без открытия UI:

- атомарно создаёт `.env` с режимом `600`;
- создаёт `config/projects.json`, `vault-root`, пользовательский vault и каталоги
  `.obsidian`;
- устанавливает Local REST API with MCP строго версии `5.1.0` из официального GitHub
  release;
- скачивает `main.js`, `manifest.json`, `styles.css` и проверяет закреплённые SHA-256:
  `c3bf3e…ce131`, `6c0d83…f8437`, `a8b5c5…0426f8`;
- включает community plugin и HTTP REST на `0.0.0.0:27123` внутри Docker-сети;
- создаёт plugin `data.json` с API key без поля `crypto`; сертификат создаёт сам plugin
  при `onload`;
- задаёт `trashOption: none` и создаёт актуальный object `core-plugins.json` с явными
  `"file-recovery": true` и `"sync": false`;
- проверяет фактических владельцев внутри `vault-root` и выбранного project vault.
  После общего подтверждения bootstrap исправляет через `chown` только записи с
  несовпадающими PUID:PGID, не следует по symlink и не переходит на другие файловые
  системы;
- через `/custom-cont-init.d/10-seed-vault.sh` добавляет `/vault` в глобальный реестр
  Obsidian до desktop startup. Другие существующие записи vault не удаляются;
- включает community plugin через Electron DevTools, доступный только на
  `127.0.0.1:9222` внутри контейнера и не опубликованный на хост;
- запускает сначала Obsidian и ждёт authenticated REST на `127.0.0.1:27123` внутри
  контейнера, затем собирает и запускает facade.

Закреплённый образ Obsidian:

```text
lscr.io/linuxserver/obsidian:v1.13.7-ls144
```

Физическое размещение данных:

- пользовательские заметки: значение `PROJECT_VAULT_PATH`;
- vault-настройки и plugin: `vault-root/.obsidian/`;
- реестр проектов: `config/projects.json`;
- desktop-конфигурация Obsidian: named volume `<compose-project>_obsidian-config`;
- системные шаблоны: `vault-system/` (read-only в контейнерах).

## Обязательная проверка готовности

Bootstrap завершается успешно только если:

- сервисы `obsidian` и `facade` находятся в состоянии running;
- `GET /health` содержит JSON `status: "ok"` (HTTP 200 с `degraded` не принимается);
- Obsidian REST отвечает с Bearer-аутентификацией из Docker-сети;
- реальный FastMCP-клиент внутри facade выполняет MCP initialize и `tools/list`;
- набор tools в точности равен семи ожидаемым именам;
- `validate(project_id)` возвращает ноль errors.

Повторная read-only проверка:

```bash
make check-runtime
# или
./scripts/bootstrap.sh --check-runtime
```

Она не запускает контейнеры и не меняет конфиги или заметки.
Для legacy `.env` отсутствующие bootstrap-переменные подставляются только в памяти и
не записываются в файл.

Успешный первый запуск печатает URL UI, логин `abc`, путь vault, `project_id`, MCP
endpoint и команды управления. Пароль и API key повторно не печатаются.

## Повторный запуск

```bash
./scripts/bootstrap.sh
```

Если `.env` уже существует, скрипт:

- не перегенерирует пароль, API key и существующий `project_id`;
- не затирает заметки, пользовательские конфиги или named volume;
- добавляет только отсутствующие bootstrap-переменные в `.env`;
- восстанавливает отсутствующие каталоги и официальные plugin assets;
- сохраняет корректный object `core-plugins.json` и прекращает работу без
  перезаписи, если существующий файл имеет другой формат или нарушает обязательные
  `file-recovery=true`/`sync=false`;
- исправляет ownership только внутри `vault-root` и `PROJECT_VAULT_PATH`, если
  фактический UID:GID отличается от выбранного PUID:PGID;
- повторно запускает проверки REST, health и MCP.

Перед восстановлением запрашивается подтверждение. Если существующий `app.json` или
plugin `data.json` несовместим с обязательными параметрами, bootstrap останавливается,
а не перезаписывает пользовательскую конфигурацию. Полного reset-режима нет.

Bootstrap никогда не выполняет `docker compose down -v`, не удаляет vault или volume и
не читает и не меняет `.cursor/mcp.json`.

Для установок, созданных до появления bootstrap, Compose сохраняет совместимые
значения по умолчанию: `$HOME/Vaults/personal`, folder `personal` и несекретный
валидный fallback id записи vault. Поэтому `docker compose config` работает до
миграции; при запуске bootstrap реальные значения генерируются и фиксируются в
`.env`. Сам bootstrap по-прежнему принимает только абсолютный `PROJECT_VAULT_PATH`.

## Подключение Cursor и других MCP-клиентов

Это отдельное пользовательское действие после bootstrap. Создайте или дополните
`.cursor/mcp.json`, не удаляя другие серверы:

```json
{
  "mcpServers": {
    "notes-facade": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

Если `FACADE_HOST_PORT` изменён в `.env`, используйте его вместо `8000`. Перезагрузите
окно Cursor и проверьте подключение и семь tools. Не помещайте API key или
`project_id` в `.cursor/mcp.json`.

Для другого локального MCP-клиента используется тот же endpoint. Инструкцию агента
можно создать из `skills/notes.skill.template.md`, заменив `{{PROJECT_ID}}` значением
из `config/projects.json`.

## Управление

```bash
make up                 # собрать/запустить оба сервиса
make down               # удалить контейнеры и сеть, но сохранить volume
make logs               # непрерывные логи
make check-runtime      # полная read-only runtime-проверка
docker compose ps
docker compose logs --tail=200 obsidian facade
docker compose restart facade
docker compose restart obsidian
```

Не используйте `docker compose down -v`: флаг `-v` удалит сохранённую desktop-
конфигурацию Obsidian.

### Смена пароля UI

Откройте `.env` локальным редактором, измените `KASM_PASSWORD`, сохраните режим файла
`600`, затем:

```bash
docker compose up -d --force-recreate obsidian
```

Логин остаётся `abc`. Не передавайте пароль аргументом командной строки.

### Ротация API key

Сгенерируйте новый 64-символьный hex key безопасным генератором. Одно и то же значение
нужно атомарно записать в `OBSIDIAN_API_KEY` файла `.env` и в поле `apiKey` файла
`vault-root/.obsidian/plugins/obsidian-local-rest-api/data.json`, не выводя его в
терминал. Затем:

```bash
docker compose up -d --force-recreate obsidian facade
make check-runtime
```

## Добавление проектов после ввода в эксплуатацию

Bootstrap создаёт ровно один начальный проект. Для дополнительного проекта:

1. создайте отдельный каталог на хосте и UUID (`make new-project` использует
   `/proc/sys/kernel/random/uuid`, с fallback на системный `uuidgen`, без Python);
2. добавьте запись `{id, name, folder}` в `config/projects.json`;
3. добавьте одинаковый bind mount в оба сервиса `docker-compose.yml`: RW для Obsidian
   и RO для facade;
4. пересоздайте сервисы и выполните `make check-runtime`.

Пример mounts для folder `work`:

```yaml
services:
  obsidian:
    volumes:
      - /absolute/path/to/work:/vault/work
  facade:
    volumes:
      - /absolute/path/to/work:/vault/work:ro
```

Передавайте новый `project_id` только агентам, которым нужен этот проект. Фасад не
предоставляет tool для списка проектов.

## Ручной fallback

Используйте его только если bootstrap не может завершиться в вашей среде:

1. скопируйте `.env.example` в `.env`, заполните абсолютные пути, UID/GID, timezone,
   UI-пароль и случайные значения API key/vault id; установите `chmod 600 .env`;
2. создайте `config/projects.json` по `config/projects.example.json` с новым UUID;
3. создайте `vault-root/.obsidian/plugins/obsidian-local-rest-api/`;
4. скачайте три assets официального release `5.1.0` и сверьте полные хеши из
   `scripts/bootstrap.sh`;
5. создайте конфиги plugin/app/core plugins с параметрами, описанными выше;
6. запустите `docker compose up -d obsidian`, дождитесь REST, затем
   `docker compose up -d --build facade`;
7. выполните `make check-runtime`.

UI на `http://127.0.0.1:3000` (HTTPS: `https://127.0.0.1:3001`) нужен только для
обычной работы или диагностики, а не для первичной настройки.

## Разработка и проверки

Проект использует Python 3.12 и Poetry:

```bash
source .venv/bin/activate
poetry install
docker compose config
make test
make lint
make typecheck
```

## Диагностика

### Timeout Obsidian REST

```bash
docker compose ps
docker compose logs --tail=200 obsidian
```

Проверьте, что plugin assets имеют версию `5.1.0`, `data.json` содержит
`enableInsecureServer: true`, `bindingHost: "0.0.0.0"`, `insecurePort: 27123`, а
API key совпадает с `.env`. Таймаут bootstrap можно временно увеличить:

```bash
BOOTSTRAP_TIMEOUT_SECONDS=480 ./scripts/bootstrap.sh
```

### Health возвращает degraded

HTTP 200 недостаточно. Посмотрите поле `reasons`:

```bash
curl --fail http://127.0.0.1:8000/health
docker compose logs --tail=200 facade obsidian
```

Частые причины: REST ещё не поднялся, API keys не совпадают или
`config/projects.json` невалиден.

### Obsidian не открывает `/vault`

Проверьте hook и глобальный реестр:

```bash
docker compose logs --tail=200 obsidian
docker compose exec obsidian jq . /config/.config/obsidian/obsidian.json
```

Hook сохраняет существующие vault entries и добавляет `/vault` только при его
отсутствии.

### Ошибки прав

```bash
stat -c '%U:%G %a %n' vault-root "$PROJECT_VAULT_PATH"
```

Исправление владельца является системным изменением. Выполняйте `chown` только после
проверки правильности пути и UID/GID.

## Резервное копирование и безопасность

Резервируйте:

- каталог из `PROJECT_VAULT_PATH`;
- `vault-root/.obsidian/`;
- `.env` и `config/projects.json`;
- named volume Obsidian, если важны desktop-настройки.

Остановите запись или обеспечьте согласованный snapshot перед копированием. Проверяйте
восстановление резервной копии в изолированном Compose project.

Не публикуйте `OBSIDIAN_API_KEY`, `KASM_PASSWORD`, crypto-материалы plugin и приватные
`project_id`. `.env`, `config/projects.json` и `vault-root/` исключены из Git. Порты UI,
HTTPS UI и MCP по умолчанию доступны только на `127.0.0.1`; не меняйте bind на
`0.0.0.0` без отдельной аутентификации, TLS и сетевых ограничений.
