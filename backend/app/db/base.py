from datetime import UTC, datetime


def utcnow() -> datetime:
    """Current UTC time truncated to milliseconds: BSON stores millisecond precision, and the audit
    hash chain must hash exactly what is stored."""
    now = datetime.now(UTC)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def edf_local_to_utc(dt: datetime, tz_name: str) -> datetime:
    """EDF start times have no time zone and are stored as if they were UTC. Re-read the wall-clock time in the
    site's time zone and convert to real UTC."""
    from zoneinfo import ZoneInfo

    wall = dt.replace(tzinfo=None)
    return wall.replace(tzinfo=ZoneInfo(tz_name)).astimezone(UTC)
