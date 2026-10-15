"""Sozlamalar faqat muhit o'zgaruvchilaridan (Environment Variables) o'qiladi.
Hech qanday maxfiy ma'lumot kod ichida saqlanmaydi."""
import os

try:  # VS Code / lokal ishga tushirishda .env faylini avtomatik o'qiydi (Vercel'da kerak emas)
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=False)
except ImportError:  # python-dotenv o'rnatilmagan bo'lsa, oddiy muhit o'zgaruvchilari ishlaydi
    pass


def _ids(raw: str) -> list[int]:
    out = []
    for part in (raw or "").replace(";", ",").replace(" ", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_IDS = _ids(os.environ.get("ADMIN_IDS", ""))  # Ega / Super Admin Telegram ID lari
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
# Lokal ishlatish uchun: service account JSON faylining yo'li (JSON matni bo'sh bo'lsa shu olinadi)
GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip().strip('"').strip("'")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Tashkent").strip() or "Asia/Tashkent"

# Upstash Redis (Vercel Marketplace orqali ulanganda shu nomlar avtomatik qo'shiladi)
REDIS_URL = (os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL") or "").strip()
REDIS_TOKEN = (os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN") or "").strip()

# Webhook manzili uchun (ixtiyoriy). Bo'sh bo'lsa Vercel'ning production domeni olinadi.
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")


def missing_required(webhook: bool = True) -> list[str]:
    """webhook=False — lokal polling rejimi (WEBHOOK_SECRET va Redis shart emas)."""
    need = {
        "BOT_TOKEN": BOT_TOKEN,
        "ADMIN_IDS": OWNER_IDS,
        "GOOGLE_SHEET_ID": GOOGLE_SHEET_ID,
        "GOOGLE_SERVICE_ACCOUNT_JSON yoki GOOGLE_SERVICE_ACCOUNT_FILE": GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE,
    }
    if webhook:
        need["WEBHOOK_SECRET"] = WEBHOOK_SECRET
    return [k for k, v in need.items() if not v]
