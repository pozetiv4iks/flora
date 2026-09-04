"""Reminder time logic with local timezone."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import Config

_TZ = ZoneInfo(Config.REMINDER_TIMEZONE)


def now_local() -> datetime:
    return datetime.now(_TZ).replace(tzinfo=None)


def local_today() -> str:
    return now_local().strftime("%Y-%m-%d")


def _parse_time(value: str) -> tuple[int, int] | None:
    if not value:
        return None
    try:
        parts = value.strip().split(":")
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None


def _in_remind_window(now: datetime, target: datetime) -> bool:
    grace = timedelta(minutes=Config.REMINDER_GRACE_MINUTES)
    return target <= now < target + grace


def should_remind_plan(plan: dict, now: datetime | None = None) -> bool:
    now = now or now_local()
    if plan.get("plan_date") != local_today():
        return False
    parsed = _parse_time(plan.get("remind_at_time") or "09:00")
    if not parsed:
        return False
    target = now.replace(hour=parsed[0], minute=parsed[1], second=0, microsecond=0)
    return _in_remind_window(now, target)


def should_remind_schedule_event(event: dict, now: datetime | None = None) -> bool:
    now = now or now_local()
    event_date = event.get("event_date")
    event_time = event.get("event_time")
    minutes_before = event.get("remind_minutes_before")
    if minutes_before is None:
        minutes_before = Config.DEFAULT_REMIND_MINUTES_BEFORE

    if event_time:
        parsed = _parse_time(event_time)
        if not parsed:
            return False
        try:
            event_dt = datetime.strptime(event_date, "%Y-%m-%d").replace(
                hour=parsed[0], minute=parsed[1], second=0, microsecond=0
            )
        except ValueError:
            return False
        remind_dt = event_dt - timedelta(minutes=minutes_before)
        return _in_remind_window(now, remind_dt)

    today = local_today()
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    if event_date == tomorrow:
        parsed = _parse_time(event.get("remind_at_time") or "20:00")
        if not parsed:
            return False
        target = now.replace(hour=parsed[0], minute=parsed[1], second=0, microsecond=0)
        return _in_remind_window(now, target)

    if event_date == today:
        parsed = _parse_time(event.get("remind_at_time") or "09:00")
        if not parsed:
            return False
        target = now.replace(hour=parsed[0], minute=parsed[1], second=0, microsecond=0)
        return _in_remind_window(now, target)

    return False
