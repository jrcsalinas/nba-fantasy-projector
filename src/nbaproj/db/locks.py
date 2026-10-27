"""An expiring named lock shared by all writers."""

from datetime import datetime, timedelta

from sqlalchemy import delete, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nbaproj.db.models import JobLock


def acquire_lock(session: Session, name: str, holder: str, ttl: timedelta, now: datetime) -> bool:
    """Try to take lock ``name`` for ``holder``. Return True if it was taken.

    An existing lock is taken over only if it has expired or already belongs to
    ``holder``. The takeover is one UPDATE, so two writers cannot both win.
    """
    expires_at = now + ttl
    takeover = session.execute(
        update(JobLock)
        .where(JobLock.name == name, or_(JobLock.expires_at <= now, JobLock.holder == holder))
        .values(holder=holder, locked_at=now, expires_at=expires_at)
    )
    if takeover.rowcount == 1:
        session.commit()
        return True

    session.add(JobLock(name=name, holder=holder, locked_at=now, expires_at=expires_at))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return False
    return True


def release_lock(session: Session, name: str, holder: str) -> bool:
    """Release lock ``name`` if ``holder`` owns it. Return True if it was released."""
    released = session.execute(
        delete(JobLock).where(JobLock.name == name, JobLock.holder == holder)
    )
    session.commit()
    return released.rowcount == 1
