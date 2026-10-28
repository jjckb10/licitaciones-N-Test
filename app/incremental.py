"""Safe, bounded incremental refresh planning for Observatory ZIPs."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

COSTA_RICA = ZoneInfo('America/Costa_Rica')


def recent_months(now=None):
    """Oldest first: previous and current month, handles January boundaries."""
    now = now or datetime.now(COSTA_RICA)
    year, month = now.year, now.month
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    return (f'{prev_year:04d}{prev_month:02d}', f'{year:04d}{month:02d}')


def due_for_refresh(imported_at, now=None, hours=18):
    """No implicit full history rescan; retries a month only if stale."""
    now = now or datetime.now(COSTA_RICA)
    if not imported_at:
        return True
    try:
        previous = datetime.fromisoformat(imported_at)
        if previous.tzinfo is None:
            return True
        return now.astimezone(previous.tzinfo) - previous >= timedelta(hours=hours)
    except (TypeError, ValueError, OverflowError):
        return True
