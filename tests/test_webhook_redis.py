import asyncio, json, os, sys, threading, urllib.request, urllib.error
from http.server import HTTPServer
from aiohttp import web

os.environ.update({"BOT_TOKEN": "123456:TESTTOKEN", "ADMIN_IDS": "1001", "GOOGLE_SHEET_ID": "x",
                   "GOOGLE_SERVICE_ACCOUNT_JSON": "{}", "WEBHOOK_SECRET": "s3cret"})
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
res = []
def check(n, c): res.append(c); print(("PASS  " if c else "FAIL  ") + n)

# ---- 1) webhook HTTP qatlami
from api.index import handler
srv = HTTPServer(("127.0.0.1", 0), handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]
def post(body, secret=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/index", data=body, method="POST")
    if secret is not None: req.add_header("X-Telegram-Bot-Api-Secret-Token", secret)
    try: return urllib.request.urlopen(req, timeout=20).status
    except urllib.error.HTTPError as e: return e.code
check("Maxfiy token yo'q -> 403", post(b"{}") == 403)
check("Noto'g'ri maxfiy token -> 403", post(b"{}", "wrong") == 403)
check("Noto'g'ri JSON -> 400", post(b"not json", "s3cret") == 400)
check("GET sog'liq tekshiruvi 200", urllib.request.urlopen(f"http://127.0.0.1:{port}/api/index").status == 200)
srv.shutdown()

# ---- 2) Upstash REST taqlidi bilan KV (SET NX EX / GET / DEL) va FSM saqlash
store = {}
async def redis_handler(request):
    if request.headers.get("Authorization") != "Bearer tok": return web.json_response({"error": "unauthorized"}, status=401)
    cmd = await request.json()
    op = cmd[0].upper()
    if op == "GET": return web.json_response({"result": store.get(cmd[1])})
    if op == "DEL": store.pop(cmd[1], None); return web.json_response({"result": 1})
    if op == "SET":
        if "NX" in cmd and cmd[1] in store: return web.json_response({"result": None})
        store[cmd[1]] = cmd[2]; return web.json_response({"result": "OK"})
    return web.json_response({"error": "bad"}, status=400)

async def run():
    app = web.Application(); app.router.add_post("/", redis_handler)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/"
    import config; config.REDIS_URL, config.REDIS_TOKEN = url, "tok"
    from services.store import KV, KVStorage, LockBusy
    from aiogram.fsm.storage.base import StorageKey
    kv = KV(); check("Redis ulangan deb aniqlandi", kv.persistent)
    check("SET NX birinchi marta muvaffaqiyatli", await kv.set("a", "1", nx=True, ex=10) is True)
    check("SET NX ikkinchi marta rad etiladi (qulf)", await kv.set("a", "2", nx=True, ex=10) is False)
    check("GET qiymatni qaytaradi", await kv.get("a") == "1")
    await kv.delete("a"); check("DEL ishlaydi", await kv.get("a") is None)
    # qulf: parallel ikki vazifa ketma-ket bajariladi
    order = []
    async def worker(n):
        async with kv.lock("o1", wait=5):
            order.append(f"in{n}"); await asyncio.sleep(0.5); order.append(f"out{n}")
    await asyncio.gather(worker(1), worker(2))
    check("Qulf bir vaqtda ikki kirishga yo'l qo'ymaydi", order in (["in1","out1","in2","out2"], ["in2","out2","in1","out1"]))
    st = KVStorage(kv); key = StorageKey(bot_id=1, chat_id=5, user_id=5)
    await st.set_state(key, "NewOrder:name"); await st.update_data(key, {"x": 1})
    check("FSM holati Redis'da saqlanadi (yangi KV bilan ham o'qiladi)",
          await KVStorage(KV()).get_state(key) == "NewOrder:name" and (await KVStorage(KV()).get_data(key)) == {"x": 1})
    await kv.close(); await runner.cleanup()
asyncio.run(run())
print(f"\nO'TDI: {sum(res)}  YIQILDI: {len(res)-sum(res)}")
sys.exit(0 if all(res) else 1)
