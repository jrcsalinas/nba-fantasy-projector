"""Password hashing and the login lockout for the single login.

The password is stored only as an argon2id hash (in the ``PASSWORD_HASH`` environment
variable). argon2 does the comparison itself, so it is constant time.
"""

import math
import time
from collections.abc import Callable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_HASHER = PasswordHasher()
MAX_FAILURES = 5
PERIOD_SECONDS = 15 * 60
MAX_TRACKED_ADDRESSES = 10_000


def hash_password(password: str, hasher: PasswordHasher = _HASHER) -> str:
    """Hash a password with argon2id and a fresh random salt."""
    return hasher.hash(password)


def verify_password(password_hash: str, password: str, hasher: PasswordHasher = _HASHER) -> bool:
    """True only if ``password`` matches ``password_hash``.

    A wrong password, an empty password, and a malformed hash all give False; none of
    them raises, so a bad setting cannot be told apart from a wrong password by a caller.
    """
    if not password or not password_hash:
        return False
    try:
        return hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


class LoginLimiter:
    """Locks an address out after too many failed logins.

    ``max_failures`` failures within ``period_seconds`` lock the address out for
    ``period_seconds``; while locked every attempt is refused, whatever the password. A
    success clears the count. It lives in memory, so a restart clears it, and it keeps at
    most ``max_tracked`` addresses so a flood of addresses cannot use up memory.
    """

    def __init__(
        self,
        max_failures: int = MAX_FAILURES,
        period_seconds: float = PERIOD_SECONDS,
        max_tracked: int = MAX_TRACKED_ADDRESSES,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._max_failures = max_failures
        self._period = period_seconds
        self._max_tracked = max_tracked
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def retry_after(self, address: str) -> int:
        """Whole seconds until the address may try again; 0 if it is not locked out."""
        until = self._locked_until.get(address)
        if until is None:
            return 0
        now = self._clock()
        if until <= now:
            self.record_success(address)
            return 0
        return max(1, math.ceil(until - now))

    def record_failure(self, address: str) -> None:
        """Count a failed login, locking the address out when it reaches the limit."""
        now = self._clock()
        recent = [t for t in self._failures.get(address, []) if now - t < self._period]
        recent.append(now)
        self._failures[address] = recent
        if len(recent) >= self._max_failures:
            self._locked_until[address] = now + self._period
        self._forget_the_oldest()

    def record_success(self, address: str) -> None:
        """Forget an address's failures and lockout."""
        self._failures.pop(address, None)
        self._locked_until.pop(address, None)

    def tracked_addresses(self) -> int:
        """How many addresses are remembered."""
        return len(self._failures.keys() | self._locked_until.keys())

    def _forget_the_oldest(self) -> None:
        while self.tracked_addresses() > self._max_tracked:
            oldest = min(self._failures, key=lambda key: self._failures[key][-1])
            self.record_success(oldest)
