"""Shared scaffolding for jobs that write under the refresh lock and log themselves."""

import logging
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Generic, TypeVar

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from nbaproj.db.locks import acquire_lock, release_lock
from nbaproj.db.models import JobLock, RefreshLog
from nbaproj.db.refresh_log import (
    FINAL_STATUSES,
    RefreshOutcome,
    finish_refresh,
    reap_abandoned,
    start_refresh,
)
from nbaproj.ingestion.client import NbaClientError
from nbaproj.ingestion.parsing import PayloadError

logger = logging.getLogger(__name__)

LOCK_NAME = "refresh"
# Jobs that load game by game renew the lock between games (see ``Lease``). Jobs that
# do one fetch-then-store transaction cannot, so their TTL must exceed the worst-case
# fetch time: one stats call is at most 3 attempts x 60s (about 3 minutes with backoff),
# so 10 minutes covers the schedule and the 30-minute backfill TTL covers its 3 calls.
LOCK_TTL = timedelta(minutes=10)
# Longer than any job's lock TTL, so a run this old has certainly stopped.
ABANDONED_AFTER = timedelta(hours=1)
MESSAGE_LIMIT = 500
UUID_HEX_LENGTH = 32
# The lock holder is "<job name>-<uuid hex>" and must fit its column (Postgres enforces it).
MAX_JOB_NAME_LENGTH = JobLock.holder.type.length - UUID_HEX_LENGTH - 1
# Failures a job expects and reports as a ``failed`` run instead of raising.
EXPECTED_ERRORS = (NbaClientError, PayloadError, SQLAlchemyError)


@dataclass(frozen=True)
class RunResult:
    """How a locked run ended. Jobs extend this with their own counts."""

    status: str
    rows_upserted: int = 0
    message: str | None = None

    def __post_init__(self) -> None:
        if self.status not in FINAL_STATUSES:
            raise ValueError(
                f"unknown run status {self.status!r}; expected one of {sorted(FINAL_STATUSES)}"
            )


R = TypeVar("R", bound=RunResult)


@dataclass(frozen=True)
class Lease:
    """A held lock that long-running work can renew so it does not expire mid-run."""

    session: Session
    holder: str
    ttl: timedelta
    clock: Callable[[], datetime]

    def renew(self) -> bool:
        """Extend the lock. False means another writer holds it now and work must stop."""
        return acquire_lock(self.session, LOCK_NAME, self.holder, self.ttl, self.clock())


@dataclass
class Guard:
    """What ``guarded`` found: ``error`` is set if the block hit an expected failure."""

    error: str | None = None


@contextmanager
def guarded(session: Session, what: str) -> Iterator[Guard]:
    """Run a block, turning an expected failure into ``guard.error`` instead of raising.

    On an expected error (``EXPECTED_ERRORS``) the session is rolled back, the traceback
    is logged, and ``guard.error`` becomes ``"<what>: <error>"``. Anything else is a bug
    and propagates.
    """
    guard = Guard()
    try:
        yield guard
    except EXPECTED_ERRORS as error:
        session.rollback()
        logger.exception("%s failed", what)
        guard.error = f"{what}: {error}"


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

    def __post_init__(self) -> None:
        if len(self.name) > MAX_JOB_NAME_LENGTH:
            raise ValueError(
                f"job name {self.name!r} is longer than {MAX_JOB_NAME_LENGTH} characters, "
                "so the lock holder would not fit its column"
            )

    def run(
        self,
        session: Session,
        trigger: str,
        clock: Callable[[], datetime],
        work: Callable[[Lease], R],
    ) -> R:
        """Run ``work`` under the lock and log the outcome.

        Runs that stopped without finishing are failed first. The lock holder is unique
        per run, so two runs of the same job exclude each other. If the lock is held the
        run is ``rejected``. If ``work`` raises unexpectedly the log row is marked
        ``failed`` and the exception propagates. Problems while recording a failure or
        releasing the lock are logged and never replace the real outcome.
        """
        reap_abandoned(session, clock(), ABANDONED_AFTER)
        log = start_refresh(session, trigger, clock())
        holder = f"{self.name}-{uuid.uuid4().hex}"
        logger.info("%s: started (trigger=%s)", self.name, trigger)
        if not self._acquire(session, log, holder, clock):
            return self._rejected(session, log, clock)
        try:
            return self._execute(session, log, work, Lease(session, holder, self.ttl, clock))
        finally:
            self._release(session, holder)

    def _acquire(
        self, session: Session, log: RefreshLog, holder: str, clock: Callable[[], datetime]
    ) -> bool:
        try:
            return acquire_lock(session, LOCK_NAME, holder, self.ttl, clock())
        except BaseException as error:
            self._record_failure(session, log, error, clock)
            raise

    def _rejected(self, session: Session, log: RefreshLog, clock: Callable[[], datetime]) -> R:
        result = self.result_type(status="rejected", message="another refresh holds the lock")
        try:
            finish_refresh(session, log, RefreshOutcome("rejected", result.message), clock())
        except SQLAlchemyError:
            session.rollback()
            logger.exception("%s: rejected, but could not record it in the refresh log", self.name)
        logger.info("%s: rejected, another refresh holds the lock", self.name)
        return result

    def _execute(
        self,
        session: Session,
        log: RefreshLog,
        work: Callable[[Lease], R],
        lease: Lease,
    ) -> R:
        clock = lease.clock
        began = clock()
        try:
            result = work(lease)
        except BaseException as error:
            self._record_failure(session, log, error, clock)
            raise
        outcome = RefreshOutcome(result.status, _clip(result.message), result.rows_upserted)
        try:
            finish_refresh(session, log, outcome, clock())
        except SQLAlchemyError:
            # The work is already committed. Report its real result; the row stays
            # `running` and is marked abandoned by the next run.
            session.rollback()
            logger.exception("%s: finished but could not record it in the refresh log", self.name)
        logger.info(
            "%s: finished status=%s rows=%d in %.1fs: %s",
            self.name,
            result.status,
            result.rows_upserted,
            (clock() - began).total_seconds(),
            result.message or "no message",
        )
        return result

    def _record_failure(
        self,
        session: Session,
        log: RefreshLog,
        error: BaseException,
        clock: Callable[[], datetime],
    ) -> None:
        try:
            session.rollback()
            failed = RefreshOutcome("failed", _clip(f"unexpected error: {error!r}"))
            finish_refresh(session, log, failed, clock())
        except SQLAlchemyError:
            logger.exception("%s: could not record the failure in the refresh log", self.name)

    def _release(self, session: Session, holder: str) -> None:
        try:
            release_lock(session, LOCK_NAME, holder)
        except SQLAlchemyError:
            logger.exception("%s: could not release the lock", self.name)
