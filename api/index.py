"""Vercel serverless kirish nuqtasi: Telegram webhook.

POST  /webhook            -> Telegram yangilanishlarini qabul qiladi (maxfiy token tekshiriladi)
GET   /webhook?setup=1    -> webhook'ni ro'yxatdan o'tkazadi va Google Sheets ulanishini tekshiradi
GET   /webhook            -> sog'liq tekshiruvi
"""
import asyncio
import hmac
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402
from bot import process_update, setup_logging, setup_webhook  # noqa: E402

setup_logging()
log = logging.getLogger("webhook")


class handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, text: str):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        if "setup" in params:
            # Manzil serverdagi o'zgaruvchidan olinadi (so'rovdan emas) — begona manzilga yo'naltirib bo'lmaydi.
            try:
                return self._reply(200, asyncio.run(setup_webhook()))
            except Exception as e:  # noqa: BLE001
                log.error("setup xatosi: %s", type(e).__name__)
                return self._reply(500, "Xatolik: " + type(e).__name__)
        self._reply(200, "Gilam yuvish boti ishlayapti ✅")

    def do_POST(self):
        secret = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not config.WEBHOOK_SECRET or not hmac.compare_digest(secret, config.WEBHOOK_SECRET):
            return self._reply(403, "forbidden")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._reply(400, "bad request")
        try:
            asyncio.run(process_update(data))
        except Exception as e:  # noqa: BLE001
            # Telegram qayta yubormasligi uchun 200 qaytaramiz; xatolik logda (tokensiz).
            log.error("Yangilanishni qayta ishlashda xatolik: %s: %s", type(e).__name__, str(e)[:200])
        self._reply(200, "ok")

    def log_message(self, *args):  # standart loglarni o'chiramiz (URL/maxfiy ma'lumot chiqmasin)
        return
