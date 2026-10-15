ROLE_LABELS = {
    "owner": "Ega (Super Admin)",
    "admin": "Administrator",
    "operator": "Operator",
    "collector": "Olib ketuvchi haydovchi",
    "washer": "Yuvuvchi xodim",
    "finisher": "Pardozlovchi xodim",
    "deliverer": "Yetkazib beruvchi haydovchi",
}
STAFF_ROLES = ["admin", "operator", "collector", "washer", "finisher", "deliverer"]

STATUS_LABELS = {
    "new": "🆕 Yangi",
    "assigned": "🚗 Haydovchi biriktirilgan",
    "collected": "📦 Olib kelingan",
    "washing": "🧼 Yuvilmoqda",
    "washed": "💧 Yuvilgan",
    "finishing": "✨ Pardozlanmoqda",
    "ready": "✅ Yetkazishga tayyor",
    "delivering": "🚛 Yetkazilmoqda",
    "delivered": "🏁 Yetkazib berildi",
    "cancelled": "❌ Bekor qilingan",
}
FILTER_STATUSES = ["new", "assigned", "collected", "washing", "washed",
                   "finishing", "ready", "delivering", "delivered", "cancelled"]

TASK_LABELS = {
    "collection": "Olib ketish",
    "washing": "Yuvish",
    "finishing": "Pardozlash",
    "delivery": "Yetkazib berish",
    "salary": "Oylik maosh",
}
ROLE_TASK = {"collector": "collection", "washer": "washing", "finisher": "finishing", "deliverer": "delivery"}
COMMISSION_COL = {
    "collection": "collection_commission",
    "washing": "washing_payment",
    "finishing": "finishing_payment",
    "delivery": "delivery_commission",
}

# Har bir bosqich: kim bajaradi, qaysi holatdan qaysi holatga o'tadi.
# claim=True  -> birinchi bosgan xodim biriktiriladi (atomik).
# claim=False -> faqat biriktirilgan xodim bajaradi.
STAGES = {
    "acc": dict(role="collector", frm="new", to="assigned", claim=True, who="collection_driver_id",
                ts=None, earn=None, btn="✅ Buyurtmani qabul qilish",
                log="Haydovchi buyurtmani qabul qildi", ask=""),
    "col": dict(role="collector", frm="assigned", to="collected", claim=False, who="collection_driver_id",
                ts="collected_at", earn="collection", btn="📦 Gilamlar olindi",
                log="Gilamlar mijozdan olib kelindi", ask="Gilamlar mijozdan olinganini tasdiqlaysizmi?"),
    "wst": dict(role="washer", frm="collected", to="washing", claim=True, who="washing_employee_id",
                ts="washing_started_at", earn=None, btn="🧼 Yuvishni boshlash",
                log="Yuvish boshlandi", ask=""),
    "wdn": dict(role="washer", frm="washing", to="washed", claim=False, who="washing_employee_id",
                ts="washing_completed_at", earn="washing", btn="✅ Yuvish tugadi",
                log="Yuvish yakunlandi", ask="Yuvish tugaganini tasdiqlaysizmi?"),
    "fst": dict(role="finisher", frm="washed", to="finishing", claim=True, who="finishing_employee_id",
                ts="finishing_started_at", earn=None, btn="✨ Pardozlashni boshlash",
                log="Pardozlash boshlandi", ask=""),
    "fdn": dict(role="finisher", frm="finishing", to="ready", claim=False, who="finishing_employee_id",
                ts="finishing_completed_at", earn="finishing", btn="✅ Pardozlash tugadi",
                log="Pardozlash yakunlandi", ask="Pardozlash tugaganini tasdiqlaysizmi?"),
    "dac": dict(role="deliverer", frm="ready", to="delivering", claim=True, who="delivery_driver_id",
                ts="delivery_started_at", earn=None, btn="🚛 Yetkazishni qabul qilish",
                log="Yetkazib berish qabul qilindi", ask=""),
    "ddn": dict(role="deliverer", frm="delivering", to="delivered", claim=False, who="delivery_driver_id",
                ts="delivered_at", earn="delivery", btn="✅ Mijozga topshirildi",
                log="Mijozga topshirildi", ask="Gilamlar mijozga topshirilganini tasdiqlaysizmi?"),
}

# Admin qayta biriktirishi mumkin bo'lgan bosqichlar
REASSIGN = {
    "collection": dict(role="collector", statuses=["new", "assigned"], col="collection_driver_id", claim_act="acc"),
    "washing": dict(role="washer", statuses=["collected", "washing"], col="washing_employee_id", claim_act="wst"),
    "finishing": dict(role="finisher", statuses=["washed", "finishing"], col="finishing_employee_id", claim_act="fst"),
    "delivery": dict(role="deliverer", statuses=["ready", "delivering"], col="delivery_driver_id", claim_act="dac"),
}

SALARY_TYPES = {"fixed": "Oylik (qat'iy)", "commission": "Komissiya", "mixed": "Oylik + komissiya"}
PAY_TYPES = {"fixed": "qat'iy summa", "percent": "narxdan foiz", "per_m2": "1 m² uchun"}
