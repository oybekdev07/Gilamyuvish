"""Botni kompyuterda (VS Code) ishga tushirish — long polling rejimi.

Ishga tushirish:   python run_local.py
To'xtatish:        Ctrl+C

Vercel, webhook va Redis kerak emas. Sozlamalar .env faylidan o'qiladi.
"""
import asyncio
import json
import logging
import sys

import config
from bot import dp, make_bot, setup_logging
from services.google_sheets import _load_credentials, ensure_once
from services.store import get_kv

log = logging.getLogger("local")


def _service_email() -> str:
    try:
        return _load_credentials().get("client_email", "")
    except Exception:  # noqa: BLE001
        return ""


async def main() -> int:
    setup_logging()
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    missing = config.missing_required(webhook=False)
    if missing:
        print("❌ .env faylida quyidagilar to'ldirilmagan: " + ", ".join(missing))
        return 1

    # 1) Google Sheets
    try:
        await ensure_once(get_kv())
        print("✅ Google Sheets ulandi, varaqlar tayyor.")
    except Exception as e:  # noqa: BLE001
        email = _service_email()
        print(f"❌ Google Sheets ulanmadi: {type(e).__name__}: {str(e)[:250]}")
        if isinstance(e, (RuntimeError, json.JSONDecodeError)):
            print("   .env dagi GOOGLE_SERVICE_ACCOUNT_FILE yo'lini va fayl mazmunini tekshiring.")
        else:
            print("   Tekshiring: 1) Google Sheets API yoqilganmi (Google Cloud → Library),")
            print("               2) jadval quyidagi emailga EDITOR sifatida ulashilganmi:")
            print(f"                  {email or '(JSON fayldagi client_email)'}")
            print("               3) GOOGLE_SHEET_ID to'g'rimi.")
        return 1

    # 2) Telegram
    bot = make_bot()
    try:
        me = await bot.get_me()
    except Exception as e:  # noqa: BLE001
        print(f"❌ Telegramga ulanib bo'lmadi: {type(e).__name__}. BOT_TOKEN to'g'riligini tekshiring.")
        await bot.session.close()
        return 1

    # Agar avval Vercel webhook o'rnatilgan bo'lsa, polling ishlashi uchun uni o'chiramiz.
    await bot.delete_webhook(drop_pending_updates=False)
    print(f"🤖 Bot ishga tushdi: @{me.username}  (to'xtatish: Ctrl+C)")
    print(f"👑 Ega ID: {', '.join(map(str, config.OWNER_IDS))}  → Telegramda botga /start yuboring.")
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        await bot.session.close()
        await get_kv().close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n👋 Bot to'xtatildi.")
