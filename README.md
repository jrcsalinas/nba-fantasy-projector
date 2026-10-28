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
| `REFRESH_TOKEN` | no | At least 32 random characters (make one the same way as `SESSION_SECRET`); unset or blank means no token. When set, the scheduled job below can call `POST /refresh` with `Authorization: Bearer <token>`. Without it only the Refresh now button works. |

Start it locally in PowerShell (the variables last only for that window):

```powershell
$env:SESSION_SECRET = "<the long random value>"
$env:PASSWORD_HASH = '<the hash from hash-password, in single quotes because it contains $>'
$env:COOKIE_SECURE = "false"          # plain http://localhost
python -m uvicorn nbaproj.web.app:create_app_from_env --factory --port 8000
```

Then open http://localhost:8000. Five wrong passwords lock an address out for 15
minutes. Failed logins, lockouts and refused refresh tokens are logged as warnings (the
address only, never a password or token).

Limits worth knowing before you put it on the internet:

- **Signing out does not revoke a stolen cookie.** The session is a signed cookie valid for
  7 days; logging out clears your browser's copy, and the server keeps no list of sessions.
  To end every session at once (a lost phone), change `SESSION_SECRET` and restart.
- **The lockout is per address, in memory.** A restart clears it, and with several worker
  processes each has its own count. Behind a proxy, start uvicorn with `--proxy-headers`
  and set `--forwarded-allow-ips` to the proxy's address only (not `*`, unless the proxy
  overwrites `X-Forwarded-For`); otherwise every visitor looks like the proxy and one
  attacker can lock you out, or a visitor can pretend to be any address and skip the lockout.

The database needs its tables first: run `python -m alembic upgrade head` once (and
again after an update) with the same `DATABASE_URL`.

**My roster** (`/roster`, linked from the home page): type a name to search, tap Add to
put a player on your roster, tap Remove (it asks first) to take one off, and open "All
changes" for the history of every add and remove. Search and edits update in place with
a small bundled script (htmx). The search box needs JavaScript; the Add and Remove
buttons also work as plain form posts that reload the page.

## Refreshing the data

Every signed-in page has a banner at the top: when the data was last checked, "Refreshing
data..." while a run is going, "The last refresh failed at ..." if the latest run failed
(the data shown is from the last good check; the reason is not shown on the page, because
it can contain internals, and is in the server log and the `refresh_log` table), and "Data
may be out of date" after 24 hours without a check. Tap **Refresh now** to start a run; the banner updates by itself when it
ends. `POST /refresh` answers at once and runs in the background:

| Answer | Meaning |
|---|---|
| `202 {"status": "started"}` | A run began. |
| `200 {"status": "recent"}` | A run started, finished or found nothing new less than 10 minutes ago, so nothing new began. A failed run never blocks a retry. |
| `409 {"status": "running"}` | A refresh (this app's or one run from a laptop) holds the shared lock. |
| `401` | A wrong or missing bearer token (or `REFRESH_TOKEN` is not set). |

### Scheduled refresh

The host sleeps when idle, so a GitHub Actions job wakes it and asks for a refresh a few
times each night. Add two repository secrets (Settings, Secrets and variables, Actions):
`APP_URL` (for example `https://your-app.onrender.com`, no trailing slash) and
`REFRESH_TOKEN` (the same value as on the host). Then add this file as
`.github/workflows/refresh.yml`:

```yaml
name: Refresh NBA data
on:
  schedule:
    - cron: "30 4,6,9,14 * * *"   # UTC; late West Coast games end about 05:30 UTC
  workflow_dispatch:
jobs:
  refresh:
    runs-on: ubuntu-latest
    steps:
      - name: Ask the app to refresh
        env:
          APP_URL: ${{ secrets.APP_URL }}
          REFRESH_TOKEN: ${{ secrets.REFRESH_TOKEN }}
        run: |
          # Retries cover a cold start (the host, then the database).
          for attempt in 1 2 3 4 5 6; do
            code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 90 -X POST \
              -H "Authorization: Bearer $REFRESH_TOKEN" "$APP_URL/refresh" || true)
            echo "attempt $attempt: HTTP $code"
            case "$code" in
              200|202|409) exit 0 ;;
              401) echo "the token was refused" >&2; exit 1 ;;
            esac
            sleep $((attempt * 30))
          done
          echo "the app did not accept the refresh" >&2
          exit 1
```

The job turns red (and GitHub emails you) only when the app never accepts the request. It
cannot see whether the refresh itself then succeeds, because that runs in the background
after the answer: a failed refresh shows in the banner, and "may be out of date" appears
after 24 hours without a good check. GitHub can pause scheduled workflows in a repository
with no activity for 60 days; that banner warning is how you would notice.

## Scope

- In scope: projections, draft rankings, lineup and waiver/trade analysis
- Deferred: Yahoo OAuth sign-in and league connection (pending Yahoo API approval)
