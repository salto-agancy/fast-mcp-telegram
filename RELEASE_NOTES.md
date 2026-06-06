# Release v0.29.0

Send files as inline data URIs and local paths across all transport modes, with improved Telegram client identity and transcription reliability.

## New Features

- **`send_message` / `edit_message` data: URI uploads** — Attach files inline via `data:` URIs (base64) without hosting or filesystem access. Filenames are preserved from the URI. (#103, #104)
- **Local file paths in all transport modes** — `send_message` and `edit_message` now accept local file paths in HTTP/SSE transport modes (previously stdio-only). Files are validated against a size limit. (#106)
- **Forum topic date filtering** — `min_date`/`max_date` now work correctly with `reply_to_id` in forum topics, using `dialog.date` as a reliable fallback. (#102)

## Fixes

- **Transcription cooldown caching** — `TranscribeAudio` cooldown errors are now cached per peer, preventing silent failures when retrying during a cooldown window. (#112)
- **Image data: URI detection** — Fixed `data:` URI images being sent as documents instead of inline photos when a `filename=` parameter was present. (#105)
- **Telegram client identity** — Set `device_model` and `app_version` for the Telegram client connection; uses LRU-cached identity to avoid redundant setup. (#107, #108)
- **CI build** — Skip integration tests in GitHub Actions workflow to prevent failures from missing Telegram API credentials. (#110)

## Internal

- Centralised config in `ServerConfig` — removed `settings.py` shim, renamed `get_config()` to `cfg()`. (#111)
- Bumped starlette 1.0.0 → 1.0.1. (#109)
- Unified bench harness, smoke tests, .codegraph cleanup.

---

**Full Changelog**: https://github.com/leshchenko1979/fast-mcp-telegram/compare/0.28.0...v0.29.0
