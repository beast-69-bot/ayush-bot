import os
from dotenv import load_dotenv

# Load env variables from .env file
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# Channel & Group IDs
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
GROUP_ID = int(os.getenv("GROUP_ID", "0"))

# Manual Fallback Details (defaults for DB settings setup)
UPI_ID = os.getenv("UPI_ID", "agpriyanshu21@fam")
QR_FILE_ID = os.getenv("QR_FILE_ID", "YAHAN_APNA_FILE_ID_PASTE_KARO")

# Razorpay Credentials (defaults)
RZP_KEY_ID = os.getenv("RZP_KEY_ID", "rzp_live_SunwtsWUJuxPoe")
RZP_KEY_SECRET = os.getenv("RZP_KEY_SECRET", "bJ7AXzblK03QVXiON85qhYFv")

# Unified Plans (Prices, Durations, Type and Destination Chats)
PAY_PLANS = {
    "direct": {
        "label": "Direct Mods (30 Days)",
        "days": 30,
        "amount": 35,
        "stars": 35,
        "sub_type": "direct_mods",
        "chat_id": CHANNEL_ID
    },
    "getpin": {
        "label": "No Getpin (30 Days)",
        "days": 30,
        "amount": 30,
        "stars": 30,
        "sub_type": "getpin",
        "chat_id": None
    },
    "faphouse_3": {
        "label": "Faphouse Paid (3 Days)",
        "days": 3,
        "amount": 15,
        "stars": 15,
        "sub_type": "faphouse",
        "chat_id": GROUP_ID
    },
    "faphouse_7": {
        "label": "Faphouse Paid (7 Days)",
        "days": 7,
        "amount": 25,
        "stars": 25,
        "sub_type": "faphouse",
        "chat_id": GROUP_ID
    },
    "donation": {
        "label": "Test Donation (₹1 / 1 Star)",
        "days": 0,
        "amount": 1,
        "stars": 1,
        "sub_type": "donation",
        "chat_id": None
    }
}

