"""Xodimlar: Telegram ID bo'yicha aniqlash (username bo'yicha emas)."""
import time

import config
from services.google_sheets import EMPLOYEES, get_db
from services.utils import now_str

_cache: dict = {"t": 0.0, "rows": []}
CACHE_TTL = 10  # soniya (xavfsiz amallarda fresh=True ishlatiladi)


def _is_true(v) -> bool:
    return str(v).strip().upper() in ("TRUE", "1", "HA", "YES")


async def all_employees(fresh: bool = False) -> list[dict]:
    if fresh or time.time() - _cache["t"] > CACHE_TTL:
        _cache["rows"] = await get_db().rows(EMPLOYEES)
        _cache["t"] = time.time()
    return _cache["rows"]


def _invalidate():
    _cache["t"] = 0.0


async def get_employee(tid, fresh: bool = False) -> dict | None:
    for e in await all_employees(fresh):
        if str(e.get("telegram_id")) == str(tid):
            return e
    return None


def owner_actor(tid: int, name: str = "") -> dict:
    return {
        "telegram_id": str(tid), "username": "", "full_name": name or "Ega", "role": "owner",
        "active": "TRUE", "salary_type": "fixed", "base_salary": "0",
        "commission_type": "", "commission_value": "", "created_at": "",
    }


async def get_actor(tid: int, fresh: bool = False, name: str = "") -> dict | None:
    """Foydalanuvchi huquqi: ADMIN_IDS dagilar — Ega; qolganlar Employees varag'idan (faqat faol)."""
    if int(tid) in config.OWNER_IDS:
        return owner_actor(tid, name)
    e = await get_employee(tid, fresh)
    if e and _is_true(e.get("active")) and e.get("role"):
        return e
    return None


async def name_map() -> dict[str, str]:
    m = {str(e["telegram_id"]): e.get("full_name", "") for e in await all_employees()}
    for oid in config.OWNER_IDS:
        m.setdefault(str(oid), "Ega")
    return m


async def active_by_role(role: str) -> list[dict]:
    return [e for e in await all_employees(True) if e.get("role") == role and _is_true(e.get("active"))]


async def admin_ids() -> list[int]:
    ids = set(config.OWNER_IDS)
    for e in await all_employees():
        if e.get("role") == "admin" and _is_true(e.get("active")) and str(e["telegram_id"]).isdigit():
            ids.add(int(e["telegram_id"]))
    return sorted(ids)


async def add_employee(tid: int, full_name: str, role: str, username: str = "",
                       salary_type: str = "commission", base_salary: float = 0) -> dict:
    row = {
        "telegram_id": str(tid), "username": username, "full_name": full_name, "role": role,
        "active": "TRUE", "salary_type": salary_type, "base_salary": base_salary,
        "commission_type": "", "commission_value": "", "created_at": now_str(),
    }
    await get_db().append(EMPLOYEES, row)
    _invalidate()
    return row


async def update_employee(tid, updates: dict) -> bool:
    ok = await get_db().update(EMPLOYEES, "telegram_id", str(tid), updates)
    _invalidate()
    return ok


def is_active(e: dict) -> bool:
    return _is_true(e.get("active"))
