import os, re, time, random, asyncio, threading, json
from datetime import datetime, date, timedelta
from io import BytesIO
from flask import Flask, request as flask_request, jsonify
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
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
ENTRY_CACHE = {}
GATE_CACHE = {}
upload_sessions = {}

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
async def handle_stripe_payment(session_data, bot):
    uid = int(session_data.get("metadata", {}).get("telegram_id", 0))
    product_type = session_data.get("metadata", {}).get("product_type", "")
    product_id = session_data.get("metadata", {}).get("product_id", "")

    if not uid:
        return

    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("UPDATE stripe_sessions SET status='completed' WHERE session_id=%s", (session_data.get("id"),))
        conn.commit()
    finally:
        release_db(conn)

    if product_type == "single_song":
        await deliver_song(bot, uid, int(product_id))


@flask_app.route("/stripe_webhook", methods=["POST"])
def stripe_webhook():
    payload = flask_request.data
    sig_header = flask_request.headers.get("Stripe-Signature")

    if not STRIPE_OK:
        return "stripe not available", 400

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        return str(e), 400

    if event["type"] == "checkout.session.completed":
        asyncio.run_coroutine_threadsafe(
            handle_stripe_payment(event["data"]["object"], app.bot),
            loop
        )

    return "ok"
async def post_init(application):
    global app, loop
    app = application
    loop = asyncio.get_running_loop()


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False)


def main():
    init_pool()
    init_db()

    print("BAZRAGOD MUSIC NETWORK RUNNING")

    # Flask thread (for Stripe only)
    threading.Thread(target=run_flask, daemon=True).start()

    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # COMMANDS
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("music", cmd_music))
    application.add_handler(CommandHandler("cart", cmd_cart))
    application.add_handler(CommandHandler("vault", cmd_vault))
    application.add_handler(CommandHandler("passport", cmd_passport))
    application.add_handler(CommandHandler("coins", cmd_coins))
    application.add_handler(CommandHandler("terms", cmd_terms))
    application.add_handler(CommandHandler("support", cmd_support))

    # CALLBACKS
    application.add_handler(CallbackQueryHandler(gate_done_cb, pattern="^gate:done"))
    application.add_handler(CallbackQueryHandler(play_song_cb, pattern="^song:"))
    application.add_handler(CallbackQueryHandler(like_cb, pattern="^like:"))
    application.add_handler(CallbackQueryHandler(cart_add_cb, pattern="^cart_add:"))
    application.add_handler(CallbackQueryHandler(cart_checkout_cb, pattern="^cart_checkout"))

    # MESSAGES
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    # ✅ THIS IS THE FIX
    application.run_polling()


if __name__ == "__main__":
    main()
