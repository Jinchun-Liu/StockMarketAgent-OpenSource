"""Time helpers with an explicit China market timezone."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""

    return datetime.now(UTC)


def china_now() -> datetime:
    """Return an aware Asia/Shanghai timestamp."""

    return datetime.now(CHINA_TZ)
