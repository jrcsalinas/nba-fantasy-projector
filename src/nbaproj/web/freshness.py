"""How fresh the data is, read from the refresh log, for the banner."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session

from nbaproj.db.models import JobLock, RefreshLog
from nbaproj.db.refresh_log import (
    STATUS_FAILED,
    STATUS_REJECTED,
    STATUS_RUNNING,
    STATUS_SKIPPED,
    STATUS_SUCCEEDED,
)
from nbaproj.ingestion.refresh import LOCK_NAME

# No successful check for this long and the banner warns that the data may be out of date.
STALE_AFTER = timedelta(hours=24)

NEVER, OK, STALE, FAILED, RUNNING = "never", "ok", "stale", "failed", "running"
# A finished run of these kinds looked at the scoreboard, so it counts as a check.
CHECK_STATUSES = (STATUS_SUCCEEDED, STATUS_SKIPPED)


@dataclass(frozen=True)
class Freshness:
    """What the banner says: a state, and the times that go with it.

    A failed run's message is deliberately not here: it can hold SQL, a host name or a URL,
    so it stays in the log and the refresh log table.
    """

    state: str
    last_checked: datetime | None = None
    last_loaded: datetime | None = None
    failed_at: datetime | None = None


def _latest_finish(session: Session, *statuses: str) -> datetime | None:
    return session.scalar(
        select(func.max(RefreshLog.finished_at)).where(RefreshLog.status.in_(statuses))
    )


def _latest_run(session: Session, now: datetime) -> RefreshLog | None:
    """The newest run that says something: not rejected, and not one that is gone.

    A run that is still going holds the refresh lock (long jobs renew it). A ``running``
    row with no live lock belongs to a process that died; the log only marks it abandoned
    when the next run starts, so it must not count as running until then.
    """
    live_lock = exists().where(JobLock.name == LOCK_NAME, JobLock.expires_at > now)
    gone = and_(RefreshLog.status == STATUS_RUNNING, ~live_lock)
    return session.scalars(
        select(RefreshLog)
        .where(RefreshLog.status != STATUS_REJECTED, ~gone)
        .order_by(RefreshLog.started_at.desc(), RefreshLog.id.desc())
        .limit(1)
    ).first()


def load_freshness(session: Session, now: datetime) -> Freshness:
    """Read the refresh log and say how fresh the data is as of ``now``."""
    latest = _latest_run(session, now)
    if latest is None:
        return Freshness(NEVER)
    checked = _latest_finish(session, *CHECK_STATUSES)
    loaded = _latest_finish(session, STATUS_SUCCEEDED)
    if latest.status == STATUS_RUNNING:
        return Freshness(RUNNING, checked, loaded)
    if latest.status == STATUS_FAILED:
        return Freshness(FAILED, checked, loaded, latest.finished_at)
    stale = checked is None or now - checked > STALE_AFTER
    return Freshness(STALE if stale else OK, checked, loaded)
