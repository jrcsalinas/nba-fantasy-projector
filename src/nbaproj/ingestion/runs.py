"""Shared scaffolding for jobs that write under the refresh lock and log themselves."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Generic, TypeVar

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from nbaproj.db.locks import acquire_lock, release_lock
from nbaproj.db.refresh_log import RefreshOutcome, finish_refresh, start_refresh
from nbaproj.ingestion.client import NbaClientError

LOCK_NAME = "refresh"
LOCK_TTL = timedelta(minutes=10)
MESSAGE_LIMIT = 500
# Failures a job expects and reports as a ``failed`` run instead of raising.
EXPECTED_ERRORS = (NbaClientError, ValueError, KeyError, SQLAlchemyError)


@dataclass(frozen=True)
class RunResult:
    """How a locked run ended. Jobs extend this with their own counts."""

    status: str
    rows_upserted: int = 0
    message: str | None = None


R = TypeVar("R", bound=RunResult)


def utc_now() -> datetime:
    """The current time, timezone-aware UTC."""
    return datetime.now(timezone.utc)


def _clip(message: str | None) -> str | None:
    return None if message is None else message[:MESSAGE_LIMIT]


@dataclass(frozen=True)
class Job(Generic[R]):
    """A named job that takes the shared refresh lock and records itself in the log."""

    name: str
    result_type: type[R]
    ttl: timedelta = LOCK_TTL

    def run(
        self,
        session: Session,
        trigger: str,
        clock: Callable[[], datetime],
        work: Callable[[], R],
    ) -> R:
        """Run ``work`` under the lock and log the outcome.

        The lock holder is unique per run, so two runs of the same job exclude each
        other. If the lock is held the run is ``rejected``. If ``work`` raises
        unexpectedly the log row is marked ``failed`` and the exception propagates.
        """
        log = start_refresh(session, trigger, clock())
        holder = f"{self.name}-{uuid.uuid4().hex}"
        if not acquire_lock(session, LOCK_NAME, holder, self.ttl, clock()):
            result = self.result_type(
                status="rejected", message="another refresh holds the lock"
            )
            finish_refresh(session, log, RefreshOutcome("rejected", result.message), clock())
            return result
        try:
            result = work()
            outcome = RefreshOutcome(
                result.status, _clip(result.message), result.rows_upserted
            )
            finish_refresh(session, log, outcome, clock())
        except BaseException as error:
            session.rollback()
            failed = RefreshOutcome("failed", _clip(f"unexpected error: {error!r}"))
            finish_refresh(session, log, failed, clock())
            raise
        finally:
            release_lock(session, LOCK_NAME, holder)
        return result
