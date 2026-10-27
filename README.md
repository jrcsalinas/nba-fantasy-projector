# NBA Fantasy Projector

Projections, draft rankings, start/sit lineup advice, and waiver/trade analysis for NBA fantasy, built on `nba_api` data.

**Stack:** Python, FastAPI, SQLite, Jinja2 + HTMX

**Status:** Early development (Trinity SDLC: intent → architecture → develop → quality).

## Getting Started

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt   # added once the first dependencies land
```

## Scope

- In scope: projections, draft rankings, lineup and waiver/trade analysis
- Deferred: Yahoo OAuth sign-in and league connection (pending Yahoo API approval)
