import os, re, time, random, asyncio, threading, json
from datetime import datetime, date, timedelta
from io import BytesIO
from flask import Flask, request as flask_request, jsonify
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from openai import OpenAI
from database import init_pool, init_db, get_db, release_db
from config import (
    BOT_TOKEN, OWNER_ID, BOOKING_EMAIL, CASHAPP, PAYPAL, TON_WALLET,
    OPENAI_API_KEY, BOT_USERNAME, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET,
    SONG_PRICE, ALBUM_PRICE, ALBUM_COUNT, VAULT_PREVIEW_PRICE, VAULT_FULL_PRICE,
    PARISH_LOUNGE, RADIO_CHANNEL, TERMS_URL, PRIVACY_URL, REFUND_URL,
)

try:
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY
    STRIPE_OK = True
except Exception:
    STRIPE_OK = False

openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
flask_app = Flask(__name__)

@flask_app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

app = None
loop = None
radio_loop_running = False
ENTRY_CACHE = {}
GATE_CACHE = {}
upload_sessions = {}

RADIO_CHANNEL_ID = os.environ.get("RADIO_CHANNEL_ID", "")

SOFT_GATE = [
    ("YouTube", "https://youtube.com/@bazragod"),
    ("Instagram", "https://instagram.com/bazragod_timeless"),
    ("Telegram Radio", "https://t.me/bazragodradio"),
    ("Parish 14 Lounge", "https://t.me/parish14lounge"),
]

main_menu = ReplyKeyboardMarkup([
    ["🎵 MUSIC", "🛒 STORE"],
    ["📻 Radio", "🔐 Secret Vault"],
    ["🪪 My Passport", "🪙 MiserCoins"],
    ["📋 Terms", "📞 Support"],
], resize_keyboard=True)

def is_admin(uid): return uid == OWNER_ID
def uname(update): u = update.effective_user; return u.username or u.first_name or str(u.id)

def get_rank(points):
    if points >= 10000: return "Parish Legend"
    if points >= 5000: return "Nation Elite"
    if points >= 2500: return "General"
    if points >= 1000: return "Commander"
    if points >= 500: return "Recruiter"
    if points >= 100: return "Supporter"
    return "Fan"

def get_next_rank(points):
    ranks = [(100,"Supporter"),(500,"Recruiter"),(1000,"Commander"),(2500,"General"),(5000,"Nation Elite"),(10000,"Parish Legend")]
    for threshold, name in ranks:
        if points < threshold:
            return f"{name} ({threshold - points:,} coins away)"
    return "Parish Legend — Maximum Rank"

def award_points(tid, amount, username=None):
    if amount <= 0: return 0
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("""INSERT INTO users (telegram_id, username, points)
            VALUES (%s,%s,%s) ON CONFLICT (telegram_id) DO UPDATE
            SET points=users.points+EXCLUDED.points,
                username=COALESCE(EXCLUDED.username, users.username)""",
            (tid, username, amount))
        cur.execute("SELECT points FROM users WHERE telegram_id=%s", (tid,))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE users SET tier=%s WHERE telegram_id=%s", (get_rank(row[0]), tid))
        conn.commit()
    finally:
        release_db(conn)
    return amount

def register_user(tid, username, referrer_id=None):
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT telegram_id FROM users WHERE telegram_id=%s", (tid,))
        if cur.fetchone(): return False
        pnum = f"P14-{tid % 100000:05d}"
        cur.execute("INSERT INTO users (telegram_id, username, referrer_id, passport_number) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (tid, username, referrer_id, pnum))
        if referrer_id and referrer_id != tid:
            cur.execute("UPDATE users SET invites=invites+1 WHERE telegram_id=%s", (referrer_id,))
        conn.commit(); return True
    finally:
        release_db(conn)

def mark_entry(uid):
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET entry_completed=TRUE WHERE telegram_id=%s", (uid,)); conn.commit()
    finally:
        release_db(conn)
    ENTRY_CACHE[uid] = True

def mark_gate(uid):
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET gate_completed=TRUE WHERE telegram_id=%s", (uid,)); conn.commit()
    finally:
        release_db(conn)
    GATE_CACHE[uid] = True

def has_entry(uid):
    if ENTRY_CACHE.get(uid): return True
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT entry_completed FROM users WHERE telegram_id=%s", (uid,))
        row = cur.fetchone()
        if row and row[0]: ENTRY_CACHE[uid] = True; return True
        return False
    finally:
        release_db(conn)

def has_gate(uid):
    if GATE_CACHE.get(uid): return True
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT gate_completed FROM users WHERE telegram_id=%s", (uid,))
        row = cur.fetchone()
        if row and row[0]: GATE_CACHE[uid] = True; return True
        return False
    finally:
        release_db(conn)

def heat(likes, plays):
    score = (likes * 5) + (plays / 1000)
    if score >= 250: return "🔥🔥🔥🔥🔥"
    if score >= 100: return "🔥🔥🔥🔥"
    if score >= 50: return "🔥🔥🔥"
    if score >= 10: return "🔥🔥"
    return "🔥"

def check_duplicate(file_id, title, table):
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute(f"SELECT id FROM {table} WHERE file_id=%s OR LOWER(title)=LOWER(%s)", (file_id, title))
        return cur.fetchone() is not None
    finally:
        release_db(conn)

def create_checkout(uid, username, product_type, amount_usd, product_name, product_id=""):
    if not STRIPE_OK: return None
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price_data": {
                "currency": "usd",
                "product_data": {"name": product_name},
                "unit_amount": int(amount_usd * 100)
            }, "quantity": 1}],
            mode="payment",
            success_url=f"https://t.me/{BOT_USERNAME}",
            cancel_url=f"https://t.me/{BOT_USERNAME}",
            metadata={
                "telegram_id": str(uid),
                "username": username or "",
                "product_type": product_type,
                "product_id": product_id
            }
        )
        conn = get_db(); cur = conn.cursor()
        try:
            cur.execute("INSERT INTO stripe_sessions (telegram_id, session_id, product_type, product_id, amount) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (uid, session.id, product_type, product_id, int(amount_usd)))
            conn.commit()
        finally:
            release_db(conn)
        return session.url
    except Exception as e:
        print(f"Stripe error: {e}"); return None

async def deliver_song(bot, uid, song_id, username="fan"):
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT title, file_id FROM songs WHERE id=%s", (song_id,))
        song = cur.fetchone()
        if not song: return False
        title, file_id = song
        cur.execute("INSERT INTO downloads (telegram_id, song_id, purchased) VALUES (%s,%s,TRUE) ON CONFLICT (telegram_id,song_id) DO UPDATE SET purchased=TRUE", (uid, song_id))
        conn.commit()
    finally:
        release_db(conn)
    await bot.send_audio(uid, file_id,
        caption=f"DOWNLOAD DELIVERED\n\n{title}\nBAZRAGOD\n\nYours to keep. Parish 14 Nation.")
    award_points(uid, 50, username)
    return True

async def handle_stripe_payment(session_data, bot):
    uid = int(session_data.get("metadata", {}).get("telegram_id", 0))
    product_type = session_data.get("metadata", {}).get("product_type", "")
    product_id = session_data.get("metadata", {}).get("product_id", "")
    session_id = session_data.get("id", "")
    username = session_data.get("metadata", {}).get("username", "fan")
    if not uid: return
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("UPDATE stripe_sessions SET status='completed' WHERE session_id=%s", (session_id,))
        conn.commit()
    finally:
        release_db(conn)
    try:
        if product_type == "single_song":
            song_id = int(product_id) if product_id else 0
            if song_id:
                await deliver_song(bot, uid, song_id, username)

        elif product_type == "cart_album":
            conn = get_db(); cur = conn.cursor()
            try:
                cur.execute("SELECT song_id FROM cart WHERE telegram_id=%s", (uid,))
                song_ids = [r[0] for r in cur.fetchall()]
                cur.execute("DELETE FROM cart WHERE telegram_id=%s", (uid,))
                conn.commit()
            finally:
                release_db(conn)
            await bot.send_message(uid, f"ALBUM DELIVERY\n\n{len(song_ids)} tracks incoming...\n\nBAZRAGOD\nParish 14 Nation.")
            for sid in song_ids:
                await deliver_song(bot, uid, sid, username)
                await asyncio.sleep(2)
            await bot.send_message(uid, f"ALL SONGS DELIVERED\n\n{len(song_ids)} tracks in your Telegram files.\n\nBAZRAGOD x Parish 14 Nation.")

        elif product_type == "vault_preview":
            expires = datetime.now() + timedelta(hours=24)
            conn = get_db(); cur = conn.cursor()
            try:
                cur.execute("SELECT id FROM vault_songs")
                for (vid,) in cur.fetchall():
                    cur.execute("INSERT INTO vault_access (telegram_id, vault_id, method, expires_at) VALUES (%s,%s,'preview',%s) ON CONFLICT (telegram_id,vault_id) DO UPDATE SET expires_at=%s, method='preview'",
                        (uid, vid, expires, expires))
                conn.commit()
            finally:
                release_db(conn)
            await bot.send_message(uid, f"VAULT PREVIEW UNLOCKED\n\n24 hour access activated.\nExpires: {expires.strftime('%B %d at %I:%M %p')}\n\nGo to Secret Vault now.\n\nBAZRAGOD.")

        elif product_type == "vault_full":
            conn = get_db(); cur = conn.cursor()
            try:
                cur.execute("SELECT id, title, file_id FROM vault_songs")
                vault_songs = cur.fetchall()
                for vid, title, file_id in vault_songs:
                    cur.execute("INSERT INTO vault_access (telegram_id, vault_id, method) VALUES (%s,%s,'purchased') ON CONFLICT (telegram_id,vault_id) DO UPDATE SET method='purchased', expires_at=NULL",
                        (uid, vid))
                conn.commit()
            finally:
                release_db(conn)
            await bot.send_message(uid, f"VAULT UNLOCKED — SUPER FAN\n\nAll 5 exclusive songs are yours.\nSigned merch and album incoming.\n\nContact: {BOOKING_EMAIL}\n\nBAZRAGOD.")
            for vid, title, file_id in vault_songs:
                await bot.send_audio(uid, file_id, caption=f"VAULT EXCLUSIVE\n\n{title}\nBAZRAGOD\n\nNot on any platform. Yours only.")
                await asyncio.sleep(2)
            try:
                await bot.send_message(OWNER_ID, f"VAULT FULL PURCHASE\n\nFan: @{username} ({uid})\nPrepare signed merch and pullover shipment.")
            except Exception: pass

        elif product_type == "supporter":
            expires = date.today() + timedelta(days=30)
            conn = get_db(); cur = conn.cursor()
            try:
                cur.execute("UPDATE users SET is_supporter=TRUE, tier='Nation Elite', supporter_expires=%s WHERE telegram_id=%s", (expires, uid))
                conn.commit()
            finally:
                release_db(conn)
            award_points(uid, 500, username)
            await bot.send_message(uid, f"SUPPORTER ACTIVATED\n\nNation Elite unlocked.\nExpires: {expires.strftime('%B %d, %Y')}\n\nBAZRAGOD sees you.")

        elif product_type == "donation":
            amount = session_data.get("amount_total", 0) / 100
            await bot.send_message(uid, f"DONATION RECEIVED\n\n${amount:.2f} goes directly to BAZRAGOD.\nNo label. No cut.\n\nParish 14 Nation.")
            award_points(uid, 25, username)

    except Exception as e:
        print(f"Delivery error: {e}")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name = uname(update)
    args = context.args if context.args else []
    referrer = None
    if args and args[0].isdigit():
        referrer = int(args[0])
        if referrer == uid: referrer = None
    is_new = register_user(uid, name, referrer)
    if is_new and referrer:
        try:
            await context.bot.send_message(referrer, f"New fan joined via your link!\n\n+50 MiserCoins\n\nParish 14 grows.")
            award_points(referrer, 50)
        except Exception: pass
    award_points(uid, 10, name)
    if has_entry(uid) and has_gate(uid):
        await update.message.reply_text(f"Welcome back to Parish 14 Nation.", reply_markup=main_menu)
        return
    await update.message.reply_text(
        "B A Z R A G O D\nI.A.A.I.M.O\nPARISH 14\n\nFrequency locked.\nTransmission incoming...")
    await asyncio.sleep(1)
    await show_gate(update.message, uid)

async def show_gate(msg, uid):
    kb = [[InlineKeyboardButton(f"Join {n}", url=u)] for n, u in SOFT_GATE]
    kb.append([InlineKeyboardButton("I Have Joined All — ENTER", callback_data="gate:done")])
    await msg.reply_text(
        "LAST STEP\n\nJoin all Parish 14 channels to enter the platform.\n\nThis grows the nation and unlocks your full access.",
        reply_markup=InlineKeyboardMarkup(kb))

async def gate_done_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    name = q.from_user.username or q.from_user.first_name or str(uid)
    mark_entry(uid); mark_gate(uid)
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT points, tier FROM users WHERE telegram_id=%s", (uid,))
        row = cur.fetchone()
    finally:
        release_db(conn)
    pts = row[0] if row else 0
    tier = row[1] if row else "Fan"
    await q.message.reply_text(
        f"YOU ARE NOW INSIDE\n\nI.A.A.I.M.O — Parish 14 Nation.\nNo labels. No middlemen. Just the movement.\n\nYou are part of history.\n\nNation Tier:  {tier}\nMiserCoins:   {pts:,}\n\nThe platform is yours.",
        reply_markup=main_menu)

async def cmd_music(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name = uname(update)
    award_points(uid, 8, name)
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT id, title, plays, likes FROM songs ORDER BY id")
        songs = cur.fetchall()
    finally:
        release_db(conn)
    if not songs:
        await update.message.reply_text("Catalog loading. Check back soon. Parish 14.")
        return
    kb = [[InlineKeyboardButton(f"{s[1]} {heat(s[3], s[2])}", callback_data=f"song:{s[0]}")] for s in songs]
    await update.message.reply_text(
        f"BAZRAGOD CATALOG\n\n{len(songs)} tracks available\n\nSelect a track to play or buy.",
        reply_markup=InlineKeyboardMarkup(kb))

async def play_song_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    name = q.from_user.username or str(uid)
    song_id = int(q.data.split(":")[1])
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT title, file_id, plays, likes FROM songs WHERE id=%s", (song_id,))
        song = cur.fetchone()
        if song:
            cur.execute("UPDATE songs SET plays=plays+1 WHERE id=%s", (song_id,))
            conn.commit()
    finally:
        release_db(conn)
    if not song: return
    title, file_id, plays, likes = song
    plays += 1
    pts = award_points(uid, 8, name)
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM downloads WHERE telegram_id=%s AND song_id=%s AND purchased=TRUE", (uid, song_id))
        owned = cur.fetchone() is not None
        cur.execute("SELECT 1 FROM cart WHERE telegram_id=%s AND song_id=%s", (uid, song_id))
        in_cart = cur.fetchone() is not None
    finally:
        release_db(conn)
    cart_label = "✅ In Cart" if in_cart else "Add to Cart"
    buy_label = "✅ Owned" if owned else f"Buy ${SONG_PRICE}"
    await q.message.reply_audio(file_id,
        caption=f"{title}\nBAZRAGOD\n\n{heat(likes, plays)} {plays:,} plays\n\n+{pts} MiserCoins",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❤️ Like", callback_data=f"like:{song_id}"),
            InlineKeyboardButton(cart_label, callback_data=f"cart_add:{song_id}"),
            InlineKeyboardButton(buy_label, callback_data=f"buy_song:{song_id}"),
        ]]))

async def like_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    song_id = int(q.data.split(":")[1])
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("INSERT INTO song_likes (telegram_id, song_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (uid, song_id))
        if cur.rowcount > 0:
            cur.execute("UPDATE songs SET likes=likes+1 WHERE id=%s", (song_id,))
            conn.commit()
            award_points(uid, 3)
            await q.answer("❤️ Liked! +3 coins")
        else:
            await q.answer("Already liked")
    finally:
        release_db(conn)

async def cart_add_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    song_id = int(q.data.split(":")[1])
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM downloads WHERE telegram_id=%s AND song_id=%s AND purchased=TRUE", (uid, song_id))
        if cur.fetchone():
            await q.answer("You already own this song.", show_alert=True)
            return
        cur.execute("INSERT INTO cart (telegram_id, song_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (uid, song_id))
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM cart WHERE telegram_id=%s", (uid,))
        count = cur.fetchone()[0]
    finally:
        release_db(conn)
    if count >= ALBUM_COUNT:
        await q.answer(f"Cart full! {count} songs = ${ALBUM_PRICE} album deal!", show_alert=False)
    else:
        await q.answer(f"Added! {count}/{ALBUM_COUNT} for ${ALBUM_PRICE} album deal.")

async def cmd_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name = uname(update)
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT s.id, s.title FROM cart c JOIN songs s ON c.song_id=s.id WHERE c.telegram_id=%s ORDER BY c.added_at", (uid,))
        items = cur.fetchall()
    finally:
        release_db(conn)
    if not items:
        await update.message.reply_text("YOUR CART\n\nCart is empty.\n\nBrowse music and add songs.")
        return
    count = len(items)
    if count >= ALBUM_COUNT:
        price = ALBUM_PRICE
        deal = f"ALBUM DEAL — {count} songs = ${ALBUM_PRICE}"
    else:
        price = count * SONG_PRICE
        deal = f"{count} song(s) = ${price}\n\nAdd {ALBUM_COUNT - count} more for ${ALBUM_PRICE} album deal"
    text = "YOUR CART\n\n" + "\n".join([f"  🎵 {t}" for _, t in items]) + f"\n\n{deal}"
    kb = [[InlineKeyboardButton(f"Remove {t[:20]}", callback_data=f"cart_remove:{i}")] for i, t in items]
    kb.append([InlineKeyboardButton(f"Checkout ${price}", callback_data="cart_checkout")])
    kb.append([InlineKeyboardButton("Clear Cart", callback_data="cart_clear")])
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def cart_remove_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    song_id = int(q.data.split(":")[1])
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("DELETE FROM cart WHERE telegram_id=%s AND song_id=%s", (uid, song_id))
        conn.commit()
    finally:
        release_db(conn)
    await q.answer("Removed.")

async def cart_clear_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("DELETE FROM cart WHERE telegram_id=%s", (q.from_user.id,))
        conn.commit()
    finally:
        release_db(conn)
    await q.message.reply_text("Cart cleared.")

async def cart_checkout_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    name = q.from_user.username or str(uid)
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM cart WHERE telegram_id=%s", (uid,))
        count = cur.fetchone()[0]
    finally:
        release_db(conn)
    if not count:
        await q.message.reply_text("Cart is empty.")
        return
    price = ALBUM_PRICE if count >= ALBUM_COUNT else count * SONG_PRICE
    url = create_checkout(uid, name, "cart_album", price, f"BAZRAGOD — {count} Song Album")
    kb = []
    if url:
        kb.append([InlineKeyboardButton(f"Pay ${price} via Stripe", url=url)])
    kb.append([InlineKeyboardButton("Pay via TON", url=f"ton://transfer/{TON_WALLET}?amount={price}")])
    kb.append([InlineKeyboardButton("CashApp", url=CASHAPP)])
    kb.append([InlineKeyboardButton("PayPal", url=PAYPAL)])
    await q.message.reply_text(
        f"CHECKOUT\n\n{count} song(s)\nTotal: ${price}\n\nChoose payment method.\n\nAfter CashApp or PayPal payment contact:\n{BOOKING_EMAIL}",
        reply_markup=InlineKeyboardMarkup(kb))

async def buy_song_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    name = q.from_user.username or str(uid)
    song_id = int(q.data.split(":")[1])
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM downloads WHERE telegram_id=%s AND song_id=%s AND purchased=TRUE", (uid, song_id))
        already = cur.fetchone() is not None
        cur.execute("SELECT title FROM songs WHERE id=%s", (song_id,))
        song = cur.fetchone()
    finally:
        release_db(conn)
    if already:
        await q.answer("You already own this song.", show_alert=True)
        return
    if not song: return
    url = create_checkout(uid, name, "single_song", SONG_PRICE, f"BAZRAGOD — {song[0]}", str(song_id))
    kb = []
    if url:
        kb.append([InlineKeyboardButton(f"Pay ${SONG_PRICE} via Stripe", url=url)])
    kb.append([InlineKeyboardButton("CashApp", url=CASHAPP)])
    kb.append([InlineKeyboardButton("PayPal", url=PAYPAL)])
    await q.message.reply_text(
        f"BUY SONG\n\n{song[0]}\nBAZRAGOD\n\nPrice: ${SONG_PRICE}\n\nDelivered instantly after Stripe payment.\n\nFor CashApp or PayPal contact:\n{BOOKING_EMAIL}",
        reply_markup=InlineKeyboardMarkup(kb))
async def cmd_vault(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    now = datetime.now()
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT points FROM users WHERE telegram_id=%s", (uid,))
        fan = cur.fetchone(); fan_pts = fan[0] if fan else 0
        cur.execute("SELECT id, title FROM vault_songs ORDER BY id")
        items = cur.fetchall()
        cur.execute("SELECT vault_id, method, expires_at FROM vault_access WHERE telegram_id=%s", (uid,))
        access = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    finally:
        release_db(conn)
    if not items:
        await update.message.reply_text("SECRET VAULT\n\nExclusive BAZRAGOD music not on any platform.\n\nContent incoming. Parish 14.")
        return
    text = f"SECRET VAULT\n\nYour MiserCoins: {fan_pts:,}\n\n"
    kb = []
    for vid, title in items:
        if vid in access:
            method, expires = access[vid]
            if method == "purchased" or expires is None or expires > now:
                kb.append([InlineKeyboardButton(f"▶️ {title}", callback_data=f"vault_play:{vid}")])
            else:
                kb.append([InlineKeyboardButton(f"🔒 {title} — Expired", callback_data="vault_preview")])
        else:
            kb.append([InlineKeyboardButton(f"🔒 {title}", callback_data="vault_preview")])
    kb.append([InlineKeyboardButton(f"Preview 24hrs — ${VAULT_PREVIEW_PRICE}", callback_data="vault_preview")])
    kb.append([InlineKeyboardButton(f"Buy All 5 + Merch — ${VAULT_FULL_PRICE}", callback_data="vault_full")])
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def vault_play_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    vault_id = int(q.data.split(":")[1])
    now = datetime.now()
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT method, expires_at FROM vault_access WHERE telegram_id=%s AND vault_id=%s", (uid, vault_id))
        access = cur.fetchone()
        cur.execute("SELECT title, file_id FROM vault_songs WHERE id=%s", (vault_id,))
        song = cur.fetchone()
    finally:
        release_db(conn)
    if not access or not song:
        await q.answer("Access not found.", show_alert=True); return
    method, expires = access
    if method != "purchased" and expires and expires < now:
        await q.answer("Your 24hr preview has expired.", show_alert=True); return
    title, file_id = song
    await q.message.reply_audio(file_id, caption=f"VAULT EXCLUSIVE\n\n{title}\nBAZRAGOD\n\nNot on any platform.")

async def vault_preview_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id; name = q.from_user.username or str(uid)
    url = create_checkout(uid, name, "vault_preview", VAULT_PREVIEW_PRICE, "BAZRAGOD Vault 24hr Preview")
    kb = []
    if url: kb.append([InlineKeyboardButton(f"Pay ${VAULT_PREVIEW_PRICE} via Stripe", url=url)])
    kb.append([InlineKeyboardButton("CashApp", url=CASHAPP)])
    kb.append([InlineKeyboardButton("PayPal", url=PAYPAL)])
    await q.message.reply_text(
        f"VAULT PREVIEW\n\n24 hour access to all 5 exclusive songs.\nPrice: ${VAULT_PREVIEW_PRICE}\n\nDelivered instantly after payment.",
        reply_markup=InlineKeyboardMarkup(kb))

async def vault_full_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id; name = q.from_user.username or str(uid)
    url = create_checkout(uid, name, "vault_full", VAULT_FULL_PRICE, "BAZRAGOD Vault Full + Merch")
    kb = []
    if url: kb.append([InlineKeyboardButton(f"Pay ${VAULT_FULL_PRICE} via Stripe", url=url)])
    kb.append([InlineKeyboardButton("CashApp", url=CASHAPP)])
    kb.append([InlineKeyboardButton("PayPal", url=PAYPAL)])
    await q.message.reply_text(
        f"VAULT FULL PURCHASE\n\nAll 5 exclusive songs + signed T-shirt + pullover.\nPrice: ${VAULT_FULL_PRICE}\n\nMerch ships within 14 days.",
        reply_markup=InlineKeyboardMarkup(kb))

async def cmd_passport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; name = uname(update)
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT username, points, invites, tier, joined_at, is_supporter, passport_number FROM users WHERE telegram_id=%s", (uid,))
        row = cur.fetchone()
        if not row: await update.message.reply_text("Send /start first."); return
        username, points, invites, tier, joined_at, is_sup, pnum = row
        cur.execute("SELECT COUNT(*) FROM users WHERE points > %s", (points,))
        global_rank = cur.fetchone()[0] + 1
        cur.execute("SELECT COUNT(*) FROM downloads WHERE telegram_id=%s AND purchased=TRUE", (uid,))
        downloads = cur.fetchone()[0]
    finally:
        release_db(conn)
    pnum = pnum or f"P14-{uid % 100000:05d}"
    sup_badge = " — SUPPORTER" if is_sup else ""
    joined = joined_at.strftime("%B %Y") if joined_at else "Unknown"
    await update.message.reply_text(
        f"PARISH 14 PASSPORT\n\n"
        f"Passport:    {pnum}\n"
        f"Name:        @{username or name}{sup_badge}\n"
        f"Tier:        {tier}\n"
        f"MiserCoins:  {points:,}\n"
        f"Rank:        #{global_rank}\n"
        f"Invites:     {invites}\n"
        f"Downloads:   {downloads}\n"
        f"Joined:      {joined}\n\n"
        f"NEXT: {get_next_rank(points)}")

async def cmd_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT points, tier, invites FROM users WHERE telegram_id=%s", (uid,))
        row = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM users WHERE points > COALESCE((SELECT points FROM users WHERE telegram_id=%s),0)", (uid,))
        global_rank = cur.fetchone()[0] + 1
    finally:
        release_db(conn)
    pts, tier, invites = row if row else (0, "Fan", 0)
    await update.message.reply_text(
        f"YOUR MISERCOINS\n\n"
        f"Coins:   {pts:,}\n"
        f"Rank:    #{global_rank}\n"
        f"Tier:    {tier}\n"
        f"Invites: {invites}\n\n"
        f"NEXT: {get_next_rank(pts)}\n\n"
        f"Earn by playing songs, liking tracks and inviting fans.")

async def cmd_terms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"BAZRAGOD MUSIC NETWORK — TERMS\n\n"
        f"Terms:   {TERMS_URL}\n"
        f"Privacy: {PRIVACY_URL}\n"
        f"Refund:  {REFUND_URL}\n\n"
        f"Contact: {BOOKING_EMAIL}")

async def cmd_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"SUPPORT\n\n"
        f"Email: {BOOKING_EMAIL}\n"
        f"Response: within 24 hours\n\n"
        f"For refunds include your Telegram username and payment date.")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM users"); fans = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM songs"); songs = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(plays),0) FROM songs"); plays = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM downloads WHERE purchased=TRUE"); sales = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM stripe_sessions WHERE status='completed'"); stripe_sales = cur.fetchone()[0]
    finally:
        release_db(conn)
    await update.message.reply_text(
        f"BAZRAGOD NETWORK STATS\n\n"
        f"Fans:         {fans:,}\n"
        f"Songs:        {songs}\n"
        f"Total Plays:  {plays:,}\n"
        f"Downloads:    {sales}\n"
        f"Stripe Sales: {stripe_sales}")

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await update.message.reply_text(
        "ADMIN PANEL\n\n"
        "UPLOAD TAGS:\n"
        "#song #beat #drop #vault #picture\n\n"
        "COMMANDS:\n"
        "/list_songs\n"
        "/delete_song id\n"
        "/unlock uid song_id\n"
        "/broadcast message\n"
        "/stats")

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): return
    audio = update.message.audio
    if not audio: return
    caption = (update.message.caption or "").strip().lower()
    title = audio.title or audio.file_name or "Untitled"
    title = re.sub(r'#\w+', '', title).strip() or "Untitled"
    file_id = audio.file_id
    if "#vault" in caption:
        if check_duplicate(file_id, title, "vault_songs"):
            await update.message.reply_text(f"DUPLICATE — {title} already in vault."); return
        conn = get_db(); cur = conn.cursor()
        try:
            cur.execute("INSERT INTO vault_songs (title, file_id) VALUES (%s,%s) RETURNING id", (title, file_id))
            new_id = cur.fetchone()[0]; conn.commit()
        finally:
            release_db(conn)
        await update.message.reply_text(f"VAULT SONG ADDED\n\nID: {new_id}\nTitle: {title}")
        return
    tag_map = {
        "#song": ("songs", "Song"),
        "#beat": ("beats", "Beat"),
        "#drop": ("drops", "Drop"),
        "#announce": ("announcements", "Announcement")
    }
    for tag, (dest, label) in tag_map.items():
        if tag in caption:
            if check_duplicate(file_id, title, dest):
                await update.message.reply_text(f"DUPLICATE — {title} already exists."); return
            conn = get_db(); cur = conn.cursor()
            try:
                cur.execute(f"INSERT INTO {dest} (title, file_id) VALUES (%s,%s) RETURNING id", (title, file_id))
                new_id = cur.fetchone()[0]; conn.commit()
            finally:
                release_db(conn)
            upload_sessions[uid] = new_id
            await update.message.reply_text(f"{label.upper()} ADDED\n\nID: {new_id}\nTitle: {title}")
            return
    await update.message.reply_text("Add caption tag:\n#song #beat #drop #vault")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): return
    caption = (update.message.caption or "").strip().lower()
    if "#picture" not in caption: return
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    buf = BytesIO()
    await file.download_to_memory(buf)
    image_data = buf.getvalue()
    song_id = upload_sessions.get(uid)
    if not song_id:
        conn = get_db(); cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM songs ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            if row: song_id = row[0]
        finally:
            release_db(conn)
    if not song_id:
        await update.message.reply_text("No recent song found."); return
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT title FROM songs WHERE id=%s", (song_id,))
        row = cur.fetchone()
        song_title = row[0] if row else "Unknown"
        cur.execute("UPDATE songs SET artwork_data=%s WHERE id=%s", (image_data, song_id))
        conn.commit()
    finally:
        release_db(conn)
    await update.message.reply_text(f"ARTWORK ASSIGNED\n\nSong: {song_title}\nID: {song_id}")

async def cmd_list_songs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT id, title, plays FROM songs ORDER BY id")
        rows = cur.fetchall()
    finally:
        release_db(conn)
    text = f"CATALOG — {len(rows)} songs\n\n"
    for r in rows:
        text += f"[{r[0]}] {r[1]} — {r[2]:,} plays\n"
    await update.message.reply_text(text or "No songs yet.")

async def cmd_delete_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /delete_song <id>"); return
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("DELETE FROM songs WHERE id=%s RETURNING title", (int(args[0]),))
        row = cur.fetchone(); conn.commit()
    finally:
        release_db(conn)
    await update.message.reply_text(f"Deleted: {row[0]}" if row else "Not found.")

async def cmd_unlock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /unlock <uid> <song_id>"); return
    fan_id = int(args[0]); song_id = int(args[1])
    success = await deliver_song(context.bot, fan_id, song_id, "admin")
    await update.message.reply_text(f"Delivered to {fan_id}" if success else "Failed.")

async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /broadcast <message>"); return
    text = " ".join(args)
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT telegram_id FROM users")
        fans = cur.fetchall()
    finally:
        release_db(conn)
    sent = 0
    for (fid,) in fans:
        try:
            await context.bot.send_message(fid, f"BAZRAGOD\n\n{text}\n\nParish 14 Nation.")
            sent += 1
        except Exception: pass
    await update.message.reply_text(f"Sent to {sent} fans.")

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    routes = {
        "🎵 MUSIC": cmd_music,
        "🛒 STORE": cmd_music,
        "🔐 Secret Vault": cmd_vault,
        "🪪 My Passport": cmd_passport,
        "🪙 MiserCoins": cmd_coins,
        "📋 Terms": cmd_terms,
        "📞 Support": cmd_support,
        "📻 Radio": lambda u, c: u.message.reply_text(
            f"BAZRAGOD RADIO\n\nTune in live:\n{RADIO_CHANNEL}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Open Radio", url=RADIO_CHANNEL)
            ]])),
    }
    handler = routes.get(text)
    if handler:
        await handler(update, context)

@flask_app.route("/")
def health():
    return jsonify({"status": "ONLINE", "platform": "BAZRAGOD Music Network", "version": "1.0"})

@flask_app.route("/stripe_webhook", methods=["POST"])
def stripe_webhook():
    payload = flask_request.data
    sig_header = flask_request.headers.get("Stripe-Signature")
    if not STRIPE_OK: return "stripe not available", 400
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        return str(e), 400
    if event["type"] == "checkout.session.completed":
        asyncio.run_coroutine_threadsafe(
            handle_stripe_payment(event["data"]["object"], app.bot), loop)
    return "ok"

async def post_init(application):
    global app, loop
    app = application
    loop = asyncio.get_event_loop()

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)

def main():
    init_pool()
    init_db()
    print("=" * 50)
    print("BAZRAGOD MUSIC NETWORK v1.0")
    print("SOVEREIGN ARTIST PLATFORM")
    print("Bot: @BAZRAGODMusicNetwork_bot")
    print("Status: ONLINE")
    print("=" * 50)

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("music", cmd_music))
    application.add_handler(CommandHandler("cart", cmd_cart))
    application.add_handler(CommandHandler("vault", cmd_vault))
    application.add_handler(CommandHandler("passport", cmd_passport))
    application.add_handler(CommandHandler("coins", cmd_coins))
    application.add_handler(CommandHandler("terms", cmd_terms))
    application.add_handler(CommandHandler("support", cmd_support))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("admin", cmd_admin))
    application.add_handler(CommandHandler("list_songs", cmd_list_songs))
    application.add_handler(CommandHandler("delete_song", cmd_delete_song))
    application.add_handler(CommandHandler("unlock", cmd_unlock))
    application.add_handler(CommandHandler("broadcast", cmd_broadcast))

    application.add_handler(CallbackQueryHandler(gate_done_cb, pattern="^gate:done"))
    application.add_handler(CallbackQueryHandler(play_song_cb, pattern="^song:"))
    application.add_handler(CallbackQueryHandler(like_cb, pattern="^like:"))
    application.add_handler(CallbackQueryHandler(cart_add_cb, pattern="^cart_add:"))
    application.add_handler(CallbackQueryHandler(cart_remove_cb, pattern="^cart_remove:"))
    application.add_handler(CallbackQueryHandler(cart_clear_cb, pattern="^cart_clear"))
    application.add_handler(CallbackQueryHandler(cart_checkout_cb, pattern="^cart_checkout"))
    application.add_handler(CallbackQueryHandler(buy_song_cb, pattern="^buy_song:"))
    application.add_handler(CallbackQueryHandler(vault_play_cb, pattern="^vault_play:"))
    application.add_handler(CallbackQueryHandler(vault_preview_cb, pattern="^vault_preview"))
    application.add_handler(CallbackQueryHandler(vault_full_cb, pattern="^vault_full"))

    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
