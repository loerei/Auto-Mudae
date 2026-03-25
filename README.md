# Auto Mudae

Forked from GDiazFentanes.

This repository contains a Windows-first Mudae automation workspace with the main roll bot plus Ouro side modes. The public repo is `.env`-first: sensitive account data stays in local files that are not tracked.

## Quick Start

1. Run `setup.bat` to create or reuse `.venv` and install `config/requirements.txt`.
2. Make sure `node` and `npm` are installed locally so the WebUI can build.
3. Copy `.env.example` to `.env`.
4. Fill in `CHANNEL_ID`, `SERVER_ID`, and `TOKENS_JSON` with your own values.
5. If you use Ouro Harvest, copy `config/oh_config.example.json` to `config/oh_config.json` and adjust it locally.

## Run

- Supported launcher: `run_webui.bat`
- `setup.bat` now routes to the same WebUI flow after environment setup.
- The WebUI serves on `http://127.0.0.1:8765/` by default and provides `Overview`, `Accounts`, `Wishlist`, `Logs`, and `Settings`.
- Standalone `OH`, `OC`, and `OQ` auto modes are controlled from the WebUI per account.
- Legacy CLI entrypoints under `src/mudae/cli/` and the interactive `OC/OQ` helpers under `src/mudae/ouro/` are no longer part of the supported product surface.

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
