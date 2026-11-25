"""
GHOST PREMIUM STORE - Telegram shop bot (single file)
UPI deposit + screenshot approve | products | keys | codes | refer & earn
force-join channels | premium emoji (admin chat se add) | backup har 4 ghante
"""
import asyncio
import csv
import html
import io
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramConflictError, TelegramUnauthorizedError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BufferedInputFile, CallbackQuery, ChatMemberUpdated, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

try:
    import qrcode
except ImportError:
    qrcode = None

# ============ ENV (Railway Variables me BOT_TOKEN aur ADMIN_ID daalo) ============
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip("\"'").replace(" ", "")
ADMIN_ID = int((os.getenv("ADMIN_ID") or "0").strip() or 0)
_VOL = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
DB_PATH = os.getenv("DB_FILE") or (os.path.join(_VOL, "shop.db") if _VOL else "shop.db")
BACKUP_EVERY = 4 * 3600
SWEEP_EVERY = 600
JOIN_CACHE = 20
HIST_PAGE = 8
IST = timezone(timedelta(hours=5, minutes=30))
LINE = "━━━━━━━━━━━━━━━━━━━"
# Ye naam wale products restore / add nahi honge
BLOCKED_WORDS = ("BOOMB", "BOMBER", "CRACK", "HACK", "GMAIL", "GOLD APK", "CAPCUT", "VOTING")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
if not BOT_TOKEN or not ADMIN_ID:
    raise SystemExit("❌ Railway Variables me BOT_TOKEN aur ADMIN_ID set karo.")

DEFAULTS = {
    "brand": "GHOST PREMIUM STORE",
    "upi_id": "",
    "upi_name": "",
    "support": "@karan_shukla_36",
    "min_dep": "50",
    "max_dep": "10000",
    "ref_reward": "1",
    "bonus_pct": "0",
    "bonus_min": "500",
}
SETTING_LABELS = [
    ("brand", "🏷 Bot / Store Naam"),
    ("upi_id", "💳 UPI ID"),
    ("upi_name", "👤 UPI Naam"),
    ("support", "🆘 Support @username"),
    ("min_dep", "⬇️ Min Deposit (₹)"),
    ("max_dep", "⬆️ Max Deposit (₹)"),
    ("ref_reward", "🪙 Refer Reward (coin, 0 = band)"),
    ("bonus_pct", "🎁 Bonus % (0 = band)"),
    ("bonus_min", "🎁 Bonus ke liye Min Deposit (₹)"),
]
NUMERIC_SETTINGS = {"min_dep", "max_dep", "ref_reward", "bonus_pct", "bonus_min"}


def blocked_name(name):
    u = str(name or "").upper()
    return any(w in u for w in BLOCKED_WORDS)


# ================= DATABASE =================
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None, timeout=30)
conn.row_factory = sqlite3.Row
try:
    conn.execute("PRAGMA journal_mode=WAL")
except sqlite3.Error:
    pass

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY, username TEXT,
        balance INTEGER NOT NULL DEFAULT 0, banned INTEGER NOT NULL DEFAULT 0,
        joined TEXT NOT NULL DEFAULT (datetime('now')))""",
    """CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, price INTEGER NOT NULL,
        kind TEXT NOT NULL, content TEXT NOT NULL DEFAULT '', descr TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1)""",
    """CREATE TABLE IF NOT EXISTS stock(
        id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL,
        data TEXT NOT NULL, sold INTEGER NOT NULL DEFAULT 0, sold_to INTEGER)""",
    """CREATE TABLE IF NOT EXISTS deposits(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        amount INTEGER NOT NULL, utr TEXT NOT NULL, shot TEXT,
        status TEXT NOT NULL DEFAULT 'pending', created TEXT NOT NULL DEFAULT (datetime('now')))""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_utr ON deposits(utr) WHERE status <> 'rejected'",
    """CREATE TABLE IF NOT EXISTS purchases(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL, price INTEGER NOT NULL, keydata TEXT,
        created TEXT NOT NULL DEFAULT (datetime('now')))""",
    """CREATE TABLE IF NOT EXISTS codes(
        code TEXT PRIMARY KEY, amount INTEGER NOT NULL,
        used INTEGER NOT NULL DEFAULT 0, used_by INTEGER)""",
    """CREATE TABLE IF NOT EXISTS channels(
        id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER UNIQUE NOT NULL,
        title TEXT NOT NULL DEFAULT '', link TEXT NOT NULL DEFAULT '')""",
    """CREATE TABLE IF NOT EXISTS referrals(
        referred_id INTEGER PRIMARY KEY, referrer_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', reward INTEGER NOT NULL DEFAULT 0,
        updated TEXT NOT NULL DEFAULT (datetime('now')))""",
    """CREATE TABLE IF NOT EXISTS ledger(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        amount INTEGER NOT NULL, reason TEXT NOT NULL,
        created TEXT NOT NULL DEFAULT (datetime('now')))""",
    "CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS premium_emoji(fallback TEXT PRIMARY KEY, emoji_id TEXT NOT NULL)",
]
for _s in SCHEMA:
    conn.execute(_s)


def q(sql, a=()):
    return conn.execute(sql, a).fetchall()


def q1(sql, a=()):
    r = conn.execute(sql, a).fetchone()
    return (r[0] or 0) if r else 0


def x(sql, a=()):
    return conn.execute(sql, a)


def S(key):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else DEFAULTS[key]


def SI(key):
    try:
        return int(S(key))
    except ValueError:
        return int(DEFAULTS[key])


def set_setting(key, value):
    x("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


# ================= PREMIUM EMOJI =================
PREM = {}


def load_prem():
    PREM.clear()
    PREM.update({r[0]: r[1] for r in q("SELECT fallback, emoji_id FROM premium_emoji")})


def prem(text):
    """Text me jahan bhi saved emoji ho, use Telegram premium (animated) emoji se badal deta hai."""
    if not PREM or not isinstance(text, str):
        return text
    pat = re.compile("|".join(re.escape(k) for k in sorted(PREM, key=len, reverse=True)))
    return pat.sub(lambda m: f'<tg-emoji emoji-id="{PREM[m.group(0)]}">{m.group(0)}</tg-emoji>', text)


def custom_emojis(m: Message):
    """Message me se (fallback, custom_emoji_id) list."""
    out = []
    text = m.text or m.caption or ""
    for en in (m.entities or m.caption_entities or []):
        if en.type == "custom_emoji" and en.custom_emoji_id:
            fb = text.encode("utf-16-le")[en.offset * 2:(en.offset + en.length) * 2].decode("utf-16-le", "ignore")
            out.append((fb or "⭐", en.custom_emoji_id))
    return out


load_prem()


# ================= HELPERS =================
def e(s):
    return html.escape(str(s))


def card(title, body=""):
    return prem(f"💎 <b>{e(title)}</b>\n{LINE}\n{body}")


def to_ist(s):
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(IST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def ensure_user(uid, username):
    x("INSERT INTO users(id, username) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET username=excluded.username", (uid, username))


def get_balance(uid):
    return q1("SELECT balance FROM users WHERE id=?", (uid,))


def is_banned(uid):
    return bool(q1("SELECT banned FROM users WHERE id=?", (uid,)))


def user_label(uid):
    r = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
    return f"@{r['username']}" if r and r["username"] else f"User {uid}"


def stock_left(pid):
    return q1("SELECT COUNT(*) FROM stock WHERE product_id=? AND sold=0", (pid,))


def bonus_for(amount):
    pct = SI("bonus_pct")
    return amount * pct // 100 if pct > 0 and amount >= SI("bonus_min") else 0


class Wallet:
    @staticmethod
    def add(uid, amount, reason):
        x("UPDATE users SET balance=balance+? WHERE id=?", (int(amount), uid))
        x("INSERT INTO ledger(user_id, amount, reason) VALUES(?,?,?)", (uid, int(amount), str(reason)[:80]))


def buy(uid, pid):
    p = conn.execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()
    if not p:
        return "gone", None, None
    if p["kind"] == "keys" and stock_left(pid) == 0:
        return "nostock", p, None
    cur = x("UPDATE users SET balance=balance-? WHERE id=? AND balance>=?", (p["price"], uid, p["price"]))
    if cur.rowcount == 0:
        return "nobalance", p, None
    key = sid = None
    if p["kind"] == "keys":
        s = conn.execute("SELECT id, data FROM stock WHERE product_id=? AND sold=0 ORDER BY id LIMIT 1", (pid,)).fetchone()
        if not s:
            x("UPDATE users SET balance=balance+? WHERE id=?", (p["price"], uid))
            return "nostock", p, None
        x("UPDATE stock SET sold=1, sold_to=? WHERE id=?", (uid, s["id"]))
        sid, key = s["id"], s["data"]
    ins = x("INSERT INTO purchases(user_id, product_id, price, keydata) VALUES(?,?,?,?)", (uid, pid, p["price"], key))
    x("INSERT INTO ledger(user_id, amount, reason) VALUES(?,?,?)", (uid, -p["price"], f"Purchase: {p['name']}"[:80]))
    return "ok", p, {"pur": ins.lastrowid, "sid": sid, "key": key}


def undo_buy(uid, price, info):
    x("DELETE FROM purchases WHERE id=?", (info["pur"],))
    if info["sid"]:
        x("UPDATE stock SET sold=0, sold_to=NULL WHERE id=?", (info["sid"],))
    Wallet.add(uid, price, "Refund: delivery fail")


async def deliver(bot: Bot, uid: int, p, key=None):
    cap = f"📦 <b>{e(p['name'])}</b>"
    if p["kind"] == "keys":
        await bot.send_message(uid, prem(f"{cap}\n{LINE}\n🔑 <code>{e(key)}</code>"), protect_content=True)
    elif p["kind"] == "text":
        await bot.send_message(uid, prem(f"{cap}\n{LINE}\n{e(p['content'])}"), protect_content=True)
    elif p["kind"] == "document":
        await bot.send_document(uid, p["content"], caption=prem(cap), protect_content=True)
    elif p["kind"] == "video":
        await bot.send_video(uid, p["content"], caption=prem(cap), protect_content=True)
    elif p["kind"] == "photo":
        await bot.send_photo(uid, p["content"], caption=prem(cap), protect_content=True)


async def show(c: CallbackQuery, text, markup=None):
    m = c.message
    if m is None:
        return await c.bot.send_message(c.from_user.id, text, reply_markup=markup)
    try:
        if m.text is None:
            try:
                await m.delete()
            except Exception:
                pass
            await m.answer(text, reply_markup=markup)
        else:
            await m.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as ex:
        if "not modified" not in str(ex):
            await m.answer(text, reply_markup=markup)


# ================= FORCE-JOIN + REFERRALS =================
_join_cache = {}


async def missing_channels(bot: Bot, uid, fresh=False):
    chans = q("SELECT chat_id, title, link FROM channels ORDER BY id")
    if not chans:
        return []
    now = time.time()
    if not fresh and uid in _join_cache and now - _join_cache[uid][0] < JOIN_CACHE:
        return _join_cache[uid][1]
    miss = []
    for ch in chans:
        try:
            m = await bot.get_chat_member(ch["chat_id"], uid)
            if getattr(m.status, "value", m.status) in ("left", "kicked"):
                miss.append(ch)
        except Exception as ex:
            logging.warning("membership check fail (%s): %s", ch["chat_id"], ex)
    _join_cache[uid] = (now, miss)
    return miss


def join_text(missing):
    lines = "\n".join(f"{i}. 📢 <b>{e(ch['title'] or 'Channel')}</b>" for i, ch in enumerate(missing, 1))
    return card("Join Required", f"🔒 Bot use karne ke liye pehle ye channel join karo:\n\n{lines}\n\nJoin karke <b>Verify</b> dabao 👇")


def join_markup(missing):
    b = InlineKeyboardBuilder()
    for ch in missing:
        if ch["link"]:
            b.button(text=f"📢 Join • {ch['title'] or 'Channel'}", url=ch["link"])
    b.button(text="✅ Maine Join Kar Liya • Verify", callback_data="chk")
    b.adjust(1)
    return b.as_markup()


async def ref_sync(bot: Bot, uid, verify=False):
    row = conn.execute("SELECT * FROM referrals WHERE referred_id=?", (uid,)).fetchone()
    if not row or not q("SELECT 1 FROM channels LIMIT 1"):
        return
    joined = not await missing_channels(bot, uid, fresh=True)
    st, ref, who, reward = row["status"], row["referrer_id"], user_label(uid), SI("ref_reward")
    if joined and reward > 0 and (st == "revoked" or (st == "pending" and verify)):
        x("UPDATE referrals SET status='rewarded', reward=?, updated=datetime('now') WHERE referred_id=?", (reward, uid))
        Wallet.add(ref, reward, f"Refer: {who} joined")
        text = f"🎉 {e(who)} ne sab channel join kar liye.\n🪙 <b>+{reward} coin</b> mil gaya."
    elif not joined and st == "rewarded":
        x("UPDATE referrals SET status='revoked', updated=datetime('now') WHERE referred_id=?", (uid,))
        Wallet.add(ref, -row["reward"], f"Refer cut: {who} left")
        text = f"⚠️ {e(who)} ne channel chhod diya.\n🪙 <b>-{row['reward']} coin</b> cut gaya."
    else:
        return
    try:
        await bot.send_message(ref, card("Refer & Earn", text))
    except Exception:
        pass


# ================= KEYBOARDS =================
def main_menu(uid):
    b = InlineKeyboardBuilder()
    b.button(text="🛍 Shop", callback_data="products")
    b.button(text="➕ Add Balance", callback_data="addbal")
    b.button(text="💰 Wallet & Orders", callback_data="wallet")
    b.button(text="🎟 Redeem Code", callback_data="redeem")
    b.button(text="🎁 Refer & Earn", callback_data="refer")
    b.button(text="❓ Help", callback_data="help")
    b.button(text="🆘 Support", callback_data="support")
    b.button(text="🔄 Refresh", callback_data="menu")
    rows = [2, 2, 2, 2]
    if uid == ADMIN_ID:
        b.button(text="🛠 Admin Panel", callback_data="adm:home")
        rows.append(1)
    b.adjust(*rows)
    return b.as_markup()


def back_kb(extra=None):
    b = InlineKeyboardBuilder()
    if extra:
        b.button(text=extra[0], callback_data=extra[1])
    b.button(text="⬅️ Menu", callback_data="menu")
    b.adjust(1)
    return b.as_markup()


def home_text(uid, name):
    orders = q1("SELECT COUNT(*) FROM purchases WHERE user_id=?", (uid,))
    refs = q1("SELECT COUNT(*) FROM referrals WHERE referrer_id=? AND status='rewarded'", (uid,))
    return card(
        S("brand"),
        f"👋 Welcome, <b>{e(name)}</b>\n\n💰 Balance: <b>₹{get_balance(uid)}</b>\n"
        f"📦 Orders: <b>{orders}</b>\n🎁 Referrals: <b>{refs}</b>\n\n"
        "⚡ Instant Delivery  •  🔒 Secure  •  🕒 24/7",
    )


# ================= STATES =================
class Dep(StatesGroup):
    amount = State()
    shot = State()
    utr = State()


class Red(StatesGroup):
    code = State()


class AddP(StatesGroup):
    name = State()
    price = State()
    descr = State()
    content = State()


class StockAdd(StatesGroup):
    data = State()


class PriceEd(StatesGroup):
    value = State()


class Gen(StatesGroup):
    amount = State()


class Bcast(StatesGroup):
    text = State()


class SetS(StatesGroup):
    value = State()


class ChanAdd(StatesGroup):
    chat = State()
    link = State()


class PE(StatesGroup):
    wait = State()


# ================= MIDDLEWARE =================
class Gate(BaseMiddleware):
    """Ban check + force-join check. Admin ko skip karta hai."""

    async def __call__(self, handler, event, data):
        db_user = data.get("event_from_user")
        if not db_user or db_user.id == ADMIN_ID:
            return await handler(event, data)
        if is_banned(db_user.id):
            if isinstance(event, CallbackQuery):
                await event.answer("🚫 Tumhara account band hai.", show_alert=True)
            elif isinstance(event, Message):
                await event.answer("🚫 Tumhara account band hai.")
            return
        if isinstance(event, Message) and (event.text or "").startswith("/start"):
            return await handler(event, data)
        if isinstance(event, CallbackQuery) and event.data == "chk":
            return await handler(event, data)
        missing = await missing_channels(data["bot"], db_user.id)
        if not missing:
            return await handler(event, data)
        state = data.get("state")
        if state:
            await state.clear()
        if isinstance(event, CallbackQuery):
            await event.answer("🔒 Pehle channel join karo", show_alert=True)
            await show(event, join_text(missing), join_markup(missing))
        elif isinstance(event, Message):
            await event.answer(join_text(missing), reply_markup=join_markup(missing))
        return


cmd_r = Router()
admin_r = Router()
user_r = Router()
admin_r.message.filter(F.from_user.id == ADMIN_ID)
admin_r.callback_query.filter(F.from_user.id == ADMIN_ID)


# ================= START / VERIFY =================
async def open_home(m: Message, bot: Bot, user):
    if user.id != ADMIN_ID:
        missing = await missing_channels(bot, user.id, fresh=True)
        if missing:
            return await m.answer(join_text(missing), reply_markup=join_markup(missing))
        await ref_sync(bot, user.id, verify=True)
    await m.answer(home_text(user.id, user.first_name), reply_markup=main_menu(user.id))


@cmd_r.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext, bot: Bot, command: CommandObject):
    await state.clear()
    uid = m.from_user.id
    is_new = not q1("SELECT COUNT(*) FROM users WHERE id=?", (uid,))
    ensure_user(uid, m.from_user.username)
    arg = (command.args or "").strip()
    if is_new and arg.startswith("ref_") and arg[4:].isdigit():
        ref_id = int(arg[4:])
        if ref_id != uid and q1("SELECT COUNT(*) FROM users WHERE id=?", (ref_id,)):
            x("INSERT OR IGNORE INTO referrals(referred_id, referrer_id) VALUES(?,?)", (uid, ref_id))
    await open_home(m, bot, m.from_user)


@cmd_r.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    await open_home(m, bot, m.from_user)


@cmd_r.callback_query(F.data == "chk")
async def cb_verify(c: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    ensure_user(c.from_user.id, c.from_user.username)
    missing = await missing_channels(bot, c.from_user.id, fresh=True)
    if missing:
        await c.answer("❌ Abhi tumne saare channel join nahi kiye!", show_alert=True)
        return await show(c, join_text(missing), join_markup(missing))
    await ref_sync(bot, c.from_user.id, verify=True)
    await c.answer("✅ Verified!")
    await show(c, home_text(c.from_user.id, c.from_user.first_name), main_menu(c.from_user.id))


@cmd_r.chat_member()
async def on_chat_member(ev: ChatMemberUpdated, bot: Bot):
    if ev.chat.id not in {r["chat_id"] for r in q("SELECT chat_id FROM channels")}:
        return
    uid = ev.new_chat_member.user.id
    _join_cache.pop(uid, None)
    if q1("SELECT COUNT(*) FROM referrals WHERE referred_id=?", (uid,)):
        await ref_sync(bot, uid)


@user_r.callback_query(F.data == "menu")
async def cb_menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    ensure_user(c.from_user.id, c.from_user.username)
    await show(c, home_text(c.from_user.id, c.from_user.first_name), main_menu(c.from_user.id))
    await c.answer()


@user_r.callback_query(F.data == "support")
async def cb_support(c: CallbackQuery, state: FSMContext):
    await state.clear()
    sup = S("support")
    b = InlineKeyboardBuilder()
    if re.fullmatch(r"@[A-Za-z0-9_]{4,32}", sup):
        b.button(text="💬 Support se Chat", url=f"https://t.me/{sup[1:]}")
    b.button(text="⬅️ Menu", callback_data="menu")
    b.adjust(1)
    await show(c, card("Support", f"Koi problem ho to yaha contact karo:\n👉 {e(sup)}\n\nDeposit ka issue ho to UTR saath bhejo."), b.as_markup())
    await c.answer()


@user_r.callback_query(F.data == "help")
async def cb_help(c: CallbackQuery, state: FSMContext):
    await state.clear()
    t = (
        "<b>🛍 Kaise kharidein</b>\n1️⃣ ➕ Add Balance se wallet recharge karo\n"
        "2️⃣ 🛍 Shop se product chuno\n3️⃣ ✅ Buy Now — turant delivery\n\n"
        "<b>💳 Balance add</b>\nUPI QR scan → Maine Pay Kar Diya → screenshot → UTR → admin approve\n\n"
        "<b>🎁 Free coin</b>\nRefer & Earn se dost invite karo\n\n"
        "⚠️ Digital product ka refund nahi hota."
    )
    await show(c, card("Help", t), back_kb(("🛍 Shop", "products")))
    await c.answer()


# ================= WALLET / ORDERS / REDEEM / REFER =================
@user_r.callback_query(F.data == "wallet")
async def cb_wallet(c: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = c.from_user.id
    spent = q1("SELECT COALESCE(SUM(price),0) FROM purchases WHERE user_id=?", (uid,))
    added = q1("SELECT COALESCE(SUM(amount),0) FROM deposits WHERE user_id=? AND status='approved'", (uid,))
    acts = q("SELECT amount, reason, created FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT 6", (uid,))
    lines = "\n".join(f"{'🟢 +' if a['amount'] > 0 else '🔴 -'}{abs(a['amount'])} • {e(a['reason'])}" for a in acts) or "Abhi koi activity nahi."
    t = (
        f"💰 Balance: <b>₹{get_balance(uid)}</b>\n📥 Total added: ₹{added}  •  🛒 Total spent: ₹{spent}\n\n"
        f"<b>📜 Recent activity</b>\n{lines}"
    )
    b = InlineKeyboardBuilder()
    b.button(text="➕ Add Balance", callback_data="addbal")
    b.button(text="📦 My Orders", callback_data="orders")
    b.button(text="⬅️ Menu", callback_data="menu")
    b.adjust(2, 1)
    await show(c, card("Wallet", t), b.as_markup())
    await c.answer()


@user_r.callback_query(F.data == "orders")
async def cb_orders(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rows = q("SELECT pu.id, p.name, pu.price FROM purchases pu JOIN products p ON p.id=pu.product_id "
             "WHERE pu.user_id=? ORDER BY pu.id DESC LIMIT 10", (c.from_user.id,))
    if not rows:
        await show(c, card("My Orders", "Abhi tak koi order nahi."), back_kb(("🛍 Shop", "products")))
        return await c.answer()
    b = InlineKeyboardBuilder()
    for r in rows:
        b.button(text=f"📥 {r['name']} • ₹{r['price']}", callback_data=f"ord:{r['id']}")
    b.button(text="⬅️ Menu", callback_data="menu")
    b.adjust(1)
    await show(c, card("My Orders", "Kisi par tap karo, wo dobara mil jayega:"), b.as_markup())
    await c.answer()


@user_r.callback_query(F.data.startswith("ord:"))
async def cb_order_again(c: CallbackQuery, bot: Bot):
    if not c.data[4:].isdigit():
        return await c.answer("Order nahi mila", show_alert=True)
    pu = conn.execute("SELECT * FROM purchases WHERE id=? AND user_id=?", (int(c.data[4:]), c.from_user.id)).fetchone()
    p = conn.execute("SELECT * FROM products WHERE id=?", (pu["product_id"],)).fetchone() if pu else None
    if not pu or not p:
        return await c.answer("Order ya product nahi mila", show_alert=True)
    try:
        await deliver(bot, c.from_user.id, p, pu["keydata"])
        await c.answer("✅ Dobara bhej diya")
    except Exception as ex:
        logging.exception(ex)
        await c.answer("Bhejne me dikkat aayi, support se baat karo.", show_alert=True)


@user_r.callback_query(F.data == "redeem")
async def cb_redeem(c: CallbackQuery, state: FSMContext):
    await state.set_state(Red.code)
    await show(c, card("Redeem", "🎟 Apna balance code bhejo:\n\n/cancel se wapas jao"), back_kb())
    await c.answer()


async def do_redeem(m: Message, code: str, state: FSMContext):
    ensure_user(m.from_user.id, m.from_user.username)
    await state.clear()
    row = conn.execute("SELECT amount FROM codes WHERE code=? AND used=0", (code.strip().upper(),)).fetchone()
    if not row:
        return await m.answer("❌ Code galat hai ya pehle use ho chuka hai.", reply_markup=back_kb())
    cur = x("UPDATE codes SET used=1, used_by=? WHERE code=? AND used=0", (m.from_user.id, code.strip().upper()))
    if cur.rowcount == 0:
        return await m.answer("❌ Ye code abhi use ho chuka hai.", reply_markup=back_kb())
    Wallet.add(m.from_user.id, row["amount"], f"Code {code.strip().upper()}")
    await m.answer(card("Redeemed", f"✅ ₹{row['amount']} wallet me add ho gaye!\n💰 Balance: <b>₹{get_balance(m.from_user.id)}</b>"), reply_markup=main_menu(m.from_user.id))


@user_r.message(Command("redeem"))
async def cmd_redeem(m: Message, state: FSMContext):
    parts = (m.text or "").split()
    if len(parts) < 2:
        await state.set_state(Red.code)
        return await m.answer("🎟 Apna balance code bhejo:")
    await do_redeem(m, parts[1], state)


@user_r.message(Red.code, F.text)
async def red_code(m: Message, state: FSMContext):
    await do_redeem(m, m.text, state)


@user_r.callback_query(F.data == "refer")
async def cb_refer(c: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = c.from_user.id
    if SI("ref_reward") <= 0:
        await show(c, card("Refer & Earn", "Refer system abhi band hai."), back_kb())
        return await c.answer()
    link = f"https://t.me/{(await c.bot.get_me()).username}?start=ref_{uid}"
    total = q1("SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (uid,))
    active = q1("SELECT COUNT(*) FROM referrals WHERE referrer_id=? AND status='rewarded'", (uid,))
    earned = q1("SELECT COALESCE(SUM(reward),0) FROM referrals WHERE referrer_id=? AND status='rewarded'", (uid,))
    t = (
        f"Dost ko invite karo, har successful refer pe <b>{SI('ref_reward')} coin</b> (1 coin = ₹1).\n\n"
        "Dost sab channel join karke Verify kare to coin milega. Channel chhode to coin cut ho jayega.\n\n"
        f"👥 Total: <b>{total}</b>  •  ✅ Active: <b>{active}</b>  •  🪙 Kamai: <b>{earned}</b>\n\n"
        f"🔗 Tumhara link:\n<code>{link}</code>"
    )
    b = InlineKeyboardBuilder()
    b.button(text="📤 Link Share Karo", url=f"https://t.me/share/url?url={quote(link)}&text={quote(S('brand') + ' - yaha se shopping karo!')}")
    b.button(text="⬅️ Menu", callback_data="menu")
    b.adjust(1)
    await show(c, card("Refer & Earn", t), b.as_markup())
    await c.answer()


# ================= SHOP =================
@user_r.callback_query(F.data == "products")
async def cb_products(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_products(c)


async def show_products(c: CallbackQuery):
    rows = q("SELECT * FROM products WHERE active=1 ORDER BY id")
    if not rows:
        await show(c, card("Shop", "Abhi koi product nahi hai."), back_kb())
        return await c.answer()
    b = InlineKeyboardBuilder()
    for r in rows:
        if r["kind"] == "keys" and stock_left(r["id"]) == 0:
            b.button(text=f"🔴 {r['name']} • Sold out", callback_data=f"buy:{r['id']}")
        else:
            b.button(text=f"🟢 {r['name']} • ₹{r['price']}", callback_data=f"buy:{r['id']}")
    b.button(text="⬅️ Menu", callback_data="menu")
    b.adjust(1)
    await show(c, card("Shop", f"📦 <b>{len(rows)}</b> product available\n\nKharidne ke liye product chuno 👇"), b.as_markup())
    await c.answer()


@user_r.callback_query(F.data.startswith("buy:"))
async def cb_buy_view(c: CallbackQuery):
    if not c.data[4:].isdigit():
        return await c.answer("Product nahi mila", show_alert=True)
    pid = int(c.data[4:])
    p = conn.execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()
    if not p:
        return await c.answer("Product nahi mila", show_alert=True)
    bal = get_balance(c.from_user.id)
    soldout = False
    t = f"🏷 <b>{e(p['name'])}</b>\n"
    if p["descr"]:
        t += f"📝 {e(p['descr'])}\n"
    t += f"\n💵 Price: <b>₹{p['price']}</b>\n"
    if p["kind"] == "keys":
        left = stock_left(pid)
        soldout = left == 0
        t += f"📦 Stock: <b>{'🔴 Sold out' if soldout else '🟢 ' + str(left) + ' available'}</b>\n"
    t += f"💰 Tumhara balance: ₹{bal}\n\n⚠️ Digital product, kharidne ke baad refund nahi."
    b = InlineKeyboardBuilder()
    if not soldout:
        if bal >= p["price"]:
            b.button(text=f"✅ Buy Now • ₹{p['price']}", callback_data=f"buyok:{pid}")
        else:
            b.button(text=f"➕ Add Balance (₹{p['price'] - bal} aur chahiye)", callback_data="addbal")
    b.button(text="⬅️ Shop", callback_data="products")
    b.adjust(1)
    await show(c, card("Product", t), b.as_markup())
    await c.answer()


@user_r.callback_query(F.data.startswith("buyok:"))
async def cb_buy_ok(c: CallbackQuery, bot: Bot):
    uid = c.from_user.id
    if not c.data[6:].isdigit():
        return await c.answer("Product nahi mila", show_alert=True)
    status, p, info = buy(uid, int(c.data[6:]))
    if status == "gone":
        return await c.answer("Product available nahi", show_alert=True)
    if status == "nostock":
        return await c.answer("🔴 Stock khatam ho gaya.", show_alert=True)
    if status == "nobalance":
        return await c.answer(f"Balance kam hai! Chahiye ₹{p['price']}, hai ₹{get_balance(uid)}.", show_alert=True)
    try:
        await deliver(bot, uid, p, info["key"])
    except Exception as ex:
        logging.exception(ex)
        undo_buy(uid, p["price"], info)
        await show(c, card("Failed", "❌ Delivery fail hui. Paisa wapas wallet me aa gaya."), back_kb())
        return await c.answer()
    await show(c, card("Order Complete", f"✅ <b>{e(p['name'])}</b> mil gaya!\n💰 Bacha balance: ₹{get_balance(uid)}"), back_kb(("🛍 Aur Shopping", "products")))
    try:
        await bot.send_message(ADMIN_ID, f"🛒 <b>Sale</b>: {e(p['name'])} • ₹{p['price']}\nUser: <code>{uid}</code> {e(user_label(uid))}")
    except Exception:
        pass
    await c.answer()


# ================= ADD BALANCE (UPI + screenshot + UTR) =================
def make_qr_png(link, amount):
    if qrcode is None:
        return None
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(link)
    qr.make(fit=True)
    base = qr.make_image(fill_color="black", back_color="white")
    try:
        from PIL import Image, ImageDraw, ImageFont

        def font(size):
            for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf"):
                try:
                    return ImageFont.truetype(name, size)
                except Exception:
                    pass
            return ImageFont.load_default()

        img = base.convert("RGB")
        W, H = img.width + 80, img.height + 180
        canvas = Image.new("RGB", (W, H), "#ffffff")
        d = ImageDraw.Draw(canvas)
        d.rectangle([0, 0, W, 96], fill="#5b3df5")

        def center(text, y, f, fill):
            w = d.textlength(text, font=f)
            d.text(((W - w) / 2, y), text, font=f, fill=fill)

        center("SCAN & PAY", 28, font(38), "#ffffff")
        canvas.paste(img, (40, 116))
        center(f"Rs. {amount}", 116 + img.height + 6, font(40), "#111111")
        center("Any UPI app", 116 + img.height + 56, font(24), "#666666")
        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        buf = io.BytesIO()
        base.save(buf, format="PNG")
        return buf.getvalue()


@user_r.callback_query(F.data == "addbal")
async def cb_addbal(c: CallbackQuery, state: FSMContext):
    await state.clear()
    lo, hi = SI("min_dep"), SI("max_dep")
    b = InlineKeyboardBuilder()
    amounts = [a for a in (50, 100, 200, 500, 1000) if lo <= a <= hi]
    for a in amounts:
        b.button(text=f"₹{a}", callback_data=f"amt:{a}")
    b.button(text="✏️ Custom amount", callback_data="amt:custom")
    b.button(text="⬅️ Menu", callback_data="menu")
    b.adjust(3, 1, 1) if len(amounts) == 3 else b.adjust(3)
    t = f"Kitna balance add karna hai?\n\n⬇️ Min: ₹{lo}   ⬆️ Max: ₹{hi}"
    if SI("bonus_pct") > 0:
        t += f"\n🎁 ₹{SI('bonus_min')}+ deposit pe <b>{SI('bonus_pct')}% bonus</b>!"
    await show(c, card("Add Balance", t), b.as_markup())
    await c.answer()


async def send_pay(target: Message, amt: int, state: FSMContext):
    upi = S("upi_id").strip()
    if not upi:
        return await target.answer(card("Payment", "⚠️ Abhi payment available nahi hai. Thodi der baad try karo."), reply_markup=back_kb())
    await state.update_data(amount=amt)
    bonus = bonus_for(amt)
    t = card(
        "Payment",
        f"💵 Amount: <b>₹{amt}</b>\n"
        + (f"🎁 Bonus: <b>+₹{bonus}</b>\n" if bonus else "")
        + f"\n🏦 UPI ID: <code>{e(upi)}</code>\n👤 Name: {e(S('upi_name'))}\n\n"
        f"1️⃣ QR scan karo ya UPI ID pe <b>exact ₹{amt}</b> bhejo\n"
        "2️⃣ Neeche <b>Maine Pay Kar Diya</b> dabao\n3️⃣ Screenshot bhejo\n4️⃣ UTR number bhejo\n"
        "5️⃣ Admin approve karega → balance add ✅",
    )
    b = InlineKeyboardBuilder()
    b.button(text="✅ Maine Pay Kar Diya", callback_data="paid")
    b.button(text="❌ Cancel", callback_data="menu")
    b.adjust(1)
    link = f"upi://pay?pa={quote(upi)}&pn={quote(S('upi_name'))}&am={amt}&cu=INR&tn={quote('Wallet topup')}"
    try:
        png = make_qr_png(link, amt)
    except Exception as ex:
        logging.warning("QR fail: %s", ex)
        png = None
    if png:
        await target.answer_photo(BufferedInputFile(png, "upi_qr.png"), caption=t, reply_markup=b.as_markup())
    else:
        await target.answer(t, reply_markup=b.as_markup())


@user_r.callback_query(F.data.startswith("amt:"))
async def cb_amt(c: CallbackQuery, state: FSMContext):
    v = c.data[4:]
    if v == "custom":
        await state.set_state(Dep.amount)
        await show(c, card("Add Balance", f"Amount likho (₹{SI('min_dep')} se ₹{SI('max_dep')}), sirf number:"), back_kb())
        return await c.answer()
    if not v.isdigit() or not SI("min_dep") <= int(v) <= SI("max_dep"):
        return await c.answer("Amount sahi nahi", show_alert=True)
    try:
        await c.message.delete()
    except Exception:
        pass
    await send_pay(c.message, int(v), state)
    await c.answer()


@user_r.message(Dep.amount, F.text)
async def dep_amount(m: Message, state: FSMContext):
    if not m.text.isdigit() or not SI("min_dep") <= int(m.text) <= SI("max_dep"):
        return await m.answer(f"Sahi amount likho (₹{SI('min_dep')} se ₹{SI('max_dep')}).")
    await send_pay(m, int(m.text), state)


@user_r.callback_query(F.data == "paid")
async def cb_paid(c: CallbackQuery, state: FSMContext):
    if "amount" not in await state.get_data():
        await state.clear()
        await show(c, home_text(c.from_user.id, c.from_user.first_name), main_menu(c.from_user.id))
        return await c.answer("Session expire ho gaya, dobara try karo.", show_alert=True)
    if q1("SELECT COUNT(*) FROM deposits WHERE user_id=? AND status='pending'", (c.from_user.id,)) >= 3:
        await state.clear()
        await show(c, card("Pending", "⏳ 3 deposit pehle se pending hain. Approve hone ka wait karo."), back_kb())
        return await c.answer()
    await state.set_state(Dep.shot)
    await show(c, card("Verify", "📸 Ab payment ka <b>screenshot</b> bhejo.\n\n/cancel se wapas jao"), back_kb())
    await c.answer()


@user_r.message(Dep.shot, F.photo)
async def dep_shot(m: Message, state: FSMContext):
    await state.update_data(shot=m.photo[-1].file_id)
    await state.set_state(Dep.utr)
    await m.answer(card("Verify", "✅ Screenshot mil gaya.\n\nAb <b>UTR / Transaction ID</b> bhejo (12 digit)"))


@user_r.message(Dep.shot)
async def dep_shot_bad(m: Message):
    await m.answer("📸 Screenshot (photo) bhejo.")


@user_r.message(Dep.utr, F.text)
async def dep_utr(m: Message, state: FSMContext, bot: Bot):
    utr = m.text.strip().upper()
    if not utr.isalnum() or not 8 <= len(utr) <= 30:
        return await m.answer("UTR sahi nahi lag raha. 12 digit ka transaction ID bhejo.")
    if q1("SELECT COUNT(*) FROM deposits WHERE utr=? AND status!='rejected'", (utr,)):
        return await m.answer("❌ Ye UTR pehle hi use ho chuka hai.")
    d = await state.get_data()
    if "amount" not in d or "shot" not in d:
        await state.clear()
        return await m.answer("Session expire ho gaya, dobara Add Balance se shuru karo.", reply_markup=back_kb())
    ensure_user(m.from_user.id, m.from_user.username)
    dep_id = x("INSERT INTO deposits(user_id, amount, utr, shot) VALUES(?,?,?,?)",
               (m.from_user.id, d["amount"], utr, d["shot"])).lastrowid
    await state.clear()
    dep = conn.execute("SELECT * FROM deposits WHERE id=?", (dep_id,)).fetchone()
    try:
        await bot.send_photo(ADMIN_ID, dep["shot"], caption=dep_caption(dep), reply_markup=dep_buttons(dep_id))
    except Exception as ex:
        logging.warning("admin ko deposit bhejne me dikkat: %s", ex)
        await bot.send_message(ADMIN_ID, dep_caption(dep), reply_markup=dep_buttons(dep_id))
    await m.answer(card("Submitted", f"✅ Request #{dep_id} admin ko bhej di gayi.\nApprove hote hi balance add ho jayega."), reply_markup=back_kb())


def dep_caption(d):
    bonus = bonus_for(d["amount"])
    return (
        f"💸 <b>Deposit #{d['id']}</b>\nUser: <code>{d['user_id']}</code> {e(user_label(d['user_id']))}\n"
        f"Paid: <b>₹{d['amount']}</b>" + (f" (+₹{bonus} bonus)" if bonus else "")
        + f"\nUTR: <code>{e(d['utr'])}</code>\n\n⚠️ Bank app me paisa check karke Approve dabao."
    )


def dep_buttons(dep_id):
    b = InlineKeyboardBuilder()
    b.button(text="✅ Approve", callback_data=f"ap:{dep_id}")
    b.button(text="❌ Reject", callback_data=f"rj:{dep_id}")
    return b.as_markup()


@admin_r.callback_query(F.data.startswith("ap:"))
async def adm_approve(c: CallbackQuery, bot: Bot):
    dep_id = int(c.data[3:])
    cur = x("UPDATE deposits SET status='approved' WHERE id=? AND status='pending'", (dep_id,))
    if cur.rowcount == 0:
        return await c.answer("Ye pehle hi handle ho chuka hai", show_alert=True)
    d = conn.execute("SELECT * FROM deposits WHERE id=?", (dep_id,)).fetchone()
    bonus = bonus_for(d["amount"])
    Wallet.add(d["user_id"], d["amount"] + bonus, f"Deposit #{dep_id}" + (f" (+₹{bonus} bonus)" if bonus else ""))
    try:
        await bot.send_message(d["user_id"], card("Payment Approved", f"✅ ₹{d['amount']} receive ho gaye!" + (f"\n🎁 Bonus: +₹{bonus}" if bonus else "") + f"\n💰 Balance: <b>₹{get_balance(d['user_id'])}</b>"))
        sent = "user ko message gaya"
    except Exception:
        sent = "balance add ho gaya, user ko message nahi gaya"
    await mark_done(c, f"\n\n✅ APPROVED • ₹{d['amount'] + bonus}\n{sent}")
    await c.answer("Approved")


@admin_r.callback_query(F.data.startswith("rj:"))
async def adm_reject(c: CallbackQuery, bot: Bot):
    dep_id = int(c.data[3:])
    cur = x("UPDATE deposits SET status='rejected' WHERE id=? AND status='pending'", (dep_id,))
    if cur.rowcount == 0:
        return await c.answer("Ye pehle hi handle ho chuka hai", show_alert=True)
    d = conn.execute("SELECT * FROM deposits WHERE id=?", (dep_id,)).fetchone()
    try:
        await bot.send_message(d["user_id"], card("Deposit Rejected", f"❌ ₹{d['amount']} ka deposit reject ho gaya. Galti lage to {e(S('support'))} se baat karo."))
    except Exception:
        pass
    await mark_done(c, "\n\n❌ REJECTED")
    await c.answer("Rejected")


async def mark_done(c: CallbackQuery, suffix):
    try:
        if c.message.caption is not None:
            await c.message.edit_caption(caption=c.message.caption + suffix, reply_markup=None)
        else:
            await c.message.edit_text((c.message.text or "") + suffix, reply_markup=None)
    except Exception as ex:
        logging.warning("admin message edit fail: %s", ex)


# ================= ADMIN PANEL =================
def admin_menu():
    b = InlineKeyboardBuilder()
    b.button(text="➕ Add Product", callback_data="adm:add")
    b.button(text="🗑 Remove / Restore Product", callback_data="adm:del")
    b.button(text="📋 Product List", callback_data="adm:list")
    b.button(text="💲 Edit Price", callback_data="adm:price")
    b.button(text="🔑 Add Stock (keys)", callback_data="adm:stock")
    b.button(text="💸 Pending Deposits", callback_data="adm:pend")
    b.button(text="🎟 Make Code", callback_data="adm:gen")
    b.button(text="📊 Stats", callback_data="adm:stats")
    b.button(text="📢 Broadcast", callback_data="adm:bc")
    b.button(text="📢 Force-Join Channels", callback_data="adm:ch")
    b.button(text="⭐ Premium Emoji", callback_data="adm:pe")
    b.button(text="⚙️ Settings", callback_data="adm:set")
    b.button(text="💾 Backup Abhi", callback_data="adm:backup")
    b.button(text="📥 Restore Kaise Kare", callback_data="adm:rhelp")
    b.button(text="⬅️ Menu", callback_data="menu")
    b.adjust(2)
    return b.as_markup()


@admin_r.message(Command("admin"))
async def cmd_admin(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(card("Admin Panel", "Kya karna hai?"), reply_markup=admin_menu())


@admin_r.callback_query(F.data == "adm:home")
async def adm_home(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, card("Admin Panel", "Kya karna hai?"), admin_menu())
    await c.answer()


@admin_r.message(Command("addbal"))
async def cmd_addbal(m: Message):
    p = (m.text or "").split()
    if len(p) != 3 or not p[1].isdigit() or not p[2].lstrip("-").isdigit():
        return await m.answer("Use: /addbal USER_ID AMOUNT   (minus se cut)")
    uid, amt = int(p[1]), int(p[2])
    if not q1("SELECT COUNT(*) FROM users WHERE id=?", (uid,)):
        return await m.answer("User nahi mila.")
    if amt < 0 and get_balance(uid) + amt < 0:
        return await m.answer(f"Balance sirf ₹{get_balance(uid)} hai.")
    Wallet.add(uid, amt, "Admin adjustment")
    await m.answer(f"✅ Done. Naya balance: ₹{get_balance(uid)}")


@admin_r.message(Command("ban"))
@admin_r.message(Command("unban"))
async def cmd_ban(m: Message):
    p = (m.text or "").split()
    if len(p) != 2 or not p[1].isdigit():
        return await m.answer("Use: /ban USER_ID  ya  /unban USER_ID")
    x("UPDATE users SET banned=? WHERE id=?", (1 if (m.text or "").startswith("/ban") else 0, int(p[1])))
    await m.answer("🚫 Banned" if (m.text or "").startswith("/ban") else "✅ Unbanned")


@admin_r.message(Command("delemoji"))
async def cmd_delemoji(m: Message):
    p = (m.text or "").split(maxsplit=1)
    if len(p) < 2:
        return await m.answer("Use: /delemoji 🔥  (wo emoji jiska premium version hatana hai)")
    x("DELETE FROM premium_emoji WHERE fallback=?", (p[1].strip(),))
    load_prem()
    await m.answer("🗑 Premium emoji hata diya.")


# ---- Premium emoji add ----
@admin_r.callback_query(F.data == "adm:pe")
async def adm_pe(c: CallbackQuery, state: FSMContext):
    await state.set_state(PE.wait)
    rows = q("SELECT fallback FROM premium_emoji")
    saved = " ".join(r[0] for r in rows) or "abhi koi nahi"
    await show(c, card("Premium Emoji", f"Jo premium emoji add karna hai, wo <b>message me bhejo</b> (kisi bhi chat me, bot ko forward/paste karo).\n"
                       f"Bot wohi emoji sab jagah premium version me dikhayega.\n\nSaved: {e(saved)}\n\n/cancel se band karo"), back_kb())
    await c.answer()


@admin_r.message(PE.wait)
async def pe_save(m: Message, state: FSMContext):
    pairs = custom_emojis(m)
    if not pairs:
        return await m.answer("Is message me premium emoji nahi mila. Premium emoji wala message bhejo.")
    for fb, eid in pairs:
        x("INSERT INTO premium_emoji(fallback, emoji_id) VALUES(?,?) ON CONFLICT(fallback) DO UPDATE SET emoji_id=excluded.emoji_id", (fb, eid))
    load_prem()
    await state.clear()
    await m.answer(card("Premium Emoji Saved", f"✅ {len(pairs)} emoji save ho gaye.\nAb ye jaha bhi bot ke messages me aayenge, premium dikhenge.\n\n" + " ".join(f for f, _ in pairs)), reply_markup=admin_menu())


# ---- Products ----
@admin_r.callback_query(F.data == "adm:add")
async def adm_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddP.name)
    await c.message.answer("📦 Product ka naam likho:")
    await c.answer()


@admin_r.message(AddP.name, F.text)
async def addp_name(m: Message, state: FSMContext):
    if blocked_name(m.text):
        return await m.answer("❌ Ye naam allowed nahi hai. Dusra naam likho.")
    await state.update_data(name=m.text.strip())
    await state.set_state(AddP.price)
    await m.answer("💵 Price (₹) likho, sirf number:")


@admin_r.message(AddP.price, F.text)
async def addp_price(m: Message, state: FSMContext):
    if not m.text.isdigit() or int(m.text) <= 0:
        return await m.answer("Sahi number likho.")
    await state.update_data(price=int(m.text))
    await state.set_state(AddP.descr)
    await m.answer("📝 Chhota description likho (ya <code>-</code>):")


@admin_r.message(AddP.descr, F.text)
async def addp_descr(m: Message, state: FSMContext):
    await state.update_data(descr="" if m.text.strip() == "-" else m.text.strip())
    await state.set_state(AddP.content)
    await m.answer("📎 Ab customer ko jo milega wo bhejo: <b>file / photo / video / text</b>.\nKeys ke liye baad me 'Add Stock' use karo.")


@admin_r.message(AddP.content)
async def addp_content(m: Message, state: FSMContext):
    d = await state.get_data()
    if m.document:
        kind, content = "document", m.document.file_id
    elif m.video:
        kind, content = "video", m.video.file_id
    elif m.photo:
        kind, content = "photo", m.photo[-1].file_id
    elif m.text:
        kind, content = "text", m.text
    else:
        return await m.answer("File, photo, video ya text bhejo.")
    x("INSERT INTO products(name, price, kind, content, descr) VALUES(?,?,?,?,?)",
      (d["name"], d["price"], kind, content, d.get("descr", "")))
    await state.clear()
    await m.answer(card("Product Added", f"✅ {e(d['name'])} • ₹{d['price']}"), reply_markup=admin_menu())


@admin_r.callback_query(F.data == "adm:list")
async def adm_list(c: CallbackQuery):
    rows = q("SELECT * FROM products ORDER BY id")
    if not rows:
        await c.message.answer("Koi product nahi.")
        return await c.answer()
    lines = []
    for r in rows:
        extra = f" • 🔑 {stock_left(r['id'])} left" if r["kind"] == "keys" else ""
        st = "" if r["active"] else " (band)"
        lines.append(f"{r['id']}. {e(r['name'])} — ₹{r['price']}{extra}{st}")
    await c.message.answer(card("Product List", "\n".join(lines[:60])))
    await c.answer()


@admin_r.callback_query(F.data == "adm:del")
async def adm_del(c: CallbackQuery):
    rows = q("SELECT id, name, active FROM products ORDER BY id")
    b = InlineKeyboardBuilder()
    for r in rows:
        b.button(text=f"{'🗑' if r['active'] else '♻️'} {r['name']}", callback_data=f"dl:{r['id']}")
    b.adjust(1)
    await c.message.answer("Product par tap karo: active hai to band ho jayega, band hai to wapas on.", reply_markup=b.as_markup())
    await c.answer()


@admin_r.callback_query(F.data.startswith("dl:"))
async def adm_del_do(c: CallbackQuery):
    x("UPDATE products SET active = 1 - active WHERE id=?", (int(c.data[3:]),))
    await c.message.edit_text("✅ Product ka status badal diya.")
    await c.answer()


@admin_r.callback_query(F.data == "adm:price")
async def adm_price(c: CallbackQuery):
    rows = q("SELECT id, name, price FROM products ORDER BY id")
    b = InlineKeyboardBuilder()
    for r in rows:
        b.button(text=f"{r['name']} (₹{r['price']})", callback_data=f"pr:{r['id']}")
    b.adjust(1)
    await c.message.answer("Kiska price badalna hai?", reply_markup=b.as_markup())
    await c.answer()


@admin_r.callback_query(F.data.startswith("pr:"))
async def adm_price_pick(c: CallbackQuery, state: FSMContext):
    await state.set_state(PriceEd.value)
    await state.update_data(pid=int(c.data[3:]))
    await c.message.answer("Naya price (₹) likho:")
    await c.answer()


@admin_r.message(PriceEd.value, F.text)
async def price_save(m: Message, state: FSMContext):
    if not m.text.isdigit() or int(m.text) <= 0:
        return await m.answer("Sahi number likho.")
    d = await state.get_data()
    x("UPDATE products SET price=? WHERE id=?", (int(m.text), d["pid"]))
    await state.clear()
    await m.answer(f"✅ Price ₹{m.text} ho gaya.", reply_markup=admin_menu())


@admin_r.callback_query(F.data == "adm:stock")
async def adm_stock(c: CallbackQuery):
    rows = q("SELECT id, name FROM products WHERE kind='text' OR kind='keys' ORDER BY id")
    b = InlineKeyboardBuilder()
    for r in rows:
        b.button(text=f"🔑 {r['name']} ({stock_left(r['id'])} left)", callback_data=f"st:{r['id']}")
    b.adjust(1)
    await c.message.answer("Kis product me keys add karni hai? (pehle product 'keys' type ka hona chahiye, ya naya text product banao)", reply_markup=b.as_markup())
    await c.answer()


@admin_r.callback_query(F.data.startswith("st:"))
async def adm_stock_pick(c: CallbackQuery, state: FSMContext):
    pid = int(c.data[3:])
    x("UPDATE products SET kind='keys' WHERE id=? AND kind='text'", (pid,))
    await state.set_state(StockAdd.data)
    await state.update_data(pid=pid)
    await c.message.answer("🔑 Keys bhejo, har line me ek key:")
    await c.answer()


@admin_r.message(StockAdd.data, F.text)
async def stock_save(m: Message, state: FSMContext):
    d = await state.get_data()
    lines = [ln.strip() for ln in m.text.splitlines() if ln.strip()]
    for ln in lines:
        x("INSERT INTO stock(product_id, data) VALUES(?,?)", (d["pid"], ln))
    await state.clear()
    await m.answer(f"✅ {len(lines)} key add hui. Stock: {stock_left(d['pid'])}", reply_markup=admin_menu())


# ---- Codes / stats / broadcast ----
@admin_r.callback_query(F.data == "adm:gen")
async def adm_gen(c: CallbackQuery, state: FSMContext):
    await state.set_state(Gen.amount)
    await c.message.answer("Code ki value (₹) likho:")
    await c.answer()


@admin_r.message(Gen.amount, F.text)
async def gen_amount(m: Message, state: FSMContext):
    if not m.text.isdigit() or int(m.text) <= 0:
        return await m.answer("Sahi number likho.")
    code = secrets.token_hex(4).upper()
    x("INSERT INTO codes(code, amount) VALUES(?,?)", (code, int(m.text)))
    await state.clear()
    await m.answer(f"🎟 Code (₹{m.text}):\n<code>{code}</code>", reply_markup=admin_menu())


@admin_r.callback_query(F.data == "adm:stats")
async def adm_stats(c: CallbackQuery):
    t = (
        "📊 <b>Stats</b>\n\n"
        f"👥 Users: {q1('SELECT COUNT(*) FROM users')}\n"
        f"🚫 Banned: {q1('SELECT COUNT(*) FROM users WHERE banned=1')}\n"
        f"📦 Orders: {q1('SELECT COUNT(*) FROM purchases')}\n"
        f"💵 Total sale: ₹{q1('SELECT COALESCE(SUM(price),0) FROM purchases')}\n"
        f"💳 Deposit (approved): ₹{q1(\"SELECT COALESCE(SUM(amount),0) FROM deposits WHERE status='approved'\")}\n"
        f"⏳ Pending deposits: {q1(\"SELECT COUNT(*) FROM deposits WHERE status='pending'\")}\n"
        f"🏦 Wallets total: ₹{q1('SELECT COALESCE(SUM(balance),0) FROM users')}\n"
        f"🎁 Referrals: {q1('SELECT COUNT(*) FROM referrals')}"
    )
    await c.message.answer(card("Stats", t))
    await c.answer()


@admin_r.callback_query(F.data == "adm:pend")
async def adm_pend(c: CallbackQuery, bot: Bot):
    rows = q("SELECT * FROM deposits WHERE status='pending' ORDER BY id LIMIT 10")
    if not rows:
        await c.message.answer("✅ Koi pending deposit nahi.")
        return await c.answer()
    for d in rows:
        try:
            await bot.send_photo(ADMIN_ID, d["shot"], caption=dep_caption(d), reply_markup=dep_buttons(d["id"]))
        except Exception:
            await bot.send_message(ADMIN_ID, dep_caption(d), reply_markup=dep_buttons(d["id"]))
    await c.answer()


@admin_r.callback_query(F.data == "adm:bc")
async def adm_bc(c: CallbackQuery, state: FSMContext):
    await state.set_state(Bcast.text)
    await c.message.answer("📢 Jo message sab users ko bhejna hai wo likho.\n\n/cancel se band karo")
    await c.answer()


@admin_r.message(Bcast.text, F.text)
async def bc_send(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    ids = [r[0] for r in q("SELECT id FROM users WHERE banned=0")]
    ok = fail = 0
    await m.answer(f"📥 {len(ids)} users ko bhej raha hoon...")
    for uid in ids:
        try:
            await bot.send_message(uid, prem(m.html_text if hasattr(m, "html_text") else m.text))
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await m.answer(f"✅ Done. Sent: {ok}  Failed: {fail}")


# ---- Force-join channels ----
def channels_menu():
    b = InlineKeyboardBuilder()
    b.button(text="➕ Add Channel", callback_data="ch:add")
    for ch in q("SELECT * FROM channels ORDER BY id"):
        b.button(text=f"🗑 Remove • {ch['title'] or ch['chat_id']}", callback_data=f"ch:del:{ch['id']}")
    b.button(text="⬅️ Admin Panel", callback_data="adm:home")
    b.adjust(1)
    return b.as_markup()


def channels_text():
    chans = q("SELECT * FROM channels ORDER BY id")
    if not chans:
        return card("Force-Join", "Abhi koi channel nahi.\n⚠️ Bot ko channel me <b>Admin</b> banana zaroori hai.")
    body = "\n".join(f"{i}. 📢 <b>{e(ch['title'])}</b>\n    {e(ch['link'])}" for i, ch in enumerate(chans, 1))
    return card("Force-Join Channels", body)


@admin_r.callback_query(F.data == "adm:ch")
async def adm_ch(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, channels_text(), channels_menu())
    await c.answer()


@admin_r.callback_query(F.data == "ch:add")
async def ch_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(ChanAdd.chat)
    await c.message.answer("Channel ka <code>@username</code> ya ID bhejo (jaise <code>-1001234567890</code>).\nBot ko channel me Admin banana zaroori hai.\n\n/cancel se band karo")
    await c.answer()


@admin_r.message(ChanAdd.chat, F.text)
async def ch_add_chat(m: Message, state: FSMContext, bot: Bot):
    t = m.text.strip()
    target = int(t) if t.lstrip("-").isdigit() else ("@" + t.replace("https://t.me/", "").lstrip("@"))
    try:
        chat = await bot.get_chat(target)
        me = await bot.get_me()
        cm = await bot.get_chat_member(chat.id, me.id)
        if getattr(cm.status, "value", cm.status) not in ("administrator", "creator"):
            return await m.answer("⚠️ Bot is channel me Admin nahi hai. Pehle Admin banao.")
    except Exception as ex:
        return await m.answer(f"❌ Channel nahi mila: {e(ex)}")
    title = chat.title or str(chat.id)
    link = f"https://t.me/{chat.username}" if chat.username else ""
    if not link:
        try:
            link = await bot.export_chat_invite_link(chat.id)
        except Exception:
            link = ""
    if not link:
        await state.update_data(chat_id=chat.id, title=title)
        await state.set_state(ChanAdd.link)
        return await m.answer("Is channel ka invite link bhejo (https://t.me/+...):")
    x("INSERT INTO channels(chat_id, title, link) VALUES(?,?,?) ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, link=excluded.link", (chat.id, title, link))
    _join_cache.clear()
    await state.clear()
    await m.answer(channels_text(), reply_markup=channels_menu())


@admin_r.message(ChanAdd.link, F.text)
async def ch_add_link(m: Message, state: FSMContext):
    link = m.text.strip()
    if not link.startswith("https://t.me/"):
        return await m.answer("Link https://t.me/... se shuru hona chahiye.")
    d = await state.get_data()
    x("INSERT INTO channels(chat_id, title, link) VALUES(?,?,?) ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, link=excluded.link", (d["chat_id"], d["title"], link))
    _join_cache.clear()
    await state.clear()
    await m.answer(channels_text(), reply_markup=channels_menu())


@admin_r.callback_query(F.data.startswith("ch:del:"))
async def ch_del(c: CallbackQuery):
    x("DELETE FROM channels WHERE id=?", (int(c.data[7:]),))
    _join_cache.clear()
    await show(c, channels_text(), channels_menu())
    await c.answer("Channel hata diya")


# ---- Settings ----
def settings_text():
    lines = "\n".join(f"{label}: <b>{e(S(k))}</b>" for k, label in SETTING_LABELS)
    return card("Settings", lines + "\n\nJo badalna hai uspe tap karo 👇")


def settings_menu():
    b = InlineKeyboardBuilder()
    for k, label in SETTING_LABELS:
        b.button(text=f"✏️ {label}", callback_data=f"set:{k}")
    b.button(text="⬅️ Admin Panel", callback_data="adm:home")
    b.adjust(1)
    return b.as_markup()


@admin_r.callback_query(F.data == "adm:set")
async def adm_set(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, settings_text(), settings_menu())
    await c.answer()


@admin_r.callback_query(F.data.startswith("set:"))
async def adm_set_pick(c: CallbackQuery, state: FSMContext):
    key = c.data[4:]
    if key not in DEFAULTS:
        return await c.answer("Setting nahi mili", show_alert=True)
    await state.set_state(SetS.value)
    await state.update_data(key=key)
    await c.message.answer(f"Abhi: <b>{e(S(key))}</b>\nNaya value likho:\n\n/cancel se band karo")
    await c.answer()


@admin_r.message(SetS.value, F.text)
async def adm_set_value(m: Message, state: FSMContext):
    d = await state.get_data()
    key, v = d.get("key"), m.text.strip()
    if key in NUMERIC_SETTINGS and not v.isdigit():
        return await m.answer("Sirf number likho.")
    if key == "upi_id" and not re.fullmatch(r"[A-Za-z0-9._\-]{2,}@[A-Za-z0-9]{2,}", v):
        return await m.answer("UPI ID sahi nahi. Jaise: naam@okaxis")
    if key == "support":
        v = v if v.startswith("@") else "@" + v
    set_setting(key, v)
    await state.clear()
    await m.answer(f"✅ Update ho gaya.\n\n{settings_text()}", reply_markup=settings_menu())


# ================= BACKUP / RESTORE =================
TABLES = ["users", "products", "stock", "deposits", "purchases", "codes", "channels",
          "referrals", "ledger", "settings", "premium_emoji"]


def export_json():
    data = {}
    for t in TABLES:
        data[t] = [dict(r) for r in q(f"SELECT * FROM {t}")]
    return json.dumps(data, default=str, ensure_ascii=False).encode()


async def send_backup(bot: Bot, caption="💾 Database backup"):
    stamp = datetime.now(IST).strftime("%Y%m%d_%H%M")
    await bot.send_document(
        ADMIN_ID,
        BufferedInputFile(export_json(), f"shop_backup_{stamp}.json"),
        caption=f"{caption}\n👥 Users: {q1('SELECT COUNT(*) FROM users')}\n\nRestore: ye file bot ko bhejo, caption me /restore force likh ke.",
    )


def import_backup(data: dict):
    prods = []
    blocked_ids = set()
    for p in data.get("products") or []:
        if blocked_name(p.get("name")):
            blocked_ids.add(p.get("id"))
        else:
            prods.append(p)
    conn.execute("BEGIN")
    try:
        for t in reversed(TABLES):
            conn.execute(f"DELETE FROM {t}")
        for t in TABLES:
            rows = data.get(t) or []
            if t == "products":
                rows = prods
            elif t in ("stock", "purchases"):
                rows = [r for r in rows if r.get("product_id") not in blocked_ids]
            cols = [c[1] for c in conn.execute(f"PRAGMA table_info({t})").fetchall()]
            for r in rows:
                use = [c for c in cols if c in r]
                if not use:
                    continue
                conn.execute(
                    f"INSERT OR REPLACE INTO {t}({','.join(use)}) VALUES({','.join('?' * len(use))})",
                    [r[c] for c in use],
                )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    load_prem()


def db_is_empty():
    return q1("SELECT COUNT(*) FROM users") == 0 and q1("SELECT COUNT(*) FROM products") == 0


@admin_r.callback_query(F.data == "adm:backup")
async def adm_backup(c: CallbackQuery, bot: Bot):
    await send_backup(bot)
    await c.answer("Backup bhej diya")


@admin_r.callback_query(F.data == "adm:rhelp")
async def adm_rhelp(c: CallbackQuery):
    await c.message.answer(card(
        "Restore Kaise Kare",
        "1️⃣ Backup file (<b>.json</b>) is chat me bhejo\n"
        "2️⃣ Database khali ho to seedha restore hoga\n"
        "3️⃣ Data pehle se ho to file ke caption me <code>/restore force</code> likho\n\n"
        "⚠️ Blocked product names restore nahi honge.",
    ))
    await c.answer()


@admin_r.message(F.document)
async def admin_document(m: Message, bot: Bot):
    name = (m.document.file_name or "").lower()
    if not name.endswith(".json"):
        return
    force = "force" in (m.caption or "").lower()
    if not db_is_empty() and not force:
        return await m.answer("⚠️ Database khali nahi hai. Caption me <code>/restore force</code> likho to purana data hatake restore hoga.")
    try:
        buf = io.BytesIO()
        await bot.download(m.document, destination=buf)
        data = json.loads(buf.getvalue().decode("utf-8-sig"))
        if not isinstance(data, dict) or not isinstance(data.get("users"), list):
            raise ValueError("bad file")
    except Exception:
        return await m.answer("❌ Ye valid backup file nahi hai.")
    try:
        import_backup(data)
    except Exception as ex:
        logging.exception(ex)
        return await m.answer(f"❌ Restore fail: {e(ex)}")
    await m.answer(card("Restore Done", f"✅ Users: {q1('SELECT COUNT(*) FROM users')}\n"
                       f"📦 Products: {q1('SELECT COUNT(*) FROM products')}\n"
                       f"💰 Wallets: ₹{q1('SELECT COALESCE(SUM(balance),0) FROM users')}"), reply_markup=admin_menu())


def auto_restore():
    """Pehli baar khali DB me backup.json ho to khud restore."""
    if not db_is_empty():
        return
    path = os.getenv("RESTORE_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "backup.json")
    if os.path.isfile(path):
        try:
            with open(path, "rb") as f:
                import_backup(json.loads(f.read().decode("utf-8-sig")))
            logging.info("Backup auto-restore ho gaya: %s", path)
        except Exception as ex:
            logging.error("auto-restore fail: %s", ex)


async def backup_loop(bot: Bot):
    while True:
        await asyncio.sleep(BACKUP_EVERY)
        try:
            if q1("SELECT COUNT(*) FROM users"):
                await send_backup(bot, "💾 Auto backup (har 4 ghante)")
        except Exception as ex:
            logging.warning("auto backup fail: %s", ex)


async def sweep_loop(bot: Bot):
    await asyncio.sleep(120)
    while True:
        try:
            if q("SELECT 1 FROM channels LIMIT 1"):
                for r in q("SELECT referred_id FROM referrals WHERE status IN ('rewarded','revoked')"):
                    await ref_sync(bot, r[0])
                    await asyncio.sleep(0.15)
        except Exception as ex:
            logging.warning("referral sweep fail: %s", ex)
        await asyncio.sleep(SWEEP_EVERY)


# ================= FALLBACK =================
@user_r.message()
async def fallback_msg(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    if await state.get_state() is not None:
        return await m.answer("⚠️ Is step me sahi cheez bhejo, ya /cancel dabao.")
    ensure_user(m.from_user.id, m.from_user.username)
    await m.answer(home_text(m.from_user.id, m.from_user.first_name), reply_markup=main_menu(m.from_user.id))


@user_r.callback_query()
async def fallback_cb(c: CallbackQuery):
    await c.answer("Ye button purana ho gaya. /start dabao.", show_alert=True)


# ================= RUN =================
async def main():
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        me = await bot.get_me()
    except TelegramUnauthorizedError:
        raise SystemExit("❌ BOT_TOKEN galat hai ya revoke ho chuka hai. BotFather se naya token lo.")
    await bot.delete_webhook(drop_pending_updates=True)
    auto_restore()
    logging.info("Bot start: @%s | DB: %s", me.username, DB_PATH)

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(cmd_r)
    dp.include_router(admin_r)
    dp.include_router(user_r)
    gate = Gate()
    dp.message.outer_middleware(gate)
    dp.callback_query.outer_middleware(gate)

    tasks = [asyncio.create_task(backup_loop(bot)), asyncio.create_task(sweep_loop(bot))]
    try:
        while True:
            try:
                await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
                break
            except TelegramConflictError:
                logging.error("Bot kahin aur chal raha hai (Conflict). 15 sec baad retry.")
                await asyncio.sleep(15)
            except TelegramUnauthorizedError:
                raise SystemExit("❌ Token revoke ho gaya.")
            except Exception as ex:
                logging.exception("polling band hui: %s", ex)
                await asyncio.sleep(5)
    finally:
        for t in tasks:
            t.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())