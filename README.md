# assistant

Leo's personal assistant. Telegram front end over the Claude Agent SDK, with
SQLite state. Deployed to the T430 by the NixOS module in the `Nixconfig` repo,
which consumes this flake as an input.

Design and rationale live in `~/reports/assistant-scope.md`.

## Phase 0 (this scaffold)

Boots, answers a Telegram message via the Agent SDK, logs the exchange to SQLite.
No tools, no calendar, no mail yet — that's phase 1.

## Develop

```sh
nix develop            # python + httpx + claude-agent-sdk + pytest + ruff + claude CLI
pytest                 # stdlib-only tests (db, config)
ruff check .

# run locally against a real bot:
export TELEGRAM_BOT_TOKEN=...          # from @BotFather
export TELEGRAM_ALLOWED_CHAT_ID=...    # your own chat id (others ignored)
export CLAUDE_CODE_OAUTH_TOKEN=...     # or be logged in via the claude CLI
export ASSISTANT_DB=./assistant.db
python -m assistant
```

Secrets are read from `<NAME>_FILE` (a path) before `<NAME>`, so the systemd
service hands them in via `LoadCredential` without putting them in the env dump.

## Runtime requirements

- The `claude` CLI on PATH (the Agent SDK shells out to it).
- `CLAUDE_CODE_OAUTH_TOKEN` (or an interactive `claude` login).

## Deploy

Via `Nixconfig`: `modules/services/assistant.nix` runs
`inputs.assistant.packages.<system>.default`. Bump with
`nix flake update assistant` in Nixconfig, then `deploy .#T430`. For tight
iteration, point the input at this working tree:
`deploy .#T430 --override-input assistant path:$HOME/Repos/assistant`.
