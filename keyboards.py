from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def kb(rows) -> InlineKeyboardMarkup:
    """rows: [[(matn, callback_data), ...], ...]"""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows]
    )


CANCEL = kb([[("❌ Bekor qilish", "x:cancel")]])
HOME_BTN = ("🏠 Bosh menyu", "m:home")

_ADMIN_MENU = [
    ("📦 Buyurtmalar", "a:orders"),
    ("👥 Xodimlar", "a:emps"),
    ("💰 Ish haqi va komissiyalar", "a:pay"),
    ("📊 Statistika", "a:stats"),
    ("⚙️ Sozlamalar", "a:set"),
    ("➕ Yangi buyurtma", "m:newo"),
    ("🔍 Buyurtma qidirish", "m:srch"),
]
MENUS = {
    "operator": [("➕ Yangi buyurtma", "m:newo"), ("📋 Buyurtmalarim", "m:myo"), ("🔍 Buyurtma qidirish", "m:srch")],
    "collector": [("📦 Yangi olib ketish buyurtmalari", "m:cnew"), ("🚚 Mening vazifalarim", "m:ctasks")],
    "washer": [("🧼 Yuvish uchun buyurtmalar", "m:wnew"), ("⚙️ Jarayondagi ishlar", "m:wprog")],
    "finisher": [("✨ Pardozlash buyurtmalari", "m:fnew"), ("⚙️ Jarayondagi ishlar", "m:fprog")],
    "deliverer": [("🚛 Yetkazib berish buyurtmalari", "m:dnew"), ("📋 Mening yetkazmalarim", "m:dtasks")],
    "admin": _ADMIN_MENU,
    "owner": _ADMIN_MENU,
}


def main_menu(role: str) -> InlineKeyboardMarkup:
    return kb([[b] for b in MENUS.get(role, [])])


def location_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Joylashuvni yuborish", request_location=True)],
                  [KeyboardButton(text="⏭ O'tkazib yuborish")]],
        resize_keyboard=True, one_time_keyboard=True,
    )
