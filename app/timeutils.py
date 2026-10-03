from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .config import settings

_TZ = ZoneInfo(settings.timezone)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def today() -> date:
    """Business date in the bank's time zone (Asia/Baku by default).

    Overdue / next-installment logic always uses this date, so a payment due
    "today" in Baku is not shown as overdue because the server runs in UTC.
    """
    return datetime.now(_TZ).date()
