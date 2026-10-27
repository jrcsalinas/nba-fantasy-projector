"""Custom column types."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, TypeDecorator


class UtcDateTime(TypeDecorator):
    """A datetime column that stores UTC and always returns aware UTC values.

    Naive datetimes are rejected so a local time can never be stored by mistake.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime is not allowed; pass a timezone-aware value")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_literal_param(self, value: datetime | None, dialect):
        return self.process_bind_param(value, dialect)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)
