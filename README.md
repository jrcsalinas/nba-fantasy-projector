# NBA Fantasy Projector

Projections, draft rankings, and lineup advice for NBA Fantasy.

## What it does

A FastAPI backend that helps fantasy basketball players make decisions: player projections,
draft rankings, weekly start/sit advice, and waiver/trade analysis. Projections are estimates,
not guarantees. The project is in early development; only a hello-world endpoint exists today.

## Quick start

```powershell
uv sync
uv run uvicorn nba_projector.main:app --reload
```

Then open <http://127.0.0.1:8000/docs> or call `GET /api/v1/hello`.

## Configuration

No environment variables are required yet.

## Usage

```powershell
curl http://127.0.0.1:8000/api/v1/hello
# {"message":"Hello, NBA Fantasy!"}
```

## Project structure

```
src/nba_projector/
  main.py      # FastAPI app entry point
  api/         # routers and schemas
```

## Development

```powershell
uv run ruff check
uv run ruff format --check
```

## Contributing

Branch as `<type>/<short-description>` and use Conventional Commits. Make sure lint passes
before opening a PR.

## License

TBD.
