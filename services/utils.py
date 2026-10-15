import html
import re
import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import config


def _tz():
    try:
        return ZoneInfo(config.TIMEZONE)
    except Exception:  # tzdata topilmasa Toshkent = UTC+5 (yozgi vaqt yo'q)
        return timezone(timedelta(hours=5))


TZ = _tz()
FMT = "%Y-%m-%d %H:%M:%S"


def now() -> datetime:
    return datetime.now(TZ)


def now_str() -> str:
    return now().strftime(FMT)


def esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def num(v, default: float = 0.0) -> float:
    try:
        return float(str(v).replace("\xa0", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return default


def money(v) -> str:
    return f"{round(num(v)):,}".replace(",", " ") + " so'm"


def area_fmt(v) -> str:
    s = f"{num(v):.2f}".rstrip("0").rstrip(".")
    return s or "0"


def parse_amount(text: str):
    """Musbat son yoki None."""
    t = (text or "").replace("\xa0", "").replace(" ", "").replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", t):
        return None
    val = float(t)
    return val if val > 0 else None


_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def rand_id(prefix: str, n: int = 4) -> str:
    return f"{prefix}-" + "".join(secrets.choice(_ALPHABET) for _ in range(n))


def short_ts(ts: str) -> str:
    """'2026-10-14 15:30:12' -> '14.10 15:30'"""
    try:
        return datetime.strptime(ts, FMT).strftime("%d.%m %H:%M")
    except (TypeError, ValueError):
        return ts or "—"
