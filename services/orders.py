"""Buyurtma yaratish, bosqichlar, biriktirish va statistika.

Bir buyurtmani bir vaqtda ikki kishi o'zgartira olmasligi uchun har bir yozuv
amali Redis qulfi (kv.lock) ichida bajariladi va holat Google Sheets'dan
QULF ICHIDA qayta o'qiladi. Shu tufayli "ikki haydovchi bitta buyurtmani oladi"
holati yuzaga kelmaydi.
"""
import json
import re
from dataclasses import dataclass
from datetime import timedelta

from services import employees, salaries
from services.constants import COMMISSION_COL, REASSIGN, STAGES, STATUS_LABELS
from services.google_sheets import CUSTOMERS, EARNINGS, HISTORY, ORDERS, get_db
from services.store import LockBusy, get_kv  # noqa: F401
from services.utils import FMT, now, now_str, num, rand_id


@dataclass
class Result:
    ok: bool
    msg: str = ""
    order: dict | None = None


# ------------------------------------------------------------------ o'qish
async def get_order(order_id: str) -> dict | None:
    for o in await get_db().rows(ORDERS):
        if o["order_id"] == order_id:
            return o
    return None


async def list_orders(statuses: list[str] | None = None, who_col: str | None = None,
                      who_id=None, created_by=None) -> list[dict]:
    out = []
    for o in await get_db().rows(ORDERS):
        if statuses and o["status"] not in statuses:
            continue
        if who_col and str(o.get(who_col)) != str(who_id):
            continue
        if created_by is not None and str(o.get("created_by")) != str(created_by):
            continue
        out.append(o)
    return sorted(out, key=lambda o: o["created_at"], reverse=True)


async def search_orders(q: str) -> list[dict]:
    q = q.strip().lower()
    digits = re.sub(r"\D", "", q)
    out = []
    for o in await get_db().rows(ORDERS):
        if (q and q in o["order_id"].lower()) or (q and q in o["customer_name"].lower()) \
                or (len(digits) >= 3 and digits in re.sub(r"\D", "", o["phone"])):
            out.append(o)
    return sorted(out, key=lambda o: o["created_at"], reverse=True)[:15]


async def history_of(order_id: str) -> list[dict]:
    rows = [h for h in await get_db().rows(HISTORY) if h["order_id"] == order_id]
    return sorted(rows, key=lambda h: h["timestamp"])


async def _log(order_id, prev, new, actor, details):
    await get_db().append(HISTORY, {
        "order_id": order_id, "previous_status": prev, "new_status": new,
        "employee_telegram_id": str(actor["telegram_id"]), "employee_name": actor.get("full_name", ""),
        "timestamp": now_str(), "action_details": details,
    })


# ------------------------------------------------------------------ yaratish
async def create_order(d: dict, creator: dict) -> dict:
    db = get_db()
    ts = now_str()
    phone = d["phone"]
    customer_id = None
    for c in await db.rows(CUSTOMERS):
        if c["phone"] == phone:
            customer_id = c["customer_id"]
            break
    existing = {o["order_id"] for o in await db.rows(ORDERS)}
    order_id = rand_id("ORD-" + now().strftime("%y%m%d"))
    while order_id in existing:
        order_id = rand_id("ORD-" + now().strftime("%y%m%d"))

    dims = [{"length": l, "width": w, "area": round(l * w, 2)} for l, w in d["dims"]]
    total = round(sum(x["area"] for x in dims), 2)

    if customer_id is None:
        customer_id = rand_id("C", 6)
        await db.append(CUSTOMERS, {
            "customer_id": customer_id, "full_name": d["name"], "phone": phone,
            "address": d["address"], "order_ids": order_id, "created_at": ts,
        })
    else:
        for c in await db.rows(CUSTOMERS):
            if c["customer_id"] == customer_id:
                ids = [x for x in c["order_ids"].split(",") if x] + [order_id]
                await db.update(CUSTOMERS, "customer_id", customer_id,
                                {"order_ids": ",".join(ids), "address": d["address"], "full_name": d["name"]})
                break

    row = {
        "order_id": order_id, "customer_id": customer_id, "customer_name": d["name"], "phone": phone,
        "pickup_address": d["address"], "latitude": d.get("lat", ""), "longitude": d.get("lon", ""),
        "carpet_count": len(dims), "dimensions_json": json.dumps(dims, ensure_ascii=False),
        "total_area": total, "carpet_type": d.get("ctype", ""), "instructions": d.get("instr", ""),
        "estimated_price": d["price"], "final_price": "", "status": "new",
        "created_by": str(creator["telegram_id"]), "created_at": ts, "updated_at": ts,
    }
    await db.append(ORDERS, row)
    await _log(order_id, "-", "new", creator, "Buyurtma ro'yxatdan o'tkazildi")
    return row


# ------------------------------------------------------------------ bosqichlar
async def advance(order_id: str, act: str, actor: dict) -> Result:
    st = STAGES.get(act)
    if not st:
        return Result(False, "Noma'lum amal.")
    emp = await employees.get_actor(int(actor["telegram_id"]), fresh=True)
    if not emp or emp["role"] != st["role"]:
        return Result(False, "⛔ Sizda bu amalni bajarish huquqi yo'q.")
    tid = str(emp["telegram_id"])
    try:
        async with get_kv().lock(f"order:{order_id}"):
            o = await get_order(order_id)
            if not o:
                return Result(False, "Buyurtma topilmadi.")
            if o["status"] != st["frm"]:
                if st["claim"] and o["status"] not in ("cancelled",):
                    return Result(False, "⚠️ Bu buyurtma allaqachon boshqa xodimga biriktirilgan yoki holati o'zgargan.")
                return Result(False, "⚠️ Buyurtma holati mos emas (amal allaqachon bajarilgan bo'lishi mumkin).")
            if not st["claim"] and str(o.get(st["who"])) != tid:
                return Result(False, "⛔ Bu buyurtma sizga biriktirilmagan.")

            ts = now_str()
            updates = {"status": st["to"], "updated_at": ts}
            if st["claim"]:
                updates[st["who"]] = tid
            if st["ts"]:
                updates[st["ts"]] = ts
            if st["earn"]:
                task = st["earn"]
                amount = await salaries.record_earning(emp, o, task)
                updates[COMMISSION_COL[task]] = amount
            if not await get_db().update(ORDERS, "order_id", order_id, updates):
                return Result(False, "Jadvalni yangilab bo'lmadi, qayta urinib ko'ring.")
            o.update(updates)
            await _log(order_id, st["frm"], st["to"], emp, st["log"])
            return Result(True, "✅ " + st["log"] + ".", o)
    except LockBusy as e:
        return Result(False, str(e))


async def reassign(order_id: str, stage: str, new_emp: dict, admin: dict) -> Result:
    cfg = REASSIGN[stage]
    if admin["role"] not in ("owner", "admin"):
        return Result(False, "⛔ Faqat administrator qayta biriktira oladi.")
    fresh = await employees.get_employee(new_emp["telegram_id"], fresh=True)
    if not fresh or fresh.get("role") != cfg["role"] or not employees.is_active(fresh):
        return Result(False, "Tanlangan xodim bu vazifa uchun mos emas yoki faol emas.")
    try:
        async with get_kv().lock(f"order:{order_id}"):
            o = await get_order(order_id)
            if not o:
                return Result(False, "Buyurtma topilmadi.")
            if o["status"] not in cfg["statuses"]:
                return Result(False, "Bu bosqich allaqachon yakunlangan yoki hali boshlanmagan — qayta biriktirib bo'lmaydi.")
            old = o.get(cfg["col"]) or ""
            ts = now_str()
            updates = {cfg["col"]: str(fresh["telegram_id"]), "updated_at": ts}
            prev = o["status"]
            if prev == cfg["statuses"][0]:  # hali hech kim olmagan -> biriktirish bilan boshlanadi
                claim = STAGES[cfg["claim_act"]]
                updates["status"] = claim["to"]
                if claim["ts"]:
                    updates[claim["ts"]] = ts
            await get_db().update(ORDERS, "order_id", order_id, updates)
            o.update(updates)
            names = await employees.name_map()
            await _log(order_id, prev, o["status"], admin,
                       f"Biriktirildi: {names.get(str(old), old) or '—'} → {fresh['full_name']} ({stage})")
            return Result(True, "✅ Biriktirildi.", {**o, "_old": str(old)})
    except LockBusy as e:
        return Result(False, str(e))


async def cancel_order(order_id: str, admin: dict) -> Result:
    if admin["role"] not in ("owner", "admin"):
        return Result(False, "⛔ Ruxsat yo'q.")
    try:
        async with get_kv().lock(f"order:{order_id}"):
            o = await get_order(order_id)
            if not o:
                return Result(False, "Buyurtma topilmadi.")
            if o["status"] in ("delivered", "cancelled"):
                return Result(False, "Bu buyurtmani bekor qilib bo'lmaydi.")
            await get_db().update(ORDERS, "order_id", order_id, {"status": "cancelled", "updated_at": now_str()})
            await _log(order_id, o["status"], "cancelled", admin, "Administrator buyurtmani bekor qildi")
            o["status"] = "cancelled"
            return Result(True, "Buyurtma bekor qilindi.", o)
    except LockBusy as e:
        return Result(False, str(e))


async def set_final_price(order_id: str, price: float, admin: dict) -> Result:
    if admin["role"] not in ("owner", "admin"):
        return Result(False, "⛔ Ruxsat yo'q.")
    try:
        async with get_kv().lock(f"order:{order_id}"):
            o = await get_order(order_id)
            if not o:
                return Result(False, "Buyurtma topilmadi.")
            await get_db().update(ORDERS, "order_id", order_id, {"final_price": price, "updated_at": now_str()})
            await _log(order_id, o["status"], o["status"], admin,
                       f"Yakuniy narx o'zgartirildi: {o['final_price'] or '—'} → {price:g}")
            o["final_price"] = price
            return Result(True, "Yakuniy narx saqlandi.", o)
    except LockBusy as e:
        return Result(False, str(e))


# ------------------------------------------------------------------ xodim statistikasi
async def employee_counts(tid) -> dict:
    tid = str(tid)
    c = {"collection": 0, "washing": 0, "finishing": 0, "delivery": 0}
    for o in await get_db().rows(ORDERS):
        c["collection"] += o["collection_driver_id"] == tid and bool(o["collected_at"])
        c["washing"] += o["washing_employee_id"] == tid and bool(o["washing_completed_at"])
        c["finishing"] += o["finishing_employee_id"] == tid and bool(o["finishing_completed_at"])
        c["delivery"] += o["delivery_driver_id"] == tid and bool(o["delivered_at"])
    return c


async def employee_history(tid, limit: int = 15) -> list[dict]:
    rows = [h for h in await get_db().rows(HISTORY) if h["employee_telegram_id"] == str(tid)]
    return sorted(rows, key=lambda h: h["timestamp"], reverse=True)[:limit]


# ------------------------------------------------------------------ biznes statistikasi
async def statistics() -> list[dict]:
    n = now()
    midnight = n.replace(hour=0, minute=0, second=0, microsecond=0)
    periods = [
        ("📅 Bugun", midnight),
        ("🗓 Shu hafta", midnight - timedelta(days=n.weekday())),
        ("📆 Shu oy", midnight.replace(day=1)),
    ]
    db = get_db()
    orders, earns = await db.rows(ORDERS), await db.rows(EARNINGS)
    liabilities = sum(max(b["outstanding"], 0) for b in (await salaries.all_balances()).values())
    out = []
    for label, start in periods:
        s = start.strftime(FMT)

        def inp(ts):
            return bool(ts) and ts >= s

        out.append({
            "label": label,
            "total": sum(inp(o["created_at"]) for o in orders),
            "completed": sum(inp(o["delivered_at"]) for o in orders),
            "pending": sum(inp(o["created_at"]) and o["status"] not in ("delivered", "cancelled") for o in orders),
            "collected": sum(int(num(o["carpet_count"])) for o in orders if inp(o["collected_at"])),
            "washed": sum(int(num(o["carpet_count"])) for o in orders if inp(o["washing_completed_at"])),
            "finished": sum(int(num(o["carpet_count"])) for o in orders if inp(o["finishing_completed_at"])),
            "delivered": sum(int(num(o["carpet_count"])) for o in orders if inp(o["delivered_at"])),
            "sqm": round(sum(num(o["total_area"]) for o in orders if inp(o["washing_completed_at"])), 2),
            "earnings": sum(num(e["amount"]) for e in earns if inp(e["date"])),
            "liabilities": liabilities,
        })
    return out


def status_label(s: str) -> str:
    return STATUS_LABELS.get(s, s)
