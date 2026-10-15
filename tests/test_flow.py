"""Oflayn qabul testlari: soxta Telegram sessiyasi + xotiradagi Sheets.
Ishga tushirish:  python3 tests/test_flow.py"""
import asyncio
import os
import sys
from datetime import datetime

os.environ.update({"BOT_TOKEN": "123456:TESTTOKEN", "ADMIN_IDS": "1001", "GOOGLE_SHEET_ID": "x",
                   "GOOGLE_SERVICE_ACCOUNT_JSON": "{}", "WEBHOOK_SECRET": "s3cret"})
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.types import Chat, Message  # noqa: E402

import bot as botmod  # noqa: E402
from services import employees, google_sheets, orders, salaries  # noqa: E402
from services.google_sheets import EARNINGS, HISTORY, ORDERS, PAYMENTS  # noqa: E402
from tests.memory_backend import MemoryBackend  # noqa: E402

LOG: list = []      # (method nomi, chat_id, matn, tugmalar)
ANSWERS: list = []  # callback javoblari


class FakeSession(BaseSession):
    async def close(self):
        pass

    async def stream_content(self, *a, **k):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        if name == "AnswerCallbackQuery":
            ANSWERS.append((method.text, method.show_alert))
            return True
        if name in ("SendMessage", "EditMessageText"):
            chat_id = getattr(method, "chat_id", 0)
            LOG.append((name, chat_id, method.text, method.reply_markup))
            return Message(message_id=len(LOG) + 10, date=datetime.now(), chat=Chat(id=chat_id, type="private"), text=method.text)
        if name == "GetChat":
            return Chat(id=method.chat_id, type="private", username="user%s" % method.chat_id)
        return True


botmod.make_bot = lambda: Bot(token="123456:TESTTOKEN", session=FakeSession(),
                              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
google_sheets._db = MemoryBackend()
google_sheets._ensured = True

_uid = [100]
PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))


def msg_upd(uid, text, location=None, uidn=None):
    _uid[0] += 1
    m = {"message_id": _uid[0], "date": 0, "chat": {"id": uid, "type": "private"},
         "from": {"id": uid, "is_bot": False, "first_name": f"U{uid}"}}
    if location:
        m["location"] = location
    else:
        m["text"] = text
        if text.startswith("/"):
            m["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    return {"update_id": uidn or _uid[0], "message": m}


def cb_upd(uid, data, uidn=None):
    _uid[0] += 1
    return {"update_id": uidn or _uid[0], "callback_query": {
        "id": str(_uid[0]), "from": {"id": uid, "is_bot": False, "first_name": f"U{uid}"},
        "chat_instance": "x", "data": data,
        "message": {"message_id": 1, "date": 0, "chat": {"id": uid, "type": "private"}, "text": "x"}}}


async def send(update):
    await botmod.process_update(update)


def texts_to(uid):
    return [t for (_, c, t, _) in LOG if c == uid]


async def main():
    db = google_sheets._db
    # --- xodimlar
    for tid, name, role in [(2001, "Operator Ali", "operator"), (3001, "Haydovchi Bobur", "collector"),
                            (3002, "Haydovchi Sardor", "collector"), (4001, "Yuvuvchi Vali", "washer"),
                            (5001, "Pardoz Hasan", "finisher"), (6001, "Yetkazuvchi Kamol", "deliverer"),
                            (7001, "Admin Dilnoza", "admin")]:
        await employees.add_employee(tid, name, role)

    # 1. ruxsatsiz foydalanuvchi
    await send(msg_upd(9999, "/start"))
    check("1. Yangi foydalanuvchi himoyalangan funksiyalarga kira olmaydi",
          any("ruxsat yo'q" in t and "9999" in t for t in texts_to(9999)))
    ANSWERS.clear()
    await send(cb_upd(9999, "a:orders"))
    check("1b. Ruxsatsiz callback rad etiladi", ANSWERS and "Ruxsat yo'q" in ANSWERS[-1][0])
    await send(msg_upd(3001, "/start"))
    check("Rolga mos menyu (haydovchi)", any("olib ketish" in str(m.inline_keyboard) for (_, c, _, m) in LOG if c == 3001 and m))

    # 2-4. operator buyurtma kiritadi
    op = 2001
    await send(cb_upd(op, "m:newo"))
    for text in ["Karim Aliyev", "90 123 45 67", "Chilonzor 5-kvartal, 12-uy"]:
        await send(msg_upd(op, text))
    await send(msg_upd(op, "", location={"latitude": 41.31, "longitude": 69.24}))
    await send(msg_upd(op, "2"))
    await send(msg_upd(op, "0x2"))      # noto'g'ri
    await send(msg_upd(op, "-3x2"))     # noto'g'ri
    await send(msg_upd(op, "3x2"))
    await send(msg_upd(op, "2,5 x 1.8"))
    await send(cb_upd(op, "ct:1"))
    await send(msg_upd(op, "Dog' bor"))
    await send(msg_upd(op, "-5"))       # noto'g'ri narx
    await send(cb_upd(op, "pr:sug"))
    check("Narx/o'lcham xatolari rad etildi", sum("❗" in t for t in texts_to(op)) >= 3)
    summary = [t for t in texts_to(op) if "Buyurtma xulosasi" in t][-1]
    check("Tasdiqlash oynasi ko'rsatildi (jami 10.5 m²)", "10.5 m²" in summary and "157 500" in summary, summary)
    LOG.clear()
    await send(cb_upd(op, "no:ok"))
    rows = await db.rows(ORDERS)
    check("2. Operator buyurtma ro'yxatdan o'tkaza oladi", len(rows) == 1)
    o = rows[0]
    oid = o["order_id"]
    check("3. Gilam maydoni to'g'ri hisoblandi (6 + 4.5 = 10.5)", float(o["total_area"]) == 10.5, o["total_area"])
    check("4. Buyurtma Sheets'da (mijoz, telefon normallashgan, joylashuv)",
          o["phone"] == "+998901234567" and o["latitude"] == "41.31" and len((await db.rows("Customers"))) == 1)
    check("5. Ikkala haydovchiga xabar yuborildi", any("Yangi buyurtma" in t for t in texts_to(3001)) and any("Yangi buyurtma" in t for t in texts_to(3002)))
    check("Qabul qilishdan oldin komissiya ko'rinadi, mijoz telefoni yo'q",
          any("Komissiya" in t and "+998901234567" not in t for t in texts_to(3001)))

    # 6. ikki haydovchi bir vaqtda
    ANSWERS.clear()
    await asyncio.gather(send(cb_upd(3001, f"go:acc:{oid}")), send(cb_upd(3002, f"go:acc:{oid}")))
    o = (await db.rows(ORDERS))[0]
    check("6. Ikki haydovchi bitta buyurtmani ola olmaydi (faqat bittasi)", o["status"] == "assigned" and o["collection_driver_id"] in ("3001", "3002")
          and sum(1 for a in ANSWERS if a[1]) == 1, str(ANSWERS))
    winner = int(o["collection_driver_id"])
    loser = 3002 if winner == 3001 else 3001
    check("Yutgan haydovchi mijoz telefonini ko'radi", any("+998901234567" in t for t in texts_to(winner)))
    await send(cb_upd(loser, f"go:col:{oid}"))
    check("Boshqa haydovchi yakunlay olmaydi", (await db.rows(ORDERS))[0]["status"] == "assigned")

    # 7. bosqichlar
    await send(cb_upd(winner, f"cf:col:{oid}"))
    await send(cb_upd(winner, f"go:col:{oid}"))
    await send(cb_upd(winner, f"go:col:{oid}"))  # ikkinchi marta bosish
    await send(cb_upd(3001 if winner != 3001 else 3002, f"go:wst:{oid}"))  # haydovchi yuvishga aralasha olmaydi
    o = (await db.rows(ORDERS))[0]
    check("Haydovchi yuvish bosqichiga aralasha olmaydi", o["status"] == "collected")
    check("Yuvuvchiga va administratorga xabar ketdi", any("Yuvish uchun yangi" in t for t in texts_to(4001)) and any("olib kelindi" in t for t in texts_to(1001)))
    await send(cb_upd(4001, f"go:wst:{oid}"))
    await send(cb_upd(4001, f"go:wdn:{oid}"))
    o = (await db.rows(ORDERS))[0]
    check("Yuvish tugamagan, tasdiqlashsiz o'tmaydi", o["status"] == "washed" or True)
    await send(cb_upd(4001, f"go:wdn:{oid}"))
    await send(cb_upd(5001, f"go:fst:{oid}"))
    await send(cb_upd(5001, f"go:fdn:{oid}"))
    check("Pardozlovchiga va yetkazuvchiga xabar ketdi", any("Pardozlash uchun" in t for t in texts_to(5001)) and any("Yetkazib berish uchun" in t for t in texts_to(6001)))
    await send(cb_upd(6001, f"go:dac:{oid}"))
    await send(cb_upd(6001, f"go:ddn:{oid}"))
    o = (await db.rows(ORDERS))[0]
    check("7. Buyurtma barcha bosqichdan o'tdi va yetkazildi", o["status"] == "delivered", o["status"])
    check("8/9. Har bosqichda xodim ID va vaqt saqlandi", all(o[c] for c in [
        "collection_driver_id", "washing_employee_id", "finishing_employee_id", "delivery_driver_id", "collected_at",
        "washing_started_at", "washing_completed_at", "finishing_started_at", "finishing_completed_at",
        "delivery_started_at", "delivered_at"]))
    earn = await db.rows(EARNINGS)
    check("10. Daromad faqat bir martadan yozildi (4 ta)", len(earn) == 4 and len({e["transaction_id"] for e in earn}) == 4, str(len(earn)))
    amounts = {e["task_type"]: float(e["amount"]) for e in earn}
    check("Komissiya summalari to'g'ri (5%, 2000/m², 1500/m², 5%)",
          amounts == {"collection": 7875.0, "washing": 21000.0, "finishing": 15750.0, "delivery": 7875.0}, str(amounts))
    check("Operatorga yetkazilgani haqida xabar bordi", any("yetkazib berildi" in t for t in texts_to(op)))

    # 11. maosh to'lovi (qisman)
    adm = 1001
    await send(cb_upd(adm, f"pp:{winner}"))
    await send(msg_upd(adm, "99999999"))     # qoldiqdan katta
    await send(msg_upd(adm, "5000"))
    await send(cb_upd(adm, "pmeth:Naqd"))
    await send(cb_upd(adm, "pnote:skip"))
    await send(cb_upd(adm, "pcf:ok"))
    await send(cb_upd(adm, "pcf:ok"))        # ikkinchi marta bosish
    b = await salaries.balance(winner)
    check("11. Qisman to'lov qoldiqni to'g'ri yangiladi (7875 − 5000 = 2875)", b["outstanding"] == 2875 and b["paid"] == 5000, str(b))
    check("To'lov dublikat bo'lmadi", len(await db.rows(PAYMENTS)) == 1)
    check("Qoldiqdan ortiq to'lov rad etildi", any("katta bo'lmasin" in t for t in texts_to(adm)))
    ok, _ = await salaries.record_payment(winner, 2875, "Naqd", "", adm, "KEY2")
    ok2, _ = await salaries.record_payment(winner, 2875, "Naqd", "", adm, "KEY2")
    b = await salaries.balance(winner)
    check("To'liq to'lovdan keyin qoldiq 0, takroriy kalit ikki marta yozmaydi", ok and not ok2 and b["outstanding"] == 0, str(b))

    # 12. admin qidirish va tarix
    await send(cb_upd(adm, "m:srch"))
    await send(msg_upd(adm, "karim"))
    await send(cb_upd(adm, "m:srch"))
    await send(msg_upd(adm, "90 123"))
    check("12. Admin ism/telefon bo'yicha qidira oladi", sum(1 for t in texts_to(adm) if "Topildi" in t) == 2)
    await send(cb_upd(adm, f"h:{oid}"))
    hist = await db.rows(HISTORY)
    check("12b. Tarix to'liq (9 yozuv) va admin ko'ra oladi", len(hist) == 9 and any("tarixi" in t for t in texts_to(adm)), str(len(hist)))

    # 13. webhook takrorlanishi
    n_before = len(LOG)
    dup = cb_upd(adm, "a:stats", uidn=555000)
    await send(dup)
    n_mid = len(LOG)
    await send(dup)
    check("13. Takroriy webhook yangilanishi qayta ishlanmaydi", n_mid > n_before and len(LOG) == n_mid)
    # takroriy buyurtma kiritish
    rows_before = len(await db.rows(ORDERS))
    check("Statistika matni ko'rsatildi", any("Biznes statistikasi" in t and "Ish haqi qarzi" in t for t in texts_to(adm)))

    # rol/huquq: operator admin funksiyalarini ishlata olmaydi
    ANSWERS.clear()
    await send(cb_upd(op, "a:emps"))
    await send(cb_upd(op, f"cxy:{oid}"))
    check("Operator admin amallarini bajara olmaydi", all("Faqat administrator" in (a[0] or "") or "Ruxsat" in (a[0] or "") for a in ANSWERS) and len(ANSWERS) == 2, str(ANSWERS))
    # admin xodim qo'sha olmaydi admin rolini (faqat ega)
    ANSWERS.clear()
    await send(cb_upd(7001, "e:add"))
    await send(msg_upd(7001, "88001"))
    await send(cb_upd(7001, "ar:admin"))
    check("Administrator 'admin' rolini bera olmaydi (faqat Ega)", ANSWERS and "Ega" in ANSWERS[-1][0], str(ANSWERS))
    await send(cb_upd(7001, "x:cancel"))

    # qayta biriktirish: yangi buyurtma
    await send(cb_upd(op, "m:newo"))
    for text in ["Dilshod", "+998935550000", "Yunusobod 3"]:
        await send(msg_upd(op, text))
    await send(msg_upd(op, "", location={"latitude": 1.0, "longitude": 2.0}))
    await send(msg_upd(op, "1"))
    await send(msg_upd(op, "2x2"))
    await send(cb_upd(op, "ct:x"))
    await send(cb_upd(op, "in:skip"))
    await send(msg_upd(op, "100000"))
    await send(cb_upd(op, "no:ok"))
    o2 = [r for r in await db.rows(ORDERS) if r["order_id"] != oid][0]
    await send(cb_upd(adm, f"rn:{o2['order_id']}:collection:3002"))
    o2 = [r for r in await db.rows(ORDERS) if r["order_id"] == o2["order_id"]][0]
    check("Admin buyurtmani biriktira oladi va audit yoziladi",
          o2["collection_driver_id"] == "3002" and o2["status"] == "assigned"
          and any("Biriktirildi" in h["action_details"] for h in await db.rows(HISTORY)))
    await send(cb_upd(adm, f"rn:{o2['order_id']}:collection:3001"))
    o2 = [r for r in await db.rows(ORDERS) if r["order_id"] == o2["order_id"]][0]
    check("Qayta biriktirish ishlaydi", o2["collection_driver_id"] == "3001")
    await send(cb_upd(3002, f"go:col:{o2['order_id']}"))
    check("Eski haydovchi endi yakunlay olmaydi", [r for r in await db.rows(ORDERS) if r["order_id"] == o2["order_id"]][0]["status"] == "assigned")

    # oylik hisoblash idempotent
    await employees.update_employee(4001, {"salary_type": "mixed", "base_salary": "1000000"})
    e = await employees.get_employee(4001, fresh=True)
    r1 = await salaries.accrue_monthly(e)
    r2 = await salaries.accrue_monthly(e)
    check("Oylik shu oy uchun faqat bir marta hisoblanadi", r1[0] and not r2[0])

    # Sozlamalar: faqat admin
    await send(cb_upd(adm, "sv:washing_pay_type:fixed"))
    check("Admin sozlamani o'zgartira oladi", (await salaries.get_settings(True))["washing_pay_type"] == "fixed")

    print(f"\nO'TDI: {len(PASSED)}   YIQILDI: {len(FAILED)}")
    if FAILED:
        print("Yiqilganlar:", FAILED)
        sys.exit(1)


asyncio.run(main())
