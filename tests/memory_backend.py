"""Faqat TEST uchun: Google Sheets o'rniga xotiradagi jadval (interfeys bir xil)."""
from services.google_sheets import DEFAULT_SETTINGS, SCHEMA, SETTINGS, _s


class MemoryBackend:
    def __init__(self):
        self.t = {name: [] for name in SCHEMA}
        for k, v, d in DEFAULT_SETTINGS:
            self.t[SETTINGS].append({"key": k, "value": v, "description": d})

    async def ensure(self):
        pass

    async def rows(self, name):
        return [dict(r) for r in self.t[name]]

    async def append(self, name, row):
        self.t[name].append({h: _s(row.get(h, "")) for h in SCHEMA[name]})

    async def update(self, name, key_col, key, updates):
        for r in self.t[name]:
            if r[key_col] == str(key):
                for c, v in updates.items():
                    if c in r:
                        r[c] = _s(v)
                return True
        return False
