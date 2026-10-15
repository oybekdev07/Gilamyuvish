"""Google Sheets — asosiy biznes ma'lumotlar bazasi.

Barcha jadvallar (varaqlar) birinchi ishga tushishda avtomatik yaratiladi.
Eslatma: gspread sinxron kutubxona, shuning uchun hamma chaqiruvlar
asyncio.to_thread orqali alohida oqimda bajariladi.
"""
import asyncio
import base64
import json
import logging
import os
import time

import gspread
from gspread.utils import rowcol_to_a1

import config

log = logging.getLogger(__name__)

ORDERS = "Orders"
CUSTOMERS = "Customers"
EMPLOYEES = "Employees"
EARNINGS = "Employee Earnings"
PAYMENTS = "Salary Payments"
HISTORY = "Order History"
SETTINGS = "Settings"

SCHEMA_VERSION = "v1"

SCHEMA: dict[str, list[str]] = {
    ORDERS: [
        "order_id", "customer_id", "customer_name", "phone", "pickup_address", "latitude", "longitude",
        "carpet_count", "dimensions_json", "total_area", "carpet_type", "instructions",
        "estimated_price", "final_price", "collection_driver_id", "washing_employee_id",
        "finishing_employee_id", "delivery_driver_id", "collection_commission", "washing_payment",
        "finishing_payment", "delivery_commission", "status", "created_by", "created_at",
        "collected_at", "washing_started_at", "washing_completed_at", "finishing_started_at",
        "finishing_completed_at", "delivery_started_at", "delivered_at", "updated_at",
    ],
    CUSTOMERS: ["customer_id", "full_name", "phone", "address", "order_ids", "created_at"],
    EMPLOYEES: [
        "telegram_id", "username", "full_name", "role", "active", "salary_type", "base_salary",
        "commission_type", "commission_value", "created_at",
    ],
    EARNINGS: ["transaction_id", "employee_id", "order_id", "task_type", "amount", "date", "payment_status"],
    PAYMENTS: ["payment_id", "employee_id", "amount_paid", "payment_date", "payment_method", "notes", "admin_id"],
    HISTORY: ["order_id", "previous_status", "new_status", "employee_telegram_id", "employee_name",
              "timestamp", "action_details"],
    SETTINGS: ["key", "value", "description"],
}

DEFAULT_SETTINGS = [
    ("price_per_m2", "15000", "1 m² uchun tavsiya etilgan narx (so'm)"),
    ("collection_pay_type", "percent", "Olib ketish haqi turi: fixed / percent / per_m2"),
    ("collection_pay_value", "5", "Olib ketish haqi qiymati (percent=%, fixed=so'm, per_m2=so'm/m²)"),
    ("washing_pay_type", "per_m2", "Yuvish haqi turi: fixed / percent / per_m2"),
    ("washing_pay_value", "2000", "Yuvish haqi qiymati"),
    ("finishing_pay_type", "per_m2", "Pardozlash haqi turi: fixed / percent / per_m2"),
    ("finishing_pay_value", "1500", "Pardozlash haqi qiymati"),
    ("delivery_pay_type", "percent", "Yetkazish haqi turi: fixed / percent / per_m2"),
    ("delivery_pay_value", "5", "Yetkazish haqi qiymati"),
]

_DEFAULT_SHEET_NAMES = {"Sheet1", "Лист1", "Sheet 1", "Hoja 1", "Feuille 1"}


def _s(v) -> str:
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return "" if v is None else str(v)


def _retry(fn, *args, **kwargs):
    delay = 1.0
    for attempt in range(4):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            code = getattr(getattr(e, "response", None), "status_code", 0)
            if code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(delay)
                delay *= 2
                continue
            raise


def _resolve_path(path: str) -> str:
    path = os.path.expandvars(os.path.expanduser(path))
    if not os.path.isabs(path):  # nisbiy yo'l — loyiha papkasiga nisbatan
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), path)
    return path


def _load_credentials() -> dict:
    raw = config.GOOGLE_SERVICE_ACCOUNT_JSON
    if not raw and config.GOOGLE_SERVICE_ACCOUNT_FILE:
        path = _resolve_path(config.GOOGLE_SERVICE_ACCOUNT_FILE)
        if not os.path.isfile(path):
            raise RuntimeError(f"Service account JSON fayli topilmadi: {path}")
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON yoki GOOGLE_SERVICE_ACCOUNT_FILE o'rnatilmagan")
    if not raw.lstrip().startswith("{"):
        raw = base64.b64decode(raw).decode("utf-8")
    info = json.loads(raw)
    if "private_key" in info:
        info["private_key"] = info["private_key"].replace("\\n", "\n")
    return info


class GoogleSheetsBackend:
    def __init__(self):
        self.gc = gspread.service_account_from_dict(_load_credentials())
        self.sh = self.gc.open_by_key(config.GOOGLE_SHEET_ID)
        self._ws: dict[str, gspread.Worksheet] = {}
        self._hdr: dict[str, list[str]] = {}

    # ---------- sinxron ichki funksiyalar
    def _worksheet(self, name: str):
        if name not in self._ws:
            self._ws[name] = _retry(self.sh.worksheet, name)
        return self._ws[name]

    def _headers(self, name: str) -> list[str]:
        if name not in self._hdr:
            self._hdr[name] = _retry(self._worksheet(name).row_values, 1)
        return self._hdr[name]

    def _ensure(self):
        existing = {w.title: w for w in _retry(self.sh.worksheets)}
        for name, headers in SCHEMA.items():
            ws = existing.get(name)
            if ws is None:
                ws = _retry(self.sh.add_worksheet, title=name, rows=1000, cols=max(len(headers), 10))
                _retry(ws.update, values=[headers], range_name="A1")
                try:
                    ws.freeze(rows=1)
                except Exception:  # noqa: BLE001
                    pass
            else:
                row1 = _retry(ws.row_values, 1)
                if not any(row1):
                    _retry(ws.update, values=[headers], range_name="A1")
                else:
                    missing = [h for h in headers if h not in row1]
                    if missing:  # yangi ustunlarni oxiriga qo'shamiz (mavjud ma'lumot buzilmaydi)
                        _retry(ws.update, values=[row1 + missing], range_name="A1")
            self._ws[name] = ws
            self._hdr.pop(name, None)
        # Standart bo'sh varaqni olib tashlaymiz
        for title, ws in existing.items():
            if title in _DEFAULT_SHEET_NAMES and title not in SCHEMA:
                try:
                    if not any(_retry(ws.get_all_values)):
                        _retry(self.sh.del_worksheet, ws)
                except Exception:  # noqa: BLE001
                    pass
        # Standart sozlamalar
        have = {r["key"] for r in self._rows(SETTINGS)}
        add = [[k, v, d] for k, v, d in DEFAULT_SETTINGS if k not in have]
        if add:
            _retry(self._worksheet(SETTINGS).append_rows, add, value_input_option="RAW")

    def _rows(self, name: str) -> list[dict]:
        ws = self._worksheet(name)
        vals = _retry(ws.get_all_values, value_render_option="UNFORMATTED_VALUE")
        if not vals:
            return []
        head = [_s(h) for h in vals[0]]
        self._hdr[name] = head
        out = []
        for r in vals[1:]:
            cells = [_s(c) for c in r]
            if not any(c.strip() for c in cells):
                continue
            out.append({h: (cells[i] if i < len(cells) else "") for i, h in enumerate(head)})
        return out

    def _append(self, name: str, row: dict):
        ws = self._worksheet(name)
        head = self._headers(name)
        values = [row.get(h, "") for h in head]
        _retry(ws.append_row, values, value_input_option="RAW", table_range="A1")

    def _update(self, name: str, key_col: str, key: str, updates: dict) -> bool:
        ws = self._worksheet(name)
        head = self._headers(name)
        if key_col not in head:
            return False
        keys = _retry(ws.col_values, head.index(key_col) + 1, value_render_option="UNFORMATTED_VALUE")
        row_no = None
        for i, v in enumerate(keys[1:], start=2):
            if _s(v) == str(key):
                row_no = i
                break
        if row_no is None:
            return False
        data = [
            {"range": rowcol_to_a1(row_no, head.index(c) + 1), "values": [[v]]}
            for c, v in updates.items() if c in head
        ]
        if data:
            _retry(ws.batch_update, data, value_input_option="RAW")
        return True

    # ---------- asinxron interfeys
    async def ensure(self):
        await asyncio.to_thread(self._ensure)

    async def rows(self, name: str) -> list[dict]:
        return await asyncio.to_thread(self._rows, name)

    async def append(self, name: str, row: dict):
        await asyncio.to_thread(self._append, name, row)

    async def update(self, name: str, key_col: str, key: str, updates: dict) -> bool:
        return await asyncio.to_thread(self._update, name, key_col, key, updates)


_db = None
_ensured = False


def get_db():
    global _db
    if _db is None:
        _db = GoogleSheetsBackend()
    return _db


async def ensure_once(kv=None):
    """Varaqlar mavjudligini tekshiradi (har sovuq startda emas, Redis belgisi bo'lsa o'tkazib yuboradi)."""
    global _ensured
    if _ensured:
        return
    flag = f"schema:{SCHEMA_VERSION}:{config.GOOGLE_SHEET_ID}"
    if kv is not None and kv.persistent and await kv.get(flag):
        _ensured = True
        return
    await get_db().ensure()
    if kv is not None and kv.persistent:
        await kv.set(flag, "1")
    _ensured = True
