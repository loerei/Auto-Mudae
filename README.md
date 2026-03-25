# Auto Mudae

Forked from GDiazFentanes.

This repository contains a Windows-first Mudae automation workspace with the main roll bot plus Ouro side modes. The public repo is `.env`-first: sensitive account data stays in local files that are not tracked.

## Quick Start

1. Run `setup.bat` to create or reuse `.venv` and install `config/requirements.txt`.
2. Copy `.env.example` to `.env`.
3. Fill in `CHANNEL_ID`, `SERVER_ID`, and `TOKENS_JSON` with your own values.
4. If you use Ouro Harvest, copy `config/oh_config.example.json` to `config/oh_config.json` and adjust it locally.

## Run

- Main bot: `run_bot.bat`
- Ouro Harvest: `run_oh.bat`
- Ouro Chest: `run_oc.bat`
- Direct CLI entrypoints live under `src/mudae/cli/`

## Local-Only Runtime Files

These files are generated or maintained locally and are intentionally ignored in git:

- `config/bot_instances.json`
- `config/claim_stats.json`
- `config/last_seen.json`
- `config/oh_config.json`
- `config/oh_stats.json`
- `logs/*`
- `cache/*`

## Testing

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Docs

- `START_HERE.md`: historical wishlist integration walkthrough
- `docs/README.md`: documentation index
- `docs/oh_bot.md`: Ouro Harvest setup notes
- `docs/DEEP_FUNCTIONAL_REVIEW_REPORT.md`: sanitized deep review and validation notes
