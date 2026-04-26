# Screenshots Module Refactor — Design

**Date:** 2026-04-26
**Type:** Refactor (no behavior change, no schema change)
**Scope:** Carve a dedicated `backend/screenshots/` module out of code currently scattered across `sources/`, `tasks/executor.py`, `api/settings.py`, and `ai/context_builder.py`.

## Motivation

Screenshot-related code is currently scattered across four locations:

| Current location | Concern |
|---|---|
| `backend/sources/chart_shot_client.py` | HTTP transport to ChartShot service |
| `backend/tasks/executor.py:322-380` | Capture → push → persist orchestration |
| `backend/api/settings.py:55-99` | Status / cookies CRUD / cookies test routes |
| `backend/ai/context_builder.py:26-28` | Lookup screenshot by `signal_id` |

`executor.py` is becoming a kitchen sink (screeners + analysis + push + screenshots + AI orchestration). `sources/` should hold *data sources* (pine_screener, exchanges) — ChartShot is a side-effect tool, not a data source. Routes for ChartShot are currently nested under `/settings/`, which conflates "system settings" with "screenshot service management."

YAGNI: this refactor does **not** add features (no new manual-trigger UI, no gallery, no retention). Those are deferred until the structural foundation exists.

## Module Layout

```
backend/screenshots/
├── __init__.py          # Re-exports public API
├── client.py            # ChartShotClient (HTTP transport, singleton chartshot_client)
└── pipeline.py          # capture_and_dispatch + get_screenshot_for_signal

backend/api/
└── screenshots.py       # 4 FastAPI routes (status, cookies CRUD, cookies test)
```

## Public API

```python
# backend/screenshots/__init__.py
from screenshots.client import chartshot_client, ChartShotClient
from screenshots.pipeline import capture_and_dispatch, get_screenshot_for_signal
```

```python
# backend/screenshots/pipeline.py
def capture_and_dispatch(task_id, symbol, timeframes, channel) -> None:
    """Run full screenshot pipeline: ChartShot HTTP request → send_photo to channel
    → insert row in screenshots table linked to latest matching signal_id.

    Silent on failure (logs via app_logger). Used by tasks/executor.py.
    """

def get_screenshot_for_signal(signal_id: int) -> str | None:
    """Return absolute file_path of the most recent screenshot for this signal,
    or None. Used by ai/context_builder.py.
    """
```

```python
# backend/screenshots/client.py
class ChartShotClient:
    # Methods unchanged from current sources/chart_shot_client.py:
    # health(), screenshot(symbol, timeframes), screenshot_url(filename),
    # get_cookies(), update_cookies(raw), test_cookies()

chartshot_client = ChartShotClient()  # module singleton
```

## URL Path Migration

| Old (under `/settings/`) | New (under `/screenshots/`) |
|---|---|
| `GET /api/settings/chartshot/status` | `GET /api/screenshots/status` |
| `GET /api/settings/chartshot/cookies` | `GET /api/screenshots/cookies` |
| `PUT /api/settings/chartshot/cookies` | `PUT /api/screenshots/cookies` |
| `POST /api/settings/chartshot/cookies/test` | `POST /api/screenshots/cookies/test` |

Frontend `client.js` updates 4 lines accordingly. No backwards compatibility shim — this is internal API used only by our own frontend.

## Files Modified

| File | Change |
|---|---|
| `backend/sources/chart_shot_client.py` | Delete (moved to `screenshots/client.py`) |
| `backend/screenshots/__init__.py` | **NEW** — re-exports |
| `backend/screenshots/client.py` | **NEW** — copy of old `chart_shot_client.py` |
| `backend/screenshots/pipeline.py` | **NEW** — extracts `_take_and_send_screenshot`, `_parse_tf_from_filename`, `_record_screenshot`, plus new `get_screenshot_for_signal` |
| `backend/api/screenshots.py` | **NEW** — 4 routes lifted from `api/settings.py` |
| `backend/api/__init__.py` | Register `screenshots_router` |
| `backend/api/settings.py` | Remove the 4 `/chartshot/*` routes and the `chartshot_client` import |
| `backend/api/tasks.py` | Update `chartshot_client` import path |
| `backend/tasks/executor.py` | Drop the 3 helper functions; replace with `from screenshots import capture_and_dispatch` |
| `backend/ai/context_builder.py` | Replace inline `SELECT FROM screenshots WHERE signal_id=?` with `get_screenshot_for_signal(signal_id)` |
| `backend/tests/test_chart_shot_client.py` | Rename → `test_screenshots_client.py`; update import to `screenshots.client` |
| `frontend/src/api/client.js` | Update 4 URL paths from `/settings/chartshot/*` to `/screenshots/*` |

## Non-Goals

- No DB schema change (`screenshots` table stays as-is).
- No `docker-compose.yml` change (the volume sharing was already done in P0).
- No new functionality (manual trigger UI, gallery, retention) — separate features for later.
- No change to ChartShot service itself (`services/chartshot/`).

## Verification

- `pytest tests/test_screenshots_client.py tests/test_config.py tests/test_database.py` — should pass.
- Smoke test: import the new module, confirm `capture_and_dispatch` and `get_screenshot_for_signal` are reachable.
- Manual: `python -c "from main import app"` to ensure router registration is clean.
- The 31 pre-existing async test failures (pytest-asyncio config) are out of scope.

## Risks

- **Risk:** A hidden caller of `sources.chart_shot_client` outside the listed files.
  **Mitigation:** Grep before removing the old file; fail-fast at import time if any import is missed.
- **Risk:** Frontend build picks up cached old paths.
  **Mitigation:** Vite dev server reloads on save; production build picks up at next `npm run build`.
