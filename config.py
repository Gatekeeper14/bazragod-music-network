import os

def clean(val):
    return val.strip().lstrip("=").strip() if val else ""

BOT_TOKEN = clean(os.environ.get("BOT_TOKEN", ""))
OWNER_ID = int(clean(os.environ.get("OWNER_ID", "0")) or "0")
OPENAI_API_KEY = clean(os.environ.get("OPENAI_API_KEY", ""))
STRIPE_SECRET_KEY = clean(os.environ.get("STRIPE_SECRET_KEY", ""))
STRIPE_WEBHOOK_SECRET = clean(os.environ.get("STRIPE_WEBHOOK_SECRET", ""))
DATABASE_URL = clean(os.environ.get("DATABASE_URL", ""))
BOT_USERNAME = clean(os.environ.get("BOT_USERNAME", "BAZRAGODMusicNetwork_bot"))
BOOKING_EMAIL = clean(os.environ.get("BOOKING_EMAIL", "Miserbot.ai@gmail.com"))
CASHAPP = clean(os.environ.get("CASHAPP", "https://cash.app/$BAZRAGOD"))
PAYPAL = clean(os.environ.get("PAYPAL", "https://paypal.me/bazragod1"))
TON_WALLET = clean(os.environ.get("TON_WALLET", "UQDsYoQEPsvtF7rKPNP914GyisPBSAz__UkxDIUwSvAn7bAl"))
PARISH_LOUNGE = clean(os.environ.get("PARISH_LOUNGE", "https://t.me/parish14lounge"))
RADIO_CHANNEL = clean(os.environ.get("RADIO_CHANNEL", "https://t.me/bazragodradio"))
RADIO_CHANNEL_ID = clean(os.environ.get("RADIO_CHANNEL_ID", ""))

SONG_PRICE = 5
ALBUM_PRICE = 30
ALBUM_COUNT = 7
VAULT_PREVIEW_PRICE = 50
VAULT_FULL_PRICE = 500

TERMS_URL = "https://Gatekeeper14.github.io/bazragod-legal/terms"
PRIVACY_URL = "https://Gatekeeper14.github.io/bazragod-legal/privacy"
REFUND_URL = "https://Gatekeeper14.github.io/bazragod-legal/refund"
