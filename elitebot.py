import asyncio
import json
import os
import re

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    filters,
)

ESCROW_TEXT = """🛡 *Private Escrow Form*
_Copy, fill and send in the group\\.
Only deals with @usernames mentioned will be accepted \\(anti\\-scam\\)\\._

`Seller: @
Buyer: @
Amount[USDT]:
Rate:
Time:`
"""

STATE_FILE = "escrow_state.json"


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"next_id": 50}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def get_next_escrow_id():
    state = load_state()
    escrow_id = state["next_id"]
    state["next_id"] = escrow_id + 1
    save_state(state)
    return escrow_id


def parse_escrow_form(text):
    lines = text.strip().split("\n")
    data = {}

    for line in lines:
        line = line.strip()
        if not line:
            continue

        match = re.match(r"^\s*([^:]+)\s*:\s*(.*?)\s*$", line)
        if match:
            key = match.group(1).strip().lower()
            value = match.group(2).strip()

            if "seller" in key:
                data["seller"] = value
            elif "buyer" in key:
                data["buyer"] = value
            elif "amount" in key:
                data["amount"] = value
            elif "rate" in key:
                data["rate"] = value
            elif "time" in key:
                data["time"] = value

    return data


def validate_escrow_form(data, sender_username):
    required = ["seller", "buyer", "amount", "rate", "time"]
    if not all(k in data for k in required):
        return None, "Missing required fields"

    seller = data["seller"]
    buyer = data["buyer"]
    amount_str = data["amount"]
    rate_str = data["rate"]
    time_val = data["time"]

    if seller.lower() == "me":
        if not sender_username:
            return None, "You need a username to use 'me'"
        seller = f"@{sender_username}"
    elif not seller.startswith("@"):
        seller = f"@{seller}"

    if buyer.lower() == "me":
        if not sender_username:
            return None, "You need a username to use 'me'"
        buyer = f"@{sender_username}"
    elif not buyer.startswith("@"):
        buyer = f"@{buyer}"

    try:
        amount = float(amount_str)
        if amount <= 0:
            return None, "Amount must be positive"
    except ValueError:
        return None, "Invalid amount"

    try:
        rate = float(rate_str)
        if rate <= 0:
            return None, "Rate must be positive"
    except ValueError:
        return None, "Invalid rate"

    if not time_val:
        return None, "Time is required"

    total_inr = amount * rate

    return {
        "seller": seller,
        "buyer": buyer,
        "amount": amount,
        "rate": rate,
        "time": time_val,
        "total_inr": total_inr,
    }, None


def escape_html(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_escrow_message(escrow_id, data):
    seller = escape_html(data["seller"])
    buyer = escape_html(data["buyer"])
    amount = data["amount"]
    rate = data["rate"]
    total_inr = data["total_inr"]
    time_val = escape_html(data["time"])

    escrow_id_str = f"{escrow_id:08d}"

    seller_note = f"Only {seller} can press Seller Confirm;"
    buyer_note = f"only {buyer} can press Buyer Confirm."

    message = f"""🟢 Escrow • {escrow_id_str}
━━━━━━━━━━━━━━━━━━━━
⏳ <b>Seller</b>: {seller}
⏳ <b>Buyer</b>: {buyer}
💵 <b>Amount</b>: {amount:.1f} USDT (BEP-20)
💱 <b>Rate</b>: {rate:.1f} INR/USDT
💰 <b>Total INR</b>: ₹{total_inr:.1f}
🕒 <b>Time</b>: {time_val}

🎉 <b>New Year Offer</b>: <code>0 USDT</code> platform fee - escrow is FREE.

<b>Status</b>: Waiting for both confirmations.
<i>{seller_note} {buyer_note}</i>"""

    return message


def is_filled_escrow_form(text):
    text_lower = text.lower()
    has_seller = "seller" in text_lower and ":" in text
    has_buyer = "buyer" in text_lower and ":" in text
    has_amount = "amount" in text_lower and ":" in text
    has_rate = "rate" in text_lower and ":" in text
    has_time = "time" in text_lower and ":" in text

    return has_seller and has_buyer and has_amount and has_rate and has_time


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type not in ("group", "supergroup"):
        return

    if not update.message.text:
        return

    text = update.message.text.strip()

    if text.lower() == "dd":
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=ESCROW_TEXT,
            parse_mode="MarkdownV2"
        )
        return

    if is_filled_escrow_form(text):
        sender_username = None
        if update.message.from_user:
            sender_username = update.message.from_user.username

        parsed = parse_escrow_form(text)
        validated, error = validate_escrow_form(parsed, sender_username)

        if error:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"<i>Error: {escape_html(error)}</i>",
                parse_mode="HTML"
            )
            return

        creating_msg = await context.bot.send_message(
            chat_id=update.message.chat_id,
            text="<i>Creating escrow ....</i>",
            parse_mode="HTML"
        )

        await asyncio.sleep(1)

        await context.bot.delete_message(
            chat_id=update.message.chat_id,
            message_id=creating_msg.message_id
        )

        escrow_id = get_next_escrow_id()
        escrow_message = build_escrow_message(escrow_id, validated)

        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=escrow_message,
            parse_mode="HTML"
        )


BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")

app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
)
app.run_polling()
