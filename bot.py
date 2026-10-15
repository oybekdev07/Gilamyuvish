"""Gilam yuvish botining asosiy fayli: barcha handlerlar (o'zbek lotin)."""
import asyncio
import json
import logging
import re
import uuid

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ErrorEvent, Message, ReplyKeyboardRemove, Update

import config
from keyboards import CANCEL, HOME_BTN, kb, location_kb, main_menu
from services import employees, orders, salaries
from services.constants import (FILTER_STATUSES, PAY_TYPES, REASSIGN, ROLE_LABELS, ROLE_TASK, SALARY_TYPES,
                                STAFF_ROLES, STAGES, STATUS_LABELS, TASK_LABELS)
from services.google_sheets import ensure_once
from services.store import KVStorage, get_kv
from services.utils import area_fmt, esc, money, num, now, now_str, parse_amount, short_ts
from states import AddEmp, EmpEdit, FinalPrice, NewOrder, Pay, Search, SetVal

log = logging.getLogger("bot")
router = Router()
dp = Dispatcher(storage=KVStorage(get_kv()))

ADMINS = ("owner", "admin")
OPS = ("owner", "admin", "operator")
DENIED = ("⛔ <b>Kechirasiz, sizda ruxsat yo'q.</b>\n\nBu bot faqat kompaniya xodimlari uchun.\n"
          "Agar siz xodim bo'lsangiz, quyidagi Telegram ID raqamingizni administratorga yuboring:\n\n<code>{id}</code>")


# =========================================================== umumiy yordamchilar
def is_admin(a) -> bool:
    return a["role"] in ADMINS


class Access(BaseMiddleware):
    """Har bir xabar/tugma uchun Telegram ID bo'yicha rol tekshiriladi."""

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is None:
            return None
        if isinstance(event, Message) and event.chat.type != "private":
            return None
        actor = await employees.get_actor(user.id, name=user.full_name)
        if actor is None:
            if isinstance(event, CallbackQuery):
                await event.answer("⛔ Ruxsat yo'q", show_alert=True)
            elif isinstance(event, Message):
                await event.answer(DENIED.format(id=user.id))
            return None
        data["actor"] = actor
        return await handler(event, data)


router.message.outer_middleware(Access())
router.callback_query.outer_middleware(Access())


async def show(ev, text: str, markup=None):
    text = text[:4000]
    if isinstance(ev, CallbackQuery):
        try:
            await ev.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except TelegramBadRequest as e:
            if "not modified" in str(e).lower():
                return
            await ev.message.answer(text, reply_markup=markup, disable_web_page_preview=True)
    else:
        await ev.answer(text, reply_markup=markup, disable_web_page_preview=True)


async def safe_send(bot: Bot, chat_id, text: str, markup=None) -> bool:
    for attempt in range(2):
        try:
            await bot.send_message(int(chat_id), text[:4000], reply_markup=markup, disable_web_page_preview=True)
            return True
        except TelegramRetryAfter as e:
            await asyncio.sleep(min(e.retry_after, 3))
        except TelegramAPIError as e:
            log.warning("Xabar yuborilmadi (%s): %s", chat_id, type(e).__name__)
            return False
    return False


async def notify_admins(bot: Bot, text: str):
    for aid in await employees.admin_ids():
        await safe_send(bot, aid, text)


def menu_text(actor) -> str:
    return (f"👋 Assalomu alaykum, <b>{esc(actor['full_name'])}</b>!\n"
            f"Rolingiz: <b>{ROLE_LABELS.get(actor['role'], actor['role'])}</b>\n\nKerakli bo'limni tanlang:")


def parse_nonneg(text: str):
    t = (text or "").replace(" ", "").replace(",", ".")
    return float(t) if re.fullmatch(r"\d+(\.\d+)?", t) else None


# =========================================================== buyurtma kartochkasi
def dims_lines(o) -> str:
    try:
        dims = json.loads(o.get("dimensions_json") or "[]")
    except ValueError:
        dims = []
    return "\n".join(
        f"   {i}) {area_fmt(d['length'])} × {area_fmt(d['width'])} = {area_fmt(d['area'])} m²"
        for i, d in enumerate(dims, 1))


def map_link(o) -> str:
    if o.get("latitude") not in ("", None) and o.get("longitude") not in ("", None):
        return f"\n🗺 <a href=\"https://maps.google.com/?q={o['latitude']},{o['longitude']}\">Xaritada ochish</a>"
    return ""


async def card_text(o: dict, actor: dict, intro: str = "") -> str:
    role, me, s = actor["role"], str(actor["telegram_id"]), o["status"]
    t = f"{intro}📋 <b>{esc(o['order_id'])}</b>\n{STATUS_LABELS.get(s, s)}\n\n"
    t += f"🧺 Gilamlar: <b>{o['carpet_count']} ta</b>\n{dims_lines(o)}\n"
    t += f"📐 Jami maydon: <b>{area_fmt(o['total_area'])} m²</b>\n"
    if o.get("carpet_type"):
        t += f"🏷 Turi: {esc(o['carpet_type'])}\n"
    if o.get("instructions"):
        t += f"📝 Izoh: {esc(o['instructions'])}\n"

    full = role in OPS
    mine_col = {"collector": "collection_driver_id", "deliverer": "delivery_driver_id"}.get(role)
    mine = bool(mine_col) and str(o.get(mine_col)) == me
    if full or mine:
        t += (f"\n👤 Mijoz: <b>{esc(o['customer_name'])}</b>\n📞 Telefon: <code>{esc(o['phone'])}</code>\n"
              f"📍 Manzil: {esc(o['pickup_address'])}{map_link(o)}\n")
    elif role in ("collector", "deliverer"):
        t += f"\n📍 Manzil: {esc(o['pickup_address'])}\n<i>Mijoz aloqasi qabul qilingandan keyin ko'rinadi.</i>\n"

    if full:
        names = await employees.name_map()
        t += f"\n💰 Taxminiy narx: {money(o['estimated_price'])}"
        if o.get("final_price"):
            t += f"\n💵 Yakuniy narx: <b>{money(o['final_price'])}</b>"
        t += "\n"
        for label, col, comm in (("Olib ketdi", "collection_driver_id", "collection_commission"),
                                 ("Yuvdi", "washing_employee_id", "washing_payment"),
                                 ("Pardozladi", "finishing_employee_id", "finishing_payment"),
                                 ("Yetkazdi", "delivery_driver_id", "delivery_commission")):
            if o.get(col):
                extra = f" — {money(o[comm])}" if o.get(comm) not in ("", None) else ""
                t += f"• {label}: {esc(names.get(str(o[col]), o[col]))}{extra}\n"
        t += (f"\n🕓 Yaratildi: {short_ts(o['created_at'])}"
              f"{' · Yetkazildi: ' + short_ts(o['delivered_at']) if o.get('delivered_at') else ''}\n")
    if role in ROLE_TASK:
        t += "\n" + await salaries.commission_text(role, o, actor) + "\n"
    return t


def card_kb(o: dict, actor: dict):
    role, me, s, oid = actor["role"], str(actor["telegram_id"]), o["status"], o["order_id"]
    rows = []
    for act, st in STAGES.items():
        if st["role"] != role or st["frm"] != s:
            continue
        if st["claim"]:
            rows.append([(st["btn"], f"go:{act}:{oid}")])
        elif str(o.get(st["who"])) == me:
            rows.append([(st["btn"], f"cf:{act}:{oid}")])
    if is_admin(actor):
        rows.append([("📜 Tarix", f"h:{oid}"), ("🔁 Biriktirish", f"ra:{oid}")])
        extra = [("💵 Yakuniy narx", f"fp:{oid}")]
        if s not in ("delivered", "cancelled"):
            extra.append(("🚫 Bekor qilish", f"cx:{oid}"))
        rows.append(extra)
    rows.append([HOME_BTN])
    return kb(rows)


def can_view(o: dict, actor: dict) -> bool:
    if actor["role"] in OPS:
        return True
    for st in STAGES.values():
        if st["role"] == actor["role"]:
            if o["status"] == st["frm"] and st["claim"]:
                return True
            if str(o.get(st["who"])) == str(actor["telegram_id"]):
                return True
    return False


def olabel(o) -> str:
    emoji = STATUS_LABELS.get(o["status"], "•").split()[0]
    return f"{emoji} {o['order_id']} · {o['carpet_count']} ta · {area_fmt(o['total_area'])} m²"


def page_kb(items: list, page: int, base: str, back: str, per: int = 8):
    chunk = items[page * per:(page + 1) * per]
    rows = [[(olabel(o), f"o:{o['order_id']}")] for o in chunk]
    nav = []
    if page > 0:
        nav.append(("⬅️", f"{base}:{page - 1}"))
    if (page + 1) * per < len(items):
        nav.append(("➡️", f"{base}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([("⬅️ Orqaga", back), HOME_BTN])
    return kb(rows)


# =========================================================== buyruqlar va bosh menyu
@router.message(CommandStart())
@router.message(Command("menu", "help"))
async def cmd_start(m: Message, actor: dict, state: FSMContext):
    await state.clear()
    await m.answer(menu_text(actor), reply_markup=main_menu(actor["role"]))


@router.message(Command("cancel"))
async def cmd_cancel(m: Message, actor: dict, state: FSMContext):
    await state.clear()
    await m.answer("❌ Bekor qilindi.", reply_markup=ReplyKeyboardRemove())
    await m.answer(menu_text(actor), reply_markup=main_menu(actor["role"]))


@router.callback_query(F.data == "m:home")
async def cb_home(cb: CallbackQuery, actor: dict, state: FSMContext):
    await cb.answer()
    await state.clear()
    await show(cb, menu_text(actor), main_menu(actor["role"]))


@router.callback_query(F.data == "x:cancel")
async def cb_cancel(cb: CallbackQuery, actor: dict, state: FSMContext):
    await cb.answer("Bekor qilindi")
    await state.clear()
    await show(cb, "❌ Bekor qilindi.\n\n" + menu_text(actor), main_menu(actor["role"]))


# =========================================================== xodimlar uchun ro'yxatlar
LIST_MENU = {
    "cnew": ("collector", ["new"], None, "📦 Yangi olib ketish buyurtmalari"),
    "ctasks": ("collector", ["assigned"], "collection_driver_id", "🚚 Mening vazifalarim"),
    "wnew": ("washer", ["collected"], None, "🧼 Yuvish uchun buyurtmalar"),
    "wprog": ("washer", ["washing"], "washing_employee_id", "⚙️ Jarayondagi ishlar"),
    "fnew": ("finisher", ["washed"], None, "✨ Pardozlash buyurtmalari"),
    "fprog": ("finisher", ["finishing"], "finishing_employee_id", "⚙️ Jarayondagi ishlar"),
    "dnew": ("deliverer", ["ready"], None, "🚛 Yetkazib berish buyurtmalari"),
    "dtasks": ("deliverer", ["delivering"], "delivery_driver_id", "📋 Mening yetkazmalarim"),
}


@router.callback_query(F.data.regexp(r"^m:(cnew|ctasks|wnew|wprog|fnew|fprog|dnew|dtasks)$"))
async def cb_task_list(cb: CallbackQuery, actor: dict):
    key = cb.data.split(":")[1]
    role, statuses, who_col, title = LIST_MENU[key]
    if actor["role"] != role:
        return await cb.answer("⛔ Ruxsat yo'q", show_alert=True)
    await cb.answer()
    items = await orders.list_orders(statuses, who_col, actor["telegram_id"])
    if not items:
        return await show(cb, f"{title}\n\nHozircha buyurtma yo'q.", kb([[HOME_BTN]]))
    await show(cb, f"{title}\n\nJami: <b>{len(items)}</b> ta. Birini tanlang:", page_kb(items[:15], 0, "nop", "m:home", per=15))


@router.callback_query(F.data == "nop")
async def cb_nop(cb: CallbackQuery):
    await cb.answer()


@router.callback_query(F.data.startswith("o:"))
async def cb_order(cb: CallbackQuery, actor: dict):
    o = await orders.get_order(cb.data[2:])
    if not o:
        return await cb.answer("Buyurtma topilmadi", show_alert=True)
    if not can_view(o, actor):
        return await cb.answer("⛔ Bu buyurtma sizga ko'rinmaydi", show_alert=True)
    await cb.answer()
    await show(cb, await card_text(o, actor), card_kb(o, actor))


# =========================================================== bosqichlar (qabul qilish / yakunlash)
@router.callback_query(F.data.startswith("cf:"))
async def cb_confirm(cb: CallbackQuery, actor: dict):
    _, act, oid = cb.data.split(":", 2)
    st = STAGES.get(act)
    if not st or st["role"] != actor["role"]:
        return await cb.answer("⛔ Ruxsat yo'q", show_alert=True)
    await cb.answer()
    await show(cb, f"❓ <b>{esc(oid)}</b>\n\n{st['ask']}",
               kb([[("✅ Ha, tasdiqlayman", f"go:{act}:{oid}")], [("⬅️ Orqaga", f"o:{oid}")]]))


async def notify_role(bot: Bot, role: str, o: dict, intro: str) -> tuple[int, int]:
    emps = await employees.active_by_role(role)
    sent = 0
    for e in emps:
        text = await card_text(o, e, intro=intro)
        if await safe_send(bot, e["telegram_id"], text, card_kb(o, e)):
            sent += 1
    return sent, len(emps)


async def after_stage(bot: Bot, act: str, o: dict, actor: dict):
    who, oid = esc(actor["full_name"]), esc(o["order_id"])
    if act == "col":
        sent, total = await notify_role(bot, "washer", o, "🧼 <b>Yuvish uchun yangi buyurtma!</b>\n\n")
        warn = "" if sent else "\n⚠️ Faol yuvuvchi xodimga xabar yetmadi!"
        await notify_admins(bot, f"📦 <b>{oid}</b> mijozdan olib kelindi.\nHaydovchi: {who}\n"
                                 f"Yuvuvchilarga yuborildi: {sent}/{total}{warn}")
    elif act == "wdn":
        sent, total = await notify_role(bot, "finisher", o, "✨ <b>Pardozlash uchun yangi buyurtma!</b>\n\n")
        if not sent:
            await notify_admins(bot, f"⚠️ <b>{oid}</b> yuvildi, lekin faol pardozlovchi xodimga xabar yetmadi.")
    elif act == "fdn":
        sent, total = await notify_role(bot, "deliverer", o, "🚛 <b>Yetkazib berish uchun buyurtma tayyor!</b>\n\n")
        await notify_admins(bot, f"✅ <b>{oid}</b> pardozlandi va yetkazishga tayyor.\n"
                                 f"Haydovchilarga yuborildi: {sent}/{total}")
    elif act == "ddn":
        text = f"🏁 <b>{oid}</b> mijozga yetkazib berildi.\nHaydovchi: {who}"
        await notify_admins(bot, text)
        if o.get("created_by") and int(o["created_by"]) not in await employees.admin_ids():
            await safe_send(bot, o["created_by"], text)


@router.callback_query(F.data.startswith("go:"))
async def cb_go(cb: CallbackQuery, actor: dict, bot: Bot):
    _, act, oid = cb.data.split(":", 2)
    res = await orders.advance(oid, act, actor)
    if not res.ok:
        await cb.answer(res.msg, show_alert=True)
        return
    await cb.answer("✅ Bajarildi")
    o = res.order
    await show(cb, await card_text(o, actor, intro=res.msg + "\n\n"), card_kb(o, actor))
    await after_stage(bot, act, o, actor)


# =========================================================== operator: yangi buyurtma (FSM)
@router.callback_query(F.data == "m:newo")
async def newo_start(cb: CallbackQuery, actor: dict, state: FSMContext):
    if actor["role"] not in OPS:
        return await cb.answer("⛔ Ruxsat yo'q", show_alert=True)
    await cb.answer()
    await state.clear()
    await state.set_state(NewOrder.name)
    await cb.message.answer("🧾 <b>Yangi buyurtma</b>\n\n1/9 👤 Mijozning to'liq ismini kiriting:", reply_markup=CANCEL)


@router.message(NewOrder.name, F.text)
async def no_name(m: Message, state: FSMContext):
    name = m.text.strip()
    if not 2 <= len(name) <= 80:
        return await m.answer("❗ Iltimos, to'g'ri ism kiriting (2–80 belgi).", reply_markup=CANCEL)
    await state.update_data(name=name)
    await state.set_state(NewOrder.phone)
    await m.answer("2/9 📞 Mijozning telefon raqami:\nMasalan: <code>+998901234567</code>", reply_markup=CANCEL)


def norm_phone(text: str):
    digits = re.sub(r"[^\d]", "", text or "")
    if len(digits) == 9:
        digits = "998" + digits
    return "+" + digits if 11 <= len(digits) <= 15 else None


@router.message(NewOrder.phone, F.text)
async def no_phone(m: Message, state: FSMContext):
    phone = norm_phone(m.text)
    if not phone:
        return await m.answer("❗ Telefon raqami noto'g'ri. Masalan: <code>+998901234567</code>", reply_markup=CANCEL)
    await state.update_data(phone=phone)
    await state.set_state(NewOrder.address)
    await m.answer("3/9 📍 Olib ketish manzilini kiriting (ko'cha, uy, mo'ljal):", reply_markup=CANCEL)


@router.message(NewOrder.address, F.text)
async def no_address(m: Message, state: FSMContext):
    addr = m.text.strip()
    if len(addr) < 5 or len(addr) > 300:
        return await m.answer("❗ Manzil juda qisqa. Batafsilroq kiriting.", reply_markup=CANCEL)
    await state.update_data(address=addr)
    await state.set_state(NewOrder.location)
    await m.answer("4/9 🗺 Xohlasangiz, joylashuvni yuboring (ixtiyoriy):", reply_markup=location_kb())


async def ask_count(m: Message, state: FSMContext):
    await state.set_state(NewOrder.count)
    await m.answer("✅ Qabul qilindi.", reply_markup=ReplyKeyboardRemove())
    await m.answer("5/9 🧺 Gilamlar sonini kiriting (1–30):", reply_markup=CANCEL)


@router.message(NewOrder.location, F.location)
async def no_location(m: Message, state: FSMContext):
    await state.update_data(lat=m.location.latitude, lon=m.location.longitude)
    await ask_count(m, state)


@router.message(NewOrder.location, F.text == "⏭ O'tkazib yuborish")
async def no_location_skip(m: Message, state: FSMContext):
    await ask_count(m, state)


@router.message(NewOrder.location)
async def no_location_bad(m: Message):
    await m.answer("Joylashuvni tugma orqali yuboring yoki «⏭ O'tkazib yuborish» ni bosing. Bekor qilish: /cancel",
                   reply_markup=location_kb())


@router.message(NewOrder.count, F.text)
async def no_count(m: Message, state: FSMContext):
    if not m.text.strip().isdigit() or not 1 <= int(m.text.strip()) <= 30:
        return await m.answer("❗ 1 dan 30 gacha butun son kiriting.", reply_markup=CANCEL)
    await state.update_data(count=int(m.text.strip()), dims=[])
    await state.set_state(NewOrder.dims)
    await m.answer("6/9 📐 <b>1-gilam</b> uzunligi va enini metrda kiriting.\nMasalan: <code>3x2</code> yoki <code>2.5 x 1.8</code>",
                   reply_markup=CANCEL)


DIM_RE = re.compile(r"\s*(\d+(?:[.,]\d+)?)\s*(?:[xXхХ×*]|\s)\s*(\d+(?:[.,]\d+)?)\s*")
CTYPES = ["Oddiy", "Jun", "Ipak", "Sintetik", "Boshqa"]


@router.message(NewOrder.dims, F.text)
async def no_dims(m: Message, state: FSMContext):
    mt = DIM_RE.fullmatch(m.text)
    if not mt:
        return await m.answer("❗ Format: <code>uzunlik x eni</code>, masalan <code>3x2</code>", reply_markup=CANCEL)
    length, width = float(mt[1].replace(",", ".")), float(mt[2].replace(",", "."))
    if not (0 < length <= 100 and 0 < width <= 100):
        return await m.answer("❗ O'lchamlar 0 dan katta va 100 m dan kichik bo'lishi kerak.", reply_markup=CANCEL)
    d = await state.get_data()
    dims = d["dims"] + [[length, width]]
    await state.update_data(dims=dims)
    done = len(dims)
    line = f"✅ {done}-gilam: {area_fmt(length)} × {area_fmt(width)} = {area_fmt(length * width)} m²"
    if done < d["count"]:
        return await m.answer(f"{line}\n\n📐 <b>{done + 1}-gilam</b> uzunligi va enini kiriting (masalan <code>3x2</code>):",
                              reply_markup=CANCEL)
    total = sum(a * b for a, b in dims)
    await state.set_state(NewOrder.ctype)
    rows = [[(c, f"ct:{i}")] for i, c in enumerate(CTYPES)] + [[("⏭ Aniq emas", "ct:x")], [("❌ Bekor qilish", "x:cancel")]]
    await m.answer(f"{line}\n📐 Jami maydon: <b>{area_fmt(total)} m²</b>\n\n7/9 🏷 Gilam turini tanlang:", reply_markup=kb(rows))


@router.callback_query(NewOrder.ctype, F.data.startswith("ct:"))
async def no_ctype(cb: CallbackQuery, state: FSMContext):
    v = cb.data[3:]
    await cb.answer()
    await state.update_data(ctype="" if v == "x" else CTYPES[int(v)])
    await state.set_state(NewOrder.instr)
    await cb.message.answer("8/9 📝 Qo'shimcha izoh (masalan: dog' bor, nozik mato). Kerak bo'lmasa tugmani bosing:",
                            reply_markup=kb([[("⏭ Izohsiz davom etish", "in:skip")], [("❌ Bekor qilish", "x:cancel")]]))


async def ask_price(target: Message, state: FSMContext):
    d = await state.get_data()
    total = sum(a * b for a, b in d["dims"])
    ppm = num((await salaries.get_settings()).get("price_per_m2"))
    rows = []
    text = "9/9 💰 Taxminiy xizmat narxini kiriting (so'mda). Masalan: <code>150000</code>"
    if ppm > 0:
        sug = round(total * ppm)
        await state.update_data(sug=sug)
        rows.append([(f"💡 {money(sug)} (maydon × narx)", "pr:sug")])
        text += f"\n\n💡 Tavsiya: {area_fmt(total)} m² × {money(ppm)} = <b>{money(sug)}</b>"
    rows.append([("❌ Bekor qilish", "x:cancel")])
    await state.set_state(NewOrder.price)
    await target.answer(text, reply_markup=kb(rows))


@router.callback_query(NewOrder.instr, F.data == "in:skip")
async def no_instr_skip(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.update_data(instr="")
    await ask_price(cb.message, state)


@router.message(NewOrder.instr, F.text)
async def no_instr(m: Message, state: FSMContext):
    await state.update_data(instr=m.text.strip()[:500])
    await ask_price(m, state)


def order_summary(d: dict) -> str:
    total = sum(a * b for a, b in d["dims"])
    lines = "\n".join(f"   {i}) {area_fmt(a)} × {area_fmt(b)} = {area_fmt(a * b)} m²" for i, (a, b) in enumerate(d["dims"], 1))
    return (f"🧾 <b>Buyurtma xulosasi</b>\n\n👤 {esc(d['name'])}\n📞 {esc(d['phone'])}\n📍 {esc(d['address'])}"
            f"{' (joylashuv bor 🗺)' if d.get('lat') is not None else ''}\n\n🧺 Gilamlar: <b>{len(d['dims'])} ta</b>\n{lines}\n"
            f"📐 Jami: <b>{area_fmt(total)} m²</b>\n🏷 Turi: {esc(d.get('ctype') or '—')}\n"
            f"📝 Izoh: {esc(d.get('instr') or '—')}\n💰 Taxminiy narx: <b>{money(d['price'])}</b>\n\nSaqlaymizmi?")


async def go_confirm(target: Message, state: FSMContext):
    await state.update_data(token=uuid.uuid4().hex[:16])
    await state.set_state(NewOrder.confirm)
    d = await state.get_data()
    await target.answer(order_summary(d), reply_markup=kb([[("✅ Tasdiqlash va saqlash", "no:ok")], [("❌ Bekor qilish", "x:cancel")]]))


@router.callback_query(NewOrder.price, F.data == "pr:sug")
async def no_price_sug(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    await cb.answer()
    await state.update_data(price=d["sug"])
    await go_confirm(cb.message, state)


@router.message(NewOrder.price, F.text)
async def no_price(m: Message, state: FSMContext):
    price = parse_amount(m.text)
    if price is None:
        return await m.answer("❗ Narx musbat son bo'lishi kerak. Masalan: <code>150000</code>", reply_markup=CANCEL)
    await state.update_data(price=price)
    await go_confirm(m, state)


@router.callback_query(NewOrder.confirm, F.data == "no:ok")
async def no_save(cb: CallbackQuery, actor: dict, state: FSMContext, bot: Bot):
    d = await state.get_data()
    if actor["role"] not in OPS:
        return await cb.answer("⛔ Ruxsat yo'q", show_alert=True)
    kv = get_kv()
    if not await kv.set(f"mk:{d.get('token')}", "1", nx=True, ex=86400):
        return await cb.answer("Bu buyurtma allaqachon saqlanmoqda/saqlangan.", show_alert=True)
    await cb.answer("Saqlanmoqda...")
    try:
        o = await orders.create_order(d, actor)
    except Exception:
        await kv.delete(f"mk:{d.get('token')}")
        log.exception("Buyurtmani saqlashda xatolik")
        await cb.message.answer("⚠️ Saqlab bo'lmadi (Google Sheets band bo'lishi mumkin). Qayta «Tasdiqlash» ni bosing.")
        return
    await state.clear()
    sent, total = await notify_role(bot, "collector", o, "🆕 <b>Yangi buyurtma!</b>\n\n")
    note = f"📨 Olib ketuvchi haydovchilarga yuborildi: {sent}/{total}"
    if sent == 0:
        note += "\n⚠️ Faol haydovchiga xabar yetmadi — administratorga xabar berildi."
        await notify_admins(bot, f"⚠️ <b>{esc(o['order_id'])}</b> yaratildi, lekin birorta haydovchiga xabar yetmadi.")
    await show(cb, f"✅ <b>Buyurtma saqlandi!</b>\nRaqami: <code>{esc(o['order_id'])}</code>\n\n{note}",
               kb([[("📋 Buyurtmani ochish", f"o:{o['order_id']}")], [HOME_BTN]]))


# =========================================================== operator: mening buyurtmalarim / qidirish
@router.callback_query(F.data == "m:myo")
@router.callback_query(F.data.regexp(r"^my:\d+$"))
async def cb_my(cb: CallbackQuery, actor: dict):
    if actor["role"] not in OPS:
        return await cb.answer("⛔ Ruxsat yo'q", show_alert=True)
    await cb.answer()
    page = int(cb.data.split(":")[1]) if cb.data.startswith("my:") else 0
    items = await orders.list_orders(created_by=actor["telegram_id"])
    if not items:
        return await show(cb, "📋 Siz hali buyurtma kiritmagansiz.", kb([[HOME_BTN]]))
    await show(cb, f"📋 <b>Buyurtmalarim</b> ({len(items)} ta):", page_kb(items, page, "my", "m:home"))


@router.callback_query(F.data == "m:srch")
async def cb_search(cb: CallbackQuery, actor: dict, state: FSMContext):
    if actor["role"] not in OPS:
        return await cb.answer("⛔ Ruxsat yo'q", show_alert=True)
    await cb.answer()
    await state.clear()
    await state.set_state(Search.query)
    await cb.message.answer("🔍 Buyurtma raqami, mijoz ismi yoki telefon raqamini yuboring:", reply_markup=CANCEL)


@router.message(Search.query, F.text)
async def search_run(m: Message, actor: dict, state: FSMContext):
    if actor["role"] not in OPS:
        return
    await state.clear()
    items = await orders.search_orders(m.text)
    if not items:
        return await m.answer("😕 Hech narsa topilmadi.", reply_markup=kb([[("🔍 Qayta qidirish", "m:srch")], [HOME_BTN]]))
    await m.answer(f"🔍 Topildi: <b>{len(items)}</b> ta", reply_markup=page_kb(items, 0, "nop", "m:home", per=15))


# =========================================================== admin: buyurtmalar
async def need_admin(cb: CallbackQuery, actor: dict) -> bool:
    if not is_admin(actor):
        await cb.answer("⛔ Faqat administrator uchun", show_alert=True)
        return False
    return True


@router.callback_query(F.data == "a:orders")
async def a_orders(cb: CallbackQuery, actor: dict):
    if not await need_admin(cb, actor):
        return
    await cb.answer()
    rows = [[(STATUS_LABELS[s], f"al:{s}:0")] for s in FILTER_STATUSES]
    rows = [rows[i][0:1] + (rows[i + 1][0:1] if i + 1 < len(rows) else []) for i in range(0, len(rows), 2)]
    rows += [[("📋 Barchasi", "al:all:0"), ("🔍 Qidirish", "m:srch")], [HOME_BTN]]
    await show(cb, "📦 <b>Buyurtmalar</b>\nHolat bo'yicha filtrlang:", kb(rows))


@router.callback_query(F.data.regexp(r"^al:[a-z]+:\d+$"))
async def a_list(cb: CallbackQuery, actor: dict):
    if not await need_admin(cb, actor):
        return
    await cb.answer()
    _, status, page = cb.data.split(":")
    items = await orders.list_orders(None if status == "all" else [status])
    title = "Barchasi" if status == "all" else STATUS_LABELS.get(status, status)
    if not items:
        return await show(cb, f"📦 {title}\n\nBuyurtma yo'q.", kb([[("⬅️ Orqaga", "a:orders"), HOME_BTN]]))
    await show(cb, f"📦 <b>{title}</b> — jami {len(items)} ta", page_kb(items, int(page), f"al:{status}", "a:orders"))


@router.callback_query(F.data.startswith("h:"))
async def a_history(cb: CallbackQuery, actor: dict):
    if not await need_admin(cb, actor):
        return
    await cb.answer()
    oid = cb.data[2:]
    rows = await orders.history_of(oid)
    lines = [f"• {short_ts(h['timestamp'])} — {STATUS_LABELS.get(h['previous_status'], h['previous_status'])} → "
             f"{STATUS_LABELS.get(h['new_status'], h['new_status'])}\n   👤 {esc(h['employee_name'])} · {esc(h['action_details'])}"
             for h in rows[-20:]]
    await show(cb, f"📜 <b>{esc(oid)}</b> tarixi:\n\n" + ("\n".join(lines) or "Yozuv yo'q."),
               kb([[("⬅️ Orqaga", f"o:{oid}"), HOME_BTN]]))


@router.callback_query(F.data.startswith("cx:"))
async def a_cancel_ask(cb: CallbackQuery, actor: dict):
    if not await need_admin(cb, actor):
        return
    await cb.answer()
    oid = cb.data[3:]
    await show(cb, f"🚫 <b>{esc(oid)}</b> buyurtmasini bekor qilishni tasdiqlaysizmi?",
               kb([[("✅ Ha, bekor qilish", f"cxy:{oid}")], [("⬅️ Orqaga", f"o:{oid}")]]))


@router.callback_query(F.data.startswith("cxy:"))
async def a_cancel_do(cb: CallbackQuery, actor: dict):
    if not await need_admin(cb, actor):
        return
    res = await orders.cancel_order(cb.data[4:], actor)
    await cb.answer(res.msg, show_alert=not res.ok)
    if res.ok:
        await show(cb, await card_text(res.order, actor, "🚫 Bekor qilindi.\n\n"), card_kb(res.order, actor))


@router.callback_query(F.data.startswith("fp:"))
async def a_fp_start(cb: CallbackQuery, actor: dict, state: FSMContext):
    if not await need_admin(cb, actor):
        return
    await cb.answer()
    await state.clear()
    await state.set_state(FinalPrice.value)
    await state.update_data(oid=cb.data[3:])
    await cb.message.answer("💵 Yakuniy narxni kiriting (so'mda):", reply_markup=CANCEL)


@router.message(FinalPrice.value, F.text)
async def a_fp_save(m: Message, actor: dict, state: FSMContext):
    price = parse_amount(m.text)
    if price is None:
        return await m.answer("❗ Musbat son kiriting.", reply_markup=CANCEL)
    d = await state.get_data()
    res = await orders.set_final_price(d["oid"], price, actor)
    await state.clear()
    if res.ok:
        await m.answer("✅ " + res.msg, reply_markup=kb([[("📋 Buyurtma", f"o:{d['oid']}")], [HOME_BTN]]))
    else:
        await m.answer("⚠️ " + res.msg)


# ---- biriktirish / qayta biriktirish
@router.callback_query(F.data.startswith("ra:"))
async def a_reassign(cb: CallbackQuery, actor: dict):
    if not await need_admin(cb, actor):
        return
    await cb.answer()
    oid = cb.data[3:]
    o = await orders.get_order(oid)
    if not o:
        return await cb.answer("Topilmadi", show_alert=True)
    rows = [[(f"{TASK_LABELS[stage]} ({ROLE_LABELS[cfg['role']]})", f"rs:{oid}:{stage}")]
            for stage, cfg in REASSIGN.items() if o["status"] in cfg["statuses"]]
    if not rows:
        return await show(cb, "ℹ️ Bu buyurtma uchun biriktirish mumkin emas (yakunlangan yoki bekor qilingan).",
                          kb([[("⬅️ Orqaga", f"o:{oid}")]]))
    await show(cb, f"🔁 <b>{esc(oid)}</b> — qaysi bosqich uchun xodim biriktiramiz?", kb(rows + [[("⬅️ Orqaga", f"o:{oid}")]]))


@router.callback_query(F.data.startswith("rs:"))
async def a_reassign_stage(cb: CallbackQuery, actor: dict):
    if not await need_admin(cb, actor):
        return
    await cb.answer()
    _, oid, stage = cb.data.split(":")
    emps = await employees.active_by_role(REASSIGN[stage]["role"])
    if not emps:
        return await show(cb, "😕 Bu rolda faol xodim yo'q.", kb([[("⬅️ Orqaga", f"ra:{oid}")]]))
    rows = [[(e["full_name"], f"rn:{oid}:{stage}:{e['telegram_id']}")] for e in emps[:20]]
    await show(cb, "👤 Xodimni tanlang:", kb(rows + [[("⬅️ Orqaga", f"ra:{oid}")]]))


@router.callback_query(F.data.startswith("rn:"))
async def a_reassign_do(cb: CallbackQuery, actor: dict, bot: Bot):
    if not await need_admin(cb, actor):
        return
    _, oid, stage, tid = cb.data.split(":")
    emp = await employees.get_employee(tid, fresh=True)
    if not emp:
        return await cb.answer("Xodim topilmadi", show_alert=True)
    res = await orders.reassign(oid, stage, emp, actor)
    if not res.ok:
        return await cb.answer(res.msg, show_alert=True)
    await cb.answer("✅ Biriktirildi")
    o = dict(res.order)
    old = o.pop("_old", "")
    await show(cb, await card_text(o, actor, "✅ Biriktirildi.\n\n"), card_kb(o, actor))
    await safe_send(bot, emp["telegram_id"], await card_text(o, emp, "📌 <b>Sizga buyurtma biriktirildi!</b>\n\n"),
                    card_kb(o, emp))
    if old and old != str(emp["telegram_id"]):
        await safe_send(bot, old, f"ℹ️ <b>{esc(oid)}</b> buyurtmasi administrator tomonidan boshqa xodimga o'tkazildi.")


# =========================================================== admin: xodimlar
def can_manage(actor: dict, e: dict) -> bool:
    if not is_admin(actor) or int(e["telegram_id"]) in config.OWNER_IDS:
        return False
    return not (e.get("role") == "admin" and actor["role"] != "owner")


def emp_text(e: dict, counts: dict | None = None) -> str:
    on = employees.is_active(e)
    t = (f"👤 <b>{esc(e['full_name'])}</b>\n🆔 <code>{e['telegram_id']}</code>"
         f"{' · @' + esc(e['username']) if e.get('username') else ''}\n"
         f"🎭 Rol: <b>{ROLE_LABELS.get(e['role'], e['role'])}</b>\n{'🟢 Faol' if on else '🔴 Faol emas'}\n"
         f"💼 Maosh turi: {SALARY_TYPES.get(e.get('salary_type'), e.get('salary_type') or '—')}\n"
         f"💵 Oylik: {money(e.get('base_salary') or 0)}\n")
    if str(e.get("commission_value", "")).strip() != "":
        t += f"📈 Maxsus komissiya: {e['commission_value']} ({PAY_TYPES.get(e.get('commission_type'), 'standart tur')})\n"
    else:
        t += "📈 Komissiya: standart (Sozlamalardan)\n"
    if counts:
        t += (f"\n✅ Bajarilgan: olib ketish {counts['collection']}, yuvish {counts['washing']}, "
              f"pardozlash {counts['finishing']}, yetkazish {counts['delivery']}")
    return t


def emp_kb(e: dict):
    tid = e["telegram_id"]
    on = employees.is_active(e)
    return kb([
        [("🔴 O'chirish" if on else "🟢 Faollashtirish", f"et:{tid}"), ("🎭 Rolni o'zgartirish", f"er:{tid}")],
        [("💼 Maosh turi", f"es:{tid}"), ("💵 Oylik summasi", f"eb:{tid}")],
        [("📈 Komissiya", f"ec:{tid}"), ("🕘 Vazifalar tarixi", f"eh:{tid}")],
        [("💰 Hisob-kitob", f"p:{tid}")],
        [("⬅️ Orqaga", "a:emps"), HOME_BTN],
    ])


@router.callback_query(F.data == "a:emps")
async def a_emps(cb: CallbackQuery, actor: dict, state: FSMContext):
    if not await need_admin(cb, actor):
        return
    await cb.answer()
    await state.clear()
    emps = await employees.all_employees(True)
    rows = [[(f"{'🟢' if employees.is_active(e) else '🔴'} {e['full_name']} · {ROLE_LABELS.get(e['role'], e['role'])}",
              f"e:{e['telegram_id']}")] for e in emps[:30]]
    rows.append([("➕ Xodim qo'shish", "e:add")])
    rows.append([HOME_BTN])
    await show(cb, f"👥 <b>Xodimlar</b> ({len(emps)} ta)", kb(rows))


@router.callback_query(F.data.regexp(r"^e:\d+$"))
async def a_emp_card(cb: CallbackQuery, actor: dict):
    if not await need_admin(cb, actor):
        return
    e = await employees.get_employee(cb.data[2:], fresh=True)
    if not e:
        return await cb.answer("Topilmadi", show_alert=True)
    await cb.answer()
    await show(cb, emp_text(e, await orders.employee_counts(e["telegram_id"])), emp_kb(e))


async def load_manageable(cb: CallbackQuery, actor: dict, tid: str):
    e = await employees.get_employee(tid, fresh=True)
    if not e:
        await cb.answer("Xodim topilmadi", show_alert=True)
        return None
    if not can_manage(actor, e):
        await cb.answer("⛔ Bu xodimni o'zgartirishga ruxsatingiz yo'q", show_alert=True)
        return None
    return e


async def refresh_emp(cb: CallbackQuery, tid: str, note: str = ""):
    e = await employees.get_employee(tid, fresh=True)
    await show(cb, note + emp_text(e, await orders.employee_counts(tid)), emp_kb(e))


@router.callback_query(F.data.startswith("et:"))
async def a_emp_toggle(cb: CallbackQuery, actor: dict):
    e = await load_manageable(cb, actor, cb.data[3:]) if is_admin(actor) else None
    if not e:
        return
    new = "FALSE" if employees.is_active(e) else "TRUE"
    await employees.update_employee(e["telegram_id"], {"active": new})
    await cb.answer("Saqlandi")
    await refresh_emp(cb, e["telegram_id"], "✅ Holat o'zgartirildi.\n\n")


@router.callback_query(F.data.startswith("er:"))
async def a_emp_role(cb: CallbackQuery, actor: dict):
    e = await load_manageable(cb, actor, cb.data[3:]) if is_admin(actor) else None
    if not e:
        return
    await cb.answer()
    roles = [r for r in STAFF_ROLES if r != "admin" or actor["role"] == "owner"]
    await show(cb, "🎭 Yangi rolni tanlang:", kb([[(ROLE_LABELS[r], f"ers:{e['telegram_id']}:{r}")] for r in roles]
                                                 + [[("⬅️ Orqaga", f"e:{e['telegram_id']}")]]))


@router.callback_query(F.data.startswith("ers:"))
async def a_emp_role_set(cb: CallbackQuery, actor: dict):
    _, tid, role = cb.data.split(":")
    e = await load_manageable(cb, actor, tid) if is_admin(actor) else None
    if not e:
        return
    if role not in STAFF_ROLES or (role == "admin" and actor["role"] != "owner"):
        return await cb.answer("⛔ Bu rolni faqat Ega bera oladi", show_alert=True)
    await employees.update_employee(tid, {"role": role})
    await cb.answer("Saqlandi")
    await refresh_emp(cb, tid, "✅ Rol o'zgartirildi.\n\n")


@router.callback_query(F.data.startswith("es:"))
async def a_emp_stype(cb: CallbackQuery, actor: dict):
    e = await load_manageable(cb, actor, cb.data[3:]) if is_admin(actor) else None
    if not e:
        return
    await cb.answer()
    await show(cb, "💼 Maosh turini tanlang:", kb([[(v, f"ess:{e['telegram_id']}:{k}")] for k, v in SALARY_TYPES.items()]
                                                 + [[("⬅️ Orqaga", f"e:{e['telegram_id']}")]]))


@router.callback_query(F.data.startswith("ess:"))
async def a_emp_stype_set(cb: CallbackQuery, actor: dict):
    _, tid, st = cb.data.split(":")
    e = await load_manageable(cb, actor, tid) if is_admin(actor) else None
    if not e or st not in SALARY_TYPES:
        return
    await employees.update_employee(tid, {"salary_type": st})
    await cb.answer("Saqlandi")
    await refresh_emp(cb, tid, "✅ Maosh turi o'zgartirildi.\n\n")


@router.callback_query(F.data.regexp(r"^e[bc]:\d+$"))
async def a_emp_value_start(cb: CallbackQuery, actor: dict, state: FSMContext):
    kind, tid = cb.data[:2], cb.data[3:]
    e = await load_manageable(cb, actor, tid) if is_admin(actor) else None
    if not e:
        return
    await cb.answer()
    await state.clear()
    await state.set_state(EmpEdit.value)
    await state.update_data(tid=tid, field="base_salary" if kind == "eb" else "commission_value")
    if kind == "eb":
        await cb.message.answer("💵 Oylik maosh summasini kiriting (so'm, 0 = yo'q):", reply_markup=CANCEL)
    else:
        await cb.message.answer("📈 Maxsus komissiya qiymatini kiriting (son).\nTurini (foiz / m² / qat'iy) keyin tanlaysiz.\n"
                                "Standartga qaytarish uchun <code>-</code> yuboring.", reply_markup=CANCEL)


@router.message(EmpEdit.value, F.text)
async def a_emp_value_save(m: Message, actor: dict, state: FSMContext):
    d = await state.get_data()
    e = await employees.get_employee(d["tid"], fresh=True)
    if not e or not can_manage(actor, e):
        await state.clear()
        return await m.answer("⛔ Ruxsat yo'q.")
    txt = m.text.strip()
    if d["field"] == "commission_value" and txt == "-":
        await employees.update_employee(d["tid"], {"commission_value": "", "commission_type": ""})
        await state.clear()
        return await m.answer("✅ Standart komissiyaga qaytarildi.", reply_markup=kb([[("👤 Xodim", f"e:{d['tid']}")]]))
    val = parse_nonneg(txt)
    if val is None:
        return await m.answer("❗ Iltimos, son kiriting (masalan 5 yoki 15000).", reply_markup=CANCEL)
    await employees.update_employee(d["tid"], {d["field"]: val})
    await state.clear()
    if d["field"] == "commission_value":
        rows = [[(v, f"ect:{d['tid']}:{k}")] for k, v in PAY_TYPES.items()] + [[("Standart tur", f"ect:{d['tid']}:")]]
        return await m.answer("📈 Komissiya turini tanlang:", reply_markup=kb(rows))
    await m.answer("✅ Saqlandi.", reply_markup=kb([[("👤 Xodim", f"e:{d['tid']}")]]))


@router.callback_query(F.data.startswith("ect:"))
async def a_emp_ctype(cb: CallbackQuery, actor: dict):
    _, tid, ct = cb.data.split(":")
    e = await load_manageable(cb, actor, tid) if is_admin(actor) else None
    if not e or (ct and ct not in PAY_TYPES):
        return
    await employees.update_employee(tid, {"commission_type": ct})
    await cb.answer("Saqlandi")
    await refresh_emp(cb, tid, "✅ Komissiya sozlandi.\n\n")


@router.callback_query(F.data.startswith("eh:"))
async def a_emp_history(cb: CallbackQuery, actor: dict):
    if not await need_admin(cb, actor):
        return
    await cb.answer()
    tid = cb.data[3:]
    rows = await orders.employee_history(tid)
    lines = [f"• {short_ts(h['timestamp'])} · {esc(h['order_id'])}\n   {esc(h['action_details'])}" for h in rows]
    await show(cb, "🕘 <b>So'nggi vazifalar</b>\n\n" + ("\n".join(lines) or "Hali vazifa yo'q."),
               kb([[("⬅️ Orqaga", f"e:{tid}")]]))


# ---- xodim qo'shish
@router.callback_query(F.data == "e:add")
async def a_add_start(cb: CallbackQuery, actor: dict, state: FSMContext):
    if not await need_admin(cb, actor):
        return
    await cb.answer()
    await state.clear()
    await state.set_state(AddEmp.tid)
    await cb.message.answer("➕ <b>Yangi xodim</b>\n\nXodimning Telegram ID raqamini kiriting.\n"
                            "<i>Xodim botga /start yuborsa, o'z ID raqamini ko'radi.</i>", reply_markup=CANCEL)


@router.message(AddEmp.tid, F.text)
async def a_add_tid(m: Message, state: FSMContext):
    t = m.text.strip()
    if not t.isdigit() or not 5 <= len(t) <= 15:
        return await m.answer("❗ Telegram ID faqat raqamlardan iborat bo'lishi kerak.", reply_markup=CANCEL)
    if int(t) in config.OWNER_IDS or await employees.get_employee(t, fresh=True):
        await state.clear()
        return await m.answer("ℹ️ Bu foydalanuvchi allaqachon tizimda bor.", reply_markup=kb([[("👥 Xodimlar", "a:emps")]]))
    await state.update_data(tid=t)
    await state.set_state(AddEmp.role)
    await m.answer("🎭 Rolni tanlang:", reply_markup=kb([[(ROLE_LABELS[r], f"ar:{r}")] for r in STAFF_ROLES]
                                                       + [[("❌ Bekor qilish", "x:cancel")]]))


@router.callback_query(AddEmp.role, F.data.startswith("ar:"))
async def a_add_role(cb: CallbackQuery, actor: dict, state: FSMContext):
    role = cb.data[3:]
    if role not in STAFF_ROLES or (role == "admin" and actor["role"] != "owner"):
        return await cb.answer("⛔ Administrator rolini faqat Ega bera oladi", show_alert=True)
    await cb.answer()
    await state.update_data(role=role)
    await state.set_state(AddEmp.name)
    await cb.message.answer("👤 Xodimning to'liq ismini kiriting:", reply_markup=CANCEL)


@router.message(AddEmp.name, F.text)
async def a_add_name(m: Message, state: FSMContext):
    name = m.text.strip()
    if not 2 <= len(name) <= 80:
        return await m.answer("❗ Ism 2–80 belgi bo'lsin.", reply_markup=CANCEL)
    await state.update_data(name=name)
    await state.set_state(AddEmp.salary_type)
    await m.answer("💼 Maosh turini tanlang:", reply_markup=kb([[(v, f"as:{k}")] for k, v in SALARY_TYPES.items()]
                                                              + [[("❌ Bekor qilish", "x:cancel")]]))


async def add_confirm(target: Message, state: FSMContext):
    d = await state.get_data()
    await state.set_state(AddEmp.confirm)
    await target.answer(
        f"➕ <b>Xodim ma'lumotlari</b>\n\n👤 {esc(d['name'])}\n🆔 <code>{d['tid']}</code>\n🎭 {ROLE_LABELS[d['role']]}\n"
        f"💼 {SALARY_TYPES[d['stype']]}\n💵 Oylik: {money(d.get('base', 0))}\n\nSaqlaymizmi?",
        reply_markup=kb([[("✅ Saqlash", "ae:ok")], [("❌ Bekor qilish", "x:cancel")]]))


@router.callback_query(AddEmp.salary_type, F.data.startswith("as:"))
async def a_add_stype(cb: CallbackQuery, state: FSMContext):
    st = cb.data[3:]
    if st not in SALARY_TYPES:
        return await cb.answer()
    await cb.answer()
    await state.update_data(stype=st, base=0)
    if st in ("fixed", "mixed"):
        await state.set_state(AddEmp.base)
        return await cb.message.answer("💵 Oylik maosh summasini kiriting (so'm):", reply_markup=CANCEL)
    await add_confirm(cb.message, state)


@router.message(AddEmp.base, F.text)
async def a_add_base(m: Message, state: FSMContext):
    val = parse_nonneg(m.text)
    if val is None:
        return await m.answer("❗ Iltimos, son kiriting.", reply_markup=CANCEL)
    await state.update_data(base=val)
    await add_confirm(m, state)


@router.callback_query(AddEmp.confirm, F.data == "ae:ok")
async def a_add_save(cb: CallbackQuery, actor: dict, state: FSMContext, bot: Bot):
    if not is_admin(actor):
        return await cb.answer("⛔ Ruxsat yo'q", show_alert=True)
    d = await state.get_data()
    if d["role"] == "admin" and actor["role"] != "owner":
        return await cb.answer("⛔ Ruxsat yo'q", show_alert=True)
    if await employees.get_employee(d["tid"], fresh=True):
        await state.clear()
        return await cb.answer("Bu xodim allaqachon mavjud", show_alert=True)
    username = ""
    try:
        chat = await bot.get_chat(int(d["tid"]))
        username = chat.username or ""
    except TelegramAPIError:
        pass
    await employees.add_employee(int(d["tid"]), d["name"], d["role"], username, d["stype"], d["base"])
    await state.clear()
    await cb.answer("Saqlandi")
    await show(cb, f"✅ <b>{esc(d['name'])}</b> qo'shildi.", kb([[("👥 Xodimlar", "a:emps")], [HOME_BTN]]))
    await safe_send(bot, d["tid"], f"✅ Siz tizimga <b>{ROLE_LABELS[d['role']]}</b> sifatida qo'shildingiz.\nBoshlash uchun /start bosing.")


# =========================================================== admin: ish haqi va komissiyalar
@router.callback_query(F.data == "a:pay")
async def a_pay(cb: CallbackQuery, actor: dict, state: FSMContext):
    if not await need_admin(cb, actor):
        return
    await cb.answer()
    await state.clear()
    emps = await employees.all_employees(True)
    bal = await salaries.all_balances()
    rows = []
    for e in emps[:30]:
        b = bal.get(str(e["telegram_id"]), {"outstanding": 0})
        rows.append([(f"{e['full_name']} — qoldiq: {money(b['outstanding'])}", f"p:{e['telegram_id']}")])
    rows.append([("📅 Hammaga shu oy oyligini hisoblash", "pm:all")])
    rows.append([HOME_BTN])
    await show(cb, "💰 <b>Ish haqi va komissiyalar</b>\n\nXodimni tanlang:" if emps else "Hali xodim yo'q.", kb(rows))


async def pay_card(tid: str) -> tuple[str, object]:
    e = await employees.get_employee(tid, fresh=True)
    b = await salaries.balance(tid)
    text = (f"💰 <b>{esc(e['full_name'])}</b> · {ROLE_LABELS.get(e['role'], e['role'])}\n\n"
            f"📈 Hisoblangan jami: <b>{money(b['earned'])}</b>\n💳 To'langan jami: <b>{money(b['paid'])}</b>\n"
            f"🧮 Qoldiq (qarz): <b>{money(b['outstanding'])}</b>\n\n"
            f"<i>Qoldiq = jami daromad (komissiya + hisoblangan oylik) − jami to'lovlar</i>")
    return text, kb([
        [("💵 To'lov qilish", f"pp:{tid}"), ("📅 Oylikni hisoblash", f"pa:{tid}")],
        [("🧾 Daromadlar", f"pe:{tid}"), ("💳 To'lovlar", f"pl:{tid}")],
        [("⬅️ Orqaga", "a:pay"), HOME_BTN],
    ])


@router.callback_query(F.data.regexp(r"^p:\d+$"))
async def a_pay_card(cb: CallbackQuery, actor: dict, state: FSMContext):
    if not await need_admin(cb, actor):
        return
    if not await employees.get_employee(cb.data[2:], fresh=True):
        return await cb.answer("Topilmadi", show_alert=True)
    await cb.answer()
    await state.clear()
    text, markup = await pay_card(cb.data[2:])
    await show(cb, text, markup)


@router.callback_query(F.data.startswith("pa:"))
async def a_pay_accrue(cb: CallbackQuery, actor: dict):
    if not await need_admin(cb, actor):
        return
    tid = cb.data[3:]
    e = await employees.get_employee(tid, fresh=True)
    if not e:
        return await cb.answer("Topilmadi", show_alert=True)
    ok, info = await salaries.accrue_monthly(e)
    await cb.answer(("✅ Hisoblandi: " if ok else "ℹ️ ") + info, show_alert=True)
    text, markup = await pay_card(tid)
    await show(cb, text, markup)


@router.callback_query(F.data == "pm:all")
async def a_pay_accrue_all(cb: CallbackQuery, actor: dict):
    if not await need_admin(cb, actor):
        return
    done = skipped = 0
    for e in await employees.all_employees(True):
        if not employees.is_active(e):
            continue
        ok, _ = await salaries.accrue_monthly(e)
        done += ok
        skipped += not ok
    await cb.answer(f"✅ Hisoblandi: {done} ta\nO'tkazib yuborildi: {skipped} ta (allaqachon yoki oylik yo'q)", show_alert=True)


@router.callback_query(F.data.startswith("pe:"))
async def a_pay_earnings(cb: CallbackQuery, actor: dict):
    if not await need_admin(cb, actor):
        return
    await cb.answer()
    tid = cb.data[3:]
    rows = (await salaries.earnings_of(tid))[:15]
    lines = [f"• {short_ts(r['date'])} · {TASK_LABELS.get(r['task_type'], r['task_type'])} · {esc(r['order_id']) or '—'} · "
             f"<b>{money(r['amount'])}</b>" for r in rows]
    await show(cb, "🧾 <b>So'nggi daromadlar</b>\n\n" + ("\n".join(lines) or "Yozuv yo'q."), kb([[("⬅️ Orqaga", f"p:{tid}")]]))


@router.callback_query(F.data.startswith("pl:"))
async def a_pay_list(cb: CallbackQuery, actor: dict):
    if not await need_admin(cb, actor):
        return
    await cb.answer()
    tid = cb.data[3:]
    rows = (await salaries.payments_of(tid))[:15]
    lines = [f"• {short_ts(r['payment_date'])} · <b>{money(r['amount_paid'])}</b> · {esc(r['payment_method'])}"
             f"{' · ' + esc(r['notes']) if r['notes'] else ''}" for r in rows]
    await show(cb, "💳 <b>So'nggi to'lovlar</b>\n\n" + ("\n".join(lines) or "Yozuv yo'q."), kb([[("⬅️ Orqaga", f"p:{tid}")]]))


@router.callback_query(F.data.startswith("pp:"))
async def a_pay_start(cb: CallbackQuery, actor: dict, state: FSMContext):
    if not await need_admin(cb, actor):
        return
    tid = cb.data[3:]
    b = await salaries.balance(tid)
    if b["outstanding"] <= 0:
        return await cb.answer("Qoldiq yo'q — to'lash kerak emas.", show_alert=True)
    await cb.answer()
    await state.clear()
    await state.set_state(Pay.amount)
    await state.update_data(tid=tid, key=uuid.uuid4().hex[:14])
    await cb.message.answer(f"💵 To'lov summasini kiriting (so'm).\nQoldiq: <b>{money(b['outstanding'])}</b>\n"
                            f"To'liq to'lash uchun: <code>{round(b['outstanding'])}</code>", reply_markup=CANCEL)


@router.message(Pay.amount, F.text)
async def a_pay_amount(m: Message, state: FSMContext):
    amount = parse_amount(m.text)
    d = await state.get_data()
    if amount is None:
        return await m.answer("❗ Musbat son kiriting.", reply_markup=CANCEL)
    bal = await salaries.balance(d["tid"])
    if amount > bal["outstanding"] + 0.01:
        return await m.answer(f"❗ Summa qoldiqdan ({money(bal['outstanding'])}) katta bo'lmasin.", reply_markup=CANCEL)
    await state.update_data(amount=amount)
    await state.set_state(Pay.method)
    await m.answer("💳 To'lov usulini tanlang:", reply_markup=kb([[("💵 Naqd", "pmeth:Naqd"), ("💳 Karta", "pmeth:Karta")],
                                                                  [("🏦 Boshqa", "pmeth:Boshqa")], [("❌ Bekor qilish", "x:cancel")]]))


@router.callback_query(Pay.method, F.data.startswith("pmeth:"))
async def a_pay_method(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.update_data(method=cb.data[6:])
    await state.set_state(Pay.note)
    await cb.message.answer("📝 Izoh yozing (ixtiyoriy):", reply_markup=kb([[("⏭ Izohsiz", "pnote:skip")], [("❌ Bekor qilish", "x:cancel")]]))


async def pay_confirm(target: Message, state: FSMContext):
    d = await state.get_data()
    e = await employees.get_employee(d["tid"])
    await state.set_state(Pay.confirm)
    await target.answer(f"💳 <b>To'lovni tasdiqlang</b>\n\n👤 {esc(e['full_name'])}\n💵 Summa: <b>{money(d['amount'])}</b>\n"
                        f"Usul: {esc(d['method'])}\nIzoh: {esc(d.get('note') or '—')}",
                        reply_markup=kb([[("✅ Tasdiqlash", "pcf:ok")], [("❌ Bekor qilish", "x:cancel")]]))


@router.callback_query(Pay.note, F.data == "pnote:skip")
async def a_pay_note_skip(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.update_data(note="")
    await pay_confirm(cb.message, state)


@router.message(Pay.note, F.text)
async def a_pay_note(m: Message, state: FSMContext):
    await state.update_data(note=m.text.strip()[:200])
    await pay_confirm(m, state)


@router.callback_query(Pay.confirm, F.data == "pcf:ok")
async def a_pay_save(cb: CallbackQuery, actor: dict, state: FSMContext, bot: Bot):
    if not is_admin(actor):
        return await cb.answer("⛔ Ruxsat yo'q", show_alert=True)
    d = await state.get_data()
    ok, msg = await salaries.record_payment(d["tid"], d["amount"], d["method"], d.get("note", ""), actor["telegram_id"], d["key"])
    await state.clear()
    if not ok:
        return await show(cb, "⚠️ " + msg, kb([[("⬅️ Orqaga", f"p:{d['tid']}")]]))
    await cb.answer("✅ To'lov saqlandi")
    text, markup = await pay_card(d["tid"])
    await show(cb, "✅ <b>To'lov saqlandi.</b>\n\n" + text, markup)
    await safe_send(bot, d["tid"], f"💳 Sizga <b>{money(d['amount'])}</b> to'landi ({esc(d['method'])}).")


# =========================================================== admin: statistika
@router.callback_query(F.data == "a:stats")
async def a_stats(cb: CallbackQuery, actor: dict):
    if not await need_admin(cb, actor):
        return
    await cb.answer()
    blocks = []
    stats = await orders.statistics()
    for s in stats:
        blocks.append(
            f"<b>{s['label']}</b>\n"
            f"📦 Buyurtmalar: {s['total']} (yakunlangan: {s['completed']}, kutilayotgan: {s['pending']})\n"
            f"🧺 Olingan: {s['collected']} · Yuvilgan: {s['washed']} · Pardozlangan: {s['finished']} · Yetkazilgan: {s['delivered']}\n"
            f"📐 Qayta ishlangan: {area_fmt(s['sqm'])} m²\n💰 Xodim daromadi: {money(s['earnings'])}")
    liab = stats[0]["liabilities"]
    await show(cb, "📊 <b>Biznes statistikasi</b>\n<i>(Toshkent vaqti: " + now_str() + ")</i>\n\n" + "\n\n".join(blocks)
               + f"\n\n🧮 <b>Ish haqi qarzi (jami to'lanmagan):</b> {money(liab)}",
               kb([[("🔄 Yangilash", "a:stats")], [HOME_BTN]]))


# =========================================================== admin: sozlamalar
SETTING_LABELS = {
    "price_per_m2": "1 m² narxi (tavsiya)",
    "collection_pay_type": "Olib ketish haqi turi", "collection_pay_value": "Olib ketish haqi qiymati",
    "washing_pay_type": "Yuvish haqi turi", "washing_pay_value": "Yuvish haqi qiymati",
    "finishing_pay_type": "Pardozlash haqi turi", "finishing_pay_value": "Pardozlash haqi qiymati",
    "delivery_pay_type": "Yetkazish haqi turi", "delivery_pay_value": "Yetkazish haqi qiymati",
}


@router.callback_query(F.data == "a:set")
async def a_settings(cb: CallbackQuery, actor: dict, state: FSMContext):
    if not await need_admin(cb, actor):
        return
    await cb.answer()
    await state.clear()
    st = await salaries.get_settings(True)
    lines = []
    for k, label in SETTING_LABELS.items():
        v = st.get(k, "—")
        lines.append(f"• {label}: <b>{PAY_TYPES.get(v, v) if k.endswith('_type') else v}</b>")
    rows = [[(label, f"sk:{k}")] for k, label in SETTING_LABELS.items()] + [[HOME_BTN]]
    await show(cb, "⚙️ <b>Sozlamalar</b>\n\n" + "\n".join(lines)
               + "\n\n<i>Haq turi: «narxdan foiz», «1 m² uchun» yoki «qat'iy summa».</i>\nO'zgartirish uchun tanlang:", kb(rows))


@router.callback_query(F.data.startswith("sk:"))
async def a_setting_pick(cb: CallbackQuery, actor: dict, state: FSMContext):
    if not await need_admin(cb, actor):
        return
    key = cb.data[3:]
    if key not in SETTING_LABELS:
        return await cb.answer()
    await cb.answer()
    if key.endswith("_type"):
        rows = [[(v, f"sv:{key}:{k}")] for k, v in PAY_TYPES.items()] + [[("⬅️ Orqaga", "a:set")]]
        return await show(cb, f"⚙️ <b>{SETTING_LABELS[key]}</b> — turini tanlang:", kb(rows))
    await state.clear()
    await state.set_state(SetVal.value)
    await state.update_data(key=key)
    await cb.message.answer(f"⚙️ <b>{SETTING_LABELS[key]}</b> uchun yangi qiymatni kiriting (son):", reply_markup=CANCEL)


@router.callback_query(F.data.startswith("sv:"))
async def a_setting_type(cb: CallbackQuery, actor: dict):
    if not await need_admin(cb, actor):
        return
    _, key, val = cb.data.split(":")
    if key not in SETTING_LABELS or not key.endswith("_type") or val not in PAY_TYPES:
        return await cb.answer()
    await salaries.set_setting(key, val)
    await cb.answer("✅ Saqlandi")
    await show(cb, f"✅ {SETTING_LABELS[key]}: <b>{PAY_TYPES[val]}</b>", kb([[("⚙️ Sozlamalar", "a:set")], [HOME_BTN]]))


@router.message(SetVal.value, F.text)
async def a_setting_save(m: Message, actor: dict, state: FSMContext):
    if not is_admin(actor):
        return
    val = parse_nonneg(m.text)
    if val is None:
        return await m.answer("❗ Iltimos, 0 yoki undan katta son kiriting.", reply_markup=CANCEL)
    d = await state.get_data()
    await salaries.set_setting(d["key"], f"{val:g}")
    await state.clear()
    await m.answer(f"✅ {SETTING_LABELS[d['key']]}: <b>{val:g}</b>", reply_markup=kb([[("⚙️ Sozlamalar", "a:set")], [HOME_BTN]]))


# =========================================================== zaxira (fallback) handlerlar va xatolar
@router.callback_query()
async def cb_stale(cb: CallbackQuery):
    await cb.answer("Bu tugma eskirgan. /start ni bosing.", show_alert=True)


@router.message(StateFilter(None))
async def msg_default(m: Message, actor: dict):
    await m.answer(menu_text(actor), reply_markup=main_menu(actor["role"]))


@router.message()
async def msg_in_flow(m: Message):
    await m.answer("❗ Iltimos, so'ralgan ma'lumotni yuboring yoki bekor qilish uchun /cancel ni bosing.")


@router.errors()
async def on_error(event: ErrorEvent, bot: Bot):
    log.error("Handlerda xatolik: %s: %s", type(event.exception).__name__, str(event.exception)[:300])
    u = event.update
    chat_id = (u.message.chat.id if u.message else u.callback_query.from_user.id if u.callback_query else None)
    if u.callback_query:
        try:
            await u.callback_query.answer()
        except TelegramAPIError:
            pass
    if chat_id:
        await safe_send(bot, chat_id, "⚠️ Vaqtinchalik xatolik yuz berdi (Google Sheets band bo'lishi mumkin). "
                                      "Bir daqiqadan keyin qayta urinib ko'ring.")
    return True


dp.include_router(router)


# =========================================================== webhook uchun kirish funksiyalari
class _Redact(logging.Formatter):
    def format(self, record):
        s = super().format(record)
        for secret in (config.BOT_TOKEN, config.WEBHOOK_SECRET, config.REDIS_TOKEN):
            if secret:
                s = s.replace(secret, "***")
        return s


def setup_logging():
    logging.basicConfig(level=logging.INFO)
    for h in logging.getLogger().handlers:
        h.setFormatter(_Redact("%(levelname)s %(name)s: %(message)s"))


def make_bot() -> Bot:
    return Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


async def process_update(data: dict):
    """Bitta Telegram yangilanishini qayta ishlaydi (update_id bo'yicha takrorlanishdan himoyalangan)."""
    kv = get_kv()
    uid = data.get("update_id")
    key = f"upd:{uid}"
    try:
        if uid is not None and not await kv.set(key, "processing", ex=120, nx=True):
            log.info("Takroriy yangilanish o'tkazib yuborildi: %s", uid)
            return
    except Exception:  # noqa: BLE001
        log.warning("Takrorlanishni tekshirib bo'lmadi (Redis)")
    bot = make_bot()
    try:
        await ensure_once(kv)
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
        try:
            await kv.set(key, "done", ex=86400)
        except Exception:  # noqa: BLE001
            pass
    finally:
        await bot.session.close()
        await kv.close()


def public_url() -> str:
    import os
    if config.PUBLIC_URL:
        return config.PUBLIC_URL + "/webhook"
    host = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") or os.environ.get("VERCEL_URL") or ""
    return f"https://{host}/webhook" if host else ""


async def setup_webhook(url: str | None = None) -> str:
    """Webhook'ni ro'yxatdan o'tkazadi va Google Sheets ulanishini tekshiradi."""
    missing = config.missing_required()
    if missing:
        return "❌ Quyidagi o'zgaruvchilar kiritilmagan: " + ", ".join(missing)
    url = url or public_url()
    if not url:
        return "❌ Sayt manzili aniqlanmadi. PUBLIC_URL o'zgaruvchisini kiriting (masalan https://mening-botim.vercel.app)."
    out = []
    kv = get_kv()
    try:
        await ensure_once(kv)
        out.append("✅ Google Sheets ulandi, varaqlar tayyor.")
    except Exception as e:  # noqa: BLE001
        out.append(f"❌ Google Sheets ulanmadi: {type(e).__name__}: {str(e)[:200]}\n"
                   "   (Jadvalni service account email manziliga Editor sifatida ulashganingizni tekshiring.)")
    if not kv.persistent:
        out.append("⚠️ Redis ulanmagan! Vercel'da bot to'g'ri ishlashi uchun Upstash Redis ulang (README, 6-qadam).")
    bot = make_bot()
    try:
        await bot.set_webhook(url, secret_token=config.WEBHOOK_SECRET,
                              allowed_updates=["message", "callback_query"], drop_pending_updates=False)
        info = await bot.get_webhook_info()
        out.append(f"✅ Webhook o'rnatildi: {url}\n   Kutilayotgan yangilanishlar: {info.pending_update_count}")
    except Exception as e:  # noqa: BLE001
        out.append(f"❌ Webhook o'rnatilmadi: {type(e).__name__}. BOT_TOKEN to'g'riligini tekshiring.")
    finally:
        await bot.session.close()
        await kv.close()
    return "\n".join(out)
