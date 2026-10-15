"""Daromad (komissiya) va ish haqi hisob-kitobi.

Muhim: "daromad" (Employee Earnings) va "to'lov" (Salary Payments) alohida tushunchalar.
Qoldiq = jami daromad (komissiya + hisoblangan oylik) - jami to'langan.
"""
import time

from services.constants import ROLE_TASK
from services.google_sheets import EARNINGS, PAYMENTS, SETTINGS, get_db
from services.store import get_kv
from services.utils import money, now, now_str, num

_scache: dict = {"t": 0.0, "d": {}}


async def get_settings(fresh: bool = False) -> dict[str, str]:
    if fresh or time.time() - _scache["t"] > 20:
        rows = await get_db().rows(SETTINGS)
        _scache["d"] = {r["key"]: r["value"] for r in rows}
        _scache["t"] = time.time()
    return _scache["d"]


async def set_setting(key: str, value: str) -> bool:
    ok = await get_db().update(SETTINGS, "key", key, {"value": value})
    _scache["t"] = 0.0
    return ok


def calc_pay(task: str, order: dict, emp: dict, settings: dict) -> float:
    """Bitta vazifa uchun xodimga to'lanadigan summa."""
    if emp.get("salary_type", "commission") == "fixed":
        return 0.0  # faqat oylik oladi
    ptype = settings.get(f"{task}_pay_type", "fixed")
    val_raw = str(emp.get("commission_value", "")).strip()
    if val_raw != "":
        ptype = emp.get("commission_type") or ptype
        val = num(val_raw)
    else:
        val = num(settings.get(f"{task}_pay_value", 0))
    if ptype == "percent":
        base = num(order.get("final_price")) or num(order.get("estimated_price"))
        amount = base * val / 100
    elif ptype == "per_m2":
        amount = num(order.get("total_area")) * val
    else:
        amount = val
    return round(max(amount, 0.0), 2)


async def commission_text(role: str, order: dict, emp: dict) -> str:
    task = ROLE_TASK.get(role)
    if not task:
        return ""
    if emp.get("salary_type") == "fixed":
        return "💼 Siz oylik maoshdagi xodimsiz (bu ish uchun alohida komissiya yo'q)."
    return f"💵 Komissiya: <b>{money(calc_pay(task, order, emp, await get_settings()))}</b>"


async def record_earning(emp: dict, order: dict, task: str) -> float:
    """Daromadni faqat BIR marta yozadi (transaction_id = E-<buyurtma>-<vazifa>)."""
    amount = calc_pay(task, order, emp, await get_settings())
    if amount <= 0:
        return 0.0
    tx_id = f"E-{order['order_id']}-{task}"
    db = get_db()
    if any(r["transaction_id"] == tx_id for r in await db.rows(EARNINGS)):
        return amount
    kv = get_kv()
    if not await kv.set(f"earn:{tx_id}", "1", nx=True, ex=7 * 86400):
        return amount
    try:
        await db.append(EARNINGS, {
            "transaction_id": tx_id, "employee_id": str(emp["telegram_id"]), "order_id": order["order_id"],
            "task_type": task, "amount": amount, "date": now_str(), "payment_status": "hisoblangan",
        })
    except Exception:
        await kv.delete(f"earn:{tx_id}")
        raise
    return amount


async def accrue_monthly(emp: dict, ym: str | None = None) -> tuple[bool, str]:
    """Qat'iy oylikni shu oy uchun BIR marta hisoblaydi."""
    base = num(emp.get("base_salary"))
    if emp.get("salary_type") not in ("fixed", "mixed") or base <= 0:
        return False, "oylik belgilanmagan"
    ym = ym or now().strftime("%Y%m")
    tx_id = f"E-SAL-{emp['telegram_id']}-{ym}"
    db = get_db()
    if any(r["transaction_id"] == tx_id for r in await db.rows(EARNINGS)):
        return False, "bu oy uchun allaqachon hisoblangan"
    kv = get_kv()
    if not await kv.set(f"earn:{tx_id}", "1", nx=True, ex=40 * 86400):
        return False, "bu oy uchun allaqachon hisoblangan"
    try:
        await db.append(EARNINGS, {
            "transaction_id": tx_id, "employee_id": str(emp["telegram_id"]), "order_id": "",
            "task_type": "salary", "amount": base, "date": now_str(), "payment_status": "hisoblangan",
        })
    except Exception:
        await kv.delete(f"earn:{tx_id}")
        raise
    return True, money(base)


async def all_balances() -> dict[str, dict]:
    db = get_db()
    res: dict[str, dict] = {}
    for r in await db.rows(EARNINGS):
        b = res.setdefault(r["employee_id"], {"earned": 0.0, "paid": 0.0})
        b["earned"] += num(r["amount"])
    for r in await db.rows(PAYMENTS):
        b = res.setdefault(r["employee_id"], {"earned": 0.0, "paid": 0.0})
        b["paid"] += num(r["amount_paid"])
    for b in res.values():
        b["outstanding"] = round(b["earned"] - b["paid"], 2)
    return res


async def balance(tid) -> dict:
    return (await all_balances()).get(str(tid), {"earned": 0.0, "paid": 0.0, "outstanding": 0.0})


async def record_payment(emp_id, amount: float, method: str, notes: str, admin_id, key: str) -> tuple[bool, str]:
    """To'lovni yozadi. payment_id = P-<key> bo'lgani uchun takroriy bosish dublikat yaratmaydi."""
    pid = f"P-{key}"
    db = get_db()
    if any(r["payment_id"] == pid for r in await db.rows(PAYMENTS)):
        return False, "Bu to'lov allaqachon saqlangan."
    bal = await balance(emp_id)
    if amount > bal["outstanding"] + 0.01:
        return False, f"Summa qoldiqdan ({money(bal['outstanding'])}) katta bo'lishi mumkin emas."
    kv = get_kv()
    if not await kv.set(f"pay:{pid}", "1", nx=True, ex=7 * 86400):
        return False, "Bu to'lov allaqachon saqlanmoqda."
    try:
        await db.append(PAYMENTS, {
            "payment_id": pid, "employee_id": str(emp_id), "amount_paid": amount, "payment_date": now_str(),
            "payment_method": method, "notes": notes, "admin_id": str(admin_id),
        })
    except Exception:
        await kv.delete(f"pay:{pid}")
        raise
    return True, "ok"


async def earnings_of(tid) -> list[dict]:
    rows = [r for r in await get_db().rows(EARNINGS) if r["employee_id"] == str(tid)]
    return sorted(rows, key=lambda r: r["date"], reverse=True)


async def payments_of(tid) -> list[dict]:
    rows = [r for r in await get_db().rows(PAYMENTS) if r["employee_id"] == str(tid)]
    return sorted(rows, key=lambda r: r["payment_date"], reverse=True)
