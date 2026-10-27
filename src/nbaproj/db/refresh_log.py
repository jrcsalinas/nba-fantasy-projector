"""Recording of refresh runs."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from nbaproj.db.models import RefreshLog

STATUS_RUNNING = "running"
FINAL_STATUSES = frozenset({"succeeded", "failed", "skipped", "rejected"})
TRIGGERS = frozenset({"cron", "ui", "cli"})


@dataclass(frozen=True)
class RefreshOutcome:
    """How a refresh run ended."""

    status: str
    message: str | None = None
    rows_upserted: int = 0

    def __post_init__(self) -> None:
        if self.status not in FINAL_STATUSES:
            raise ValueError(
                f"unknown refresh status {self.status!r}; expected one of {sorted(FINAL_STATUSES)}"
            )


def start_refresh(session: Session, trigger: str, now: datetime) -> RefreshLog:
    """Record the start of a refresh run and return its log row."""
    if trigger not in TRIGGERS:
        raise ValueError(f"unknown refresh trigger {trigger!r}; expected one of {sorted(TRIGGERS)}")
    log = RefreshLog(trigger=trigger, started_at=now, status=STATUS_RUNNING, rows_upserted=0)
    session.add(log)
    session.commit()
    return log


def finish_refresh(
    session: Session, log: RefreshLog, outcome: RefreshOutcome, now: datetime
) -> RefreshLog:
    """Record the outcome of a refresh run."""
    log.status = outcome.status
    log.message = outcome.message
    log.rows_upserted = outcome.rows_upserted
    log.finished_at = now
    session.commit()
    return log
