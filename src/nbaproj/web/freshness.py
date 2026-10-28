"""How fresh the data is, read from the refresh log, for the banner."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from nbaproj.db.models import RefreshLog
from nbaproj.db.refresh_log import STATUS_RUNNING
from nbaproj.ingestion.runs import ABANDONED_AFTER

# No successful check for this long and the banner warns that the data may be out of date.
STALE_AFTER = timedelta(hours=24)
MESSAGE_LIMIT = 200

NEVER, OK, STALE, FAILED, RUNNING = "never", "ok", "stale", "failed", "running"
# A finished run of these kinds looked at the scoreboard, so it counts as a check.
CHECK_STATUSES = ("succeeded", "skipped")


@dataclass(frozen=True)
class Freshness:
    """What the banner says: a state, and the times and message that go with it."""

    state: str
    last_checked: datetime | None = None
    last_loaded: datetime | None = None
    failed_at: datetime | None = None
    message: str | None = None


def _latest_finish(session: Session, *statuses: str) -> datetime | None:
    return session.scalar(
        select(func.max(RefreshLog.finished_at)).where(RefreshLog.status.in_(statuses))
    )


def _latest_run(session: Session, now: datetime) -> RefreshLog | None:
    """The newest run that says something: not rejected, and not one that died mid-run."""
    died = and_(
        RefreshLog.status == STATUS_RUNNING, RefreshLog.started_at <= now - ABANDONED_AFTER
    )
    return session.scalars(
        select(RefreshLog)
        .where(RefreshLog.status != "rejected", ~died)
        .order_by(RefreshLog.started_at.desc(), RefreshLog.id.desc())
        .limit(1)
    ).first()


def load_freshness(session: Session, now: datetime) -> Freshness:
    """Read the refresh log and say how fresh the data is as of ``now``."""
    latest = _latest_run(session, now)
    if latest is None:
        return Freshness(NEVER)
    checked = _latest_finish(session, *CHECK_STATUSES)
    loaded = _latest_finish(session, "succeeded")
    if latest.status == STATUS_RUNNING:
        return Freshness(RUNNING, checked, loaded)
    if latest.status == "failed":
        message = (latest.message or "")[:MESSAGE_LIMIT]
        return Freshness(FAILED, checked, loaded, latest.finished_at, message)
    stale = checked is None or now - checked > STALE_AFTER
    return Freshness(STALE if stale else OK, checked, loaded)
