# NotesFacade

MCP facade over Obsidian Local REST API.

## Prerequisites

- Docker and Docker Compose
- Poetry
- Existing local vault directory, for example: `~/Vaults/personal`

## Quick start

1. Copy `.env.example` to `.env` and fill the values.
2. Copy `config/projects.example.json` to `config/projects.json`.
3. Start the stack:

   ```bash
   make up
   ```

4. Check logs if needed:

   ```bash
   make logs
   ```

5. Stop the stack:

   ```bash
   make down
   ```

## One-time Obsidian setup

1. Open [https://localhost:3001](https://localhost:3001) in a browser.
2. Sign in with the KasmVNC password from `.env` (`KASM_PASSWORD`).
3. In Obsidian, open `/vault` as the vault.
4. Install the community plugin `Local REST API`.
5. In plugin settings, enable `HTTP server`.
6. In plugin settings, enable `Listen on all interfaces`.
7. Copy generated API key into `.env` as `OBSIDIAN_API_KEY`.
8. Enable Obsidian `File Recovery`.
9. Restart facade service:

   ```bash
   docker compose restart facade
   ```

## Adding a new project id

Generate a new opaque project id and get mount/config hints:

```bash
make new-project
```

## Local quality checks

```bash
make test
make lint
make typecheck
```

## Подключение агентов

### Cursor

Добавьте MCP-сервер в `mcp.json`:

```json
{
  "mcpServers": {
    "notes-facade": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

### OpenClaw

OpenClaw запускается на хосте, поэтому использует тот же endpoint MCP-фасада.
Добавьте аналогичную запись в конфиг MCP OpenClaw:

```json
{
  "mcpServers": {
    "notes-facade": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

### Создание скилла агента из шаблона

1. Скопируйте `skills/notes.skill.template.md` в рабочий файл скилла агента.
2. Замените плейсхолдер `{{PROJECT_ID}}` на реальный opaque id вашего проекта.
3. Не придумывайте `project_id`: используйте только значение из `config/projects.json`.