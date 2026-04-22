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

# Stripe
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
    return response

# GLOBALS
app = None
loop = None

# ------------------ FIXED WEBHOOK ------------------

@flask_app.route("/")
def health():
    return jsonify({"status": "ONLINE"})

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = flask_request.get_json()

        if data and app:
            update = Update.de_json(data, app.bot)

            asyncio.run_coroutine_threadsafe(
                app.process_update(update),
                loop
            )

        return "ok"

    except Exception as e:
        print("WEBHOOK ERROR:", e)
        return "error", 500

# ------------------ FIXED STRIPE ------------------

@flask_app.route("/stripe_webhook", methods=["POST"])
def stripe_webhook():
    if not STRIPE_OK:
        return "stripe disabled", 400

    try:
        event = stripe.Webhook.construct_event(
            flask_request.data,
            flask_request.headers.get("Stripe-Signature"),
            STRIPE_WEBHOOK_SECRET
        )

        if event["type"] == "checkout.session.completed":
            asyncio.run_coroutine_threadsafe(
                handle_stripe_payment(event["data"]["object"], app.bot),
                loop
            )

        return "ok"

    except Exception as e:
        print("STRIPE ERROR:", e)
        return str(e), 400

# ------------------ CORE LOGIC (UNCHANGED) ------------------

def is_admin(uid):
    return uid == OWNER_ID

def uname(update):
    u = update.effective_user
    return u.username or u.first_name or str(u.id)

# (ALL YOUR ORIGINAL FUNCTIONS STAY — no changes needed)

# ------------------ STRIPE HANDLER ------------------

async def handle_stripe_payment(session_data, bot):
    try:
        uid = int(session_data.get("metadata", {}).get("telegram_id", 0))

        if uid:
            await bot.send_message(uid, "Payment received. Processing delivery...")

    except Exception as e:
        print("DELIVERY ERROR:", e)

# ------------------ LOOP FIX ------------------

async def post_init(application):
    global app, loop
    app = application
    loop = asyncio.get_running_loop()

# ------------------ FLASK RUN ------------------

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True
    )

# ------------------ MAIN ------------------

def main():
    init_pool()
    init_db()

    print("BAZRAGOD MUSIC NETWORK ONLINE")

    threading.Thread(target=run_flask, daemon=True).start()

    application = Application.builder()\
        .token(BOT_TOKEN)\
        .post_init(post_init)\
        .build()

    # COMMANDS
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

    # CALLBACKS
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

    # HANDLERS
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    # ✅ STABLE MODE
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
