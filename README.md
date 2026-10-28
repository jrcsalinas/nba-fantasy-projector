# NBA Fantasy Projector

Projections, draft rankings, start/sit lineup advice, and waiver/trade analysis for NBA fantasy, built on `nba_api` data.

**Stack:** Python, FastAPI, SQLite (Postgres in the cloud), Jinja2 + HTMX

**Status:** Early development (Trinity SDLC: intent → architecture → develop → quality).

## Getting Started

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip      # older pip releases have known vulnerabilities
pip install -r requirements-dev.txt   # runtime and test dependencies
$env:PYTHONPATH = "src"               # the package is not installed; do this in each new window
```

Create the database tables once, then load data from your laptop:

```powershell
python -m alembic upgrade head
python -m nbaproj.cli backfill --season 2025-26   # a past season (default: the last completed one)
python -m nbaproj.cli schedule                    # the current season's schedule
```

## Running the web app

The app has one login. It reads these environment variables (it does not read a `.env`
file; set them in your shell or in the host's settings, never in the repository):

| Variable | Required | What it is |
|---|---|---|
| `SESSION_SECRET` | yes | At least 32 random characters; signs the session cookie. Make one with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `PASSWORD_HASH` | yes | The argon2 hash of your password. Make it with `python -m nbaproj.cli hash-password` (it asks for the password twice and prints only the hash). |
| `COOKIE_SECURE` | no | `true` (the default) marks the cookie Secure, so browsers send it only over HTTPS. Set `false` for plain `http://localhost` development. |
| `OWNER_NAME` | no | Your name in the app; default `owner`. |
| `DATABASE_URL` | no | The database; default `sqlite:///nbaproj.db` in the folder you run from. |
| `APP_TIMEZONE` | no | An IANA timezone such as `America/New_York`; default `UTC`. |

Start it locally in PowerShell (the variables last only for that window):

```powershell
$env:SESSION_SECRET = "<the long random value>"
$env:PASSWORD_HASH = '<the hash from hash-password, in single quotes because it contains $>'
$env:COOKIE_SECURE = "false"          # plain http://localhost
python -m uvicorn nbaproj.web.app:create_app_from_env --factory --port 8000
```

Then open http://localhost:8000. Five wrong passwords lock an address out for 15
minutes (held in memory, so a restart clears it).

The database needs its tables first: run `python -m alembic upgrade head` once (and
again after an update) with the same `DATABASE_URL`.

**My roster** (`/roster`, linked from the home page): type a name to search, tap Add to
put a player on your roster, tap Remove (it asks first) to take one off, and open "All
changes" for the history of every add and remove. Search and edits update in place with
a small bundled script (htmx). The search box needs JavaScript; the Add and Remove
buttons also work as plain form posts that reload the page.

## Scope

- In scope: projections, draft rankings, lineup and waiver/trade analysis
- Deferred: Yahoo OAuth sign-in and league connection (pending Yahoo API approval)
