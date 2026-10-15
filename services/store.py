"""Doimiy xotira: FSM holati, webhook takrorlanishini oldini olish va qulflar.

Vercel serverless'da xotira (RAM) har so'rovda yo'qolishi mumkin, shuning uchun
Upstash Redis (REST) ishlatiladi. U Vercel Marketplace orqali bepul ulanadi va
KV_REST_API_URL / KV_REST_API_TOKEN o'zgaruvchilarini avtomatik qo'shadi.

Agar Redis ulanmagan bo'lsa, faqat lokal sinov uchun jarayon xotirasi ishlatiladi
(Vercel'da ishonchli EMAS).
"""
import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

import aiohttp
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey

import config

log = logging.getLogger(__name__)
_MEM: dict[str, tuple[str, float | None]] = {}


class KV:
    def __init__(self):
        self.url = config.REDIS_URL
        self.token = config.REDIS_TOKEN
        self._session: aiohttp.ClientSession | None = None
        self._loop = None

    @property
    def persistent(self) -> bool:
        return bool(self.url and self.token)

    async def _cmd(self, *args):
        loop = asyncio.get_running_loop()
        if self._session is None or self._loop is not loop or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8))
            self._loop = loop
        async with self._session.post(
            self.url, json=list(args), headers={"Authorization": f"Bearer {self.token}"}
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200 or (isinstance(data, dict) and "error" in data):
                raise RuntimeError(f"Redis xatosi (HTTP {resp.status})")
            return data.get("result")

    # ---- xotira (lokal) yordamchilari
    @staticmethod
    def _mem_get(key):
        item = _MEM.get(key)
        if not item:
            return None
        if item[1] is not None and item[1] < time.time():
            _MEM.pop(key, None)
            return None
        return item[0]

    async def get(self, key: str):
        if not self.persistent:
            return self._mem_get(key)
        return await self._cmd("GET", key)

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if not self.persistent:
            if nx and self._mem_get(key) is not None:
                return False
            _MEM[key] = (value, time.time() + ex if ex else None)
            return True
        args = ["SET", key, value]
        if ex:
            args += ["EX", str(ex)]
        if nx:
            args.append("NX")
        return (await self._cmd(*args)) == "OK"

    async def delete(self, key: str):
        if not self.persistent:
            _MEM.pop(key, None)
            return
        await self._cmd("DEL", key)

    async def close(self):
        if self._session and not self._session.closed:
            try:
                await self._session.close()
            except Exception:  # noqa: BLE001
                pass
        self._session = None

    @asynccontextmanager
    async def lock(self, name: str, ttl: int = 30, wait: float = 6.0):
        """Qisqa muddatli o'zaro-chiqarib-tashlovchi qulf (mutex).
        Bir vaqtning o'zida ikki kishi bitta buyurtmani o'zgartira olmaydi."""
        key = f"lock:{name}"
        token = uuid.uuid4().hex
        deadline = time.monotonic() + wait
        while not await self.set(key, token, ex=ttl, nx=True):
            if time.monotonic() >= deadline:
                raise LockBusy("Buyurtma hozir band, bir ozdan keyin qayta urinib ko'ring.")
            await asyncio.sleep(0.4)
        try:
            yield
        finally:
            try:
                if await self.get(key) == token:
                    await self.delete(key)
            except Exception:  # noqa: BLE001
                log.warning("Qulfni bo'shatib bo'lmadi: %s", name)


class LockBusy(Exception):
    pass


_kv: KV | None = None


def get_kv() -> KV:
    global _kv
    if _kv is None:
        _kv = KV()
    return _kv


class KVStorage(BaseStorage):
    """aiogram FSM holati va ma'lumotlari — Redis'da (serverless uchun xavfsiz)."""

    TTL = 24 * 3600

    def __init__(self, kv: KV):
        self.kv = kv

    @staticmethod
    def _k(key: StorageKey) -> str:
        return f"fsm:{key.bot_id}:{key.chat_id}:{key.user_id}:{getattr(key, 'thread_id', 0) or 0}:{key.destiny}"

    async def _load(self, key: StorageKey) -> dict:
        raw = await self.kv.get(self._k(key))
        return json.loads(raw) if raw else {"state": None, "data": {}}

    async def _save(self, key: StorageKey, blob: dict):
        k = self._k(key)
        if blob["state"] is None and not blob["data"]:
            await self.kv.delete(k)
        else:
            await self.kv.set(k, json.dumps(blob, ensure_ascii=False), ex=self.TTL)

    async def set_state(self, key: StorageKey, state=None) -> None:
        blob = await self._load(key)
        blob["state"] = state.state if isinstance(state, State) else state
        await self._save(key, blob)

    async def get_state(self, key: StorageKey):
        return (await self._load(key))["state"]

    async def set_data(self, key: StorageKey, data) -> None:
        blob = await self._load(key)
        blob["data"] = dict(data)
        await self._save(key, blob)

    async def get_data(self, key: StorageKey) -> dict:
        return dict((await self._load(key))["data"])

    async def close(self) -> None:
        await self.kv.close()
