import asyncio
import json
import os
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import (
    CreateChannelRequest,
    InviteToChannelRequest,
    EditAdminRequest,
    LeaveChannelRequest,
)
from telethon.tl.types import ChatAdminRights
from telethon import utils as telethon_utils

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
ESCROWS_FILE = "escrows.json"

TELETHON_API_ID = 38828234
TELETHON_API_HASH = "99d96d08bc57f882907032a2f8f65b46"
TELETHON_SESSION = os.environ.get("TELETHON_SESSION", "")

BOT_USERNAME = "EcroweBot"
BOT_ID = 8029678424

state_lock = asyncio.Lock()
telethon_client = None


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"next_id": 50}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def load_escrows():
    if os.path.exists(ESCROWS_FILE):
        with open(ESCROWS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_escrows(escrows):
    with open(ESCROWS_FILE, "w") as f:
        json.dump(escrows, f)


def get_next_escrow_id():
    state = load_state()
    escrow_id = state["next_id"]
    state["next_id"] = escrow_id + 1
    save_state(state)
    return escrow_id


def save_escrow(escrow_id, data, chat_id, message_id):
    escrows = load_escrows()
    escrows[str(escrow_id)] = {
        "seller": data["seller"],
        "buyer": data["buyer"],
        "amount": data["amount"],
        "rate": data["rate"],
        "time": data["time"],
        "total_inr": data["total_inr"],
        "chat_id": chat_id,
        "message_id": message_id,
        "seller_confirmed": False,
        "buyer_confirmed": False,
    }
    save_escrows(escrows)


def get_escrow(escrow_id):
    escrows = load_escrows()
    return escrows.get(str(escrow_id))


def update_escrow(escrow_id, updates):
    escrows = load_escrows()
    if str(escrow_id) in escrows:
        escrows[str(escrow_id)].update(updates)
        save_escrows(escrows)
        return True
    return False


def get_escrow_by_room_chat_id(room_chat_id):
    escrows = load_escrows()
    for eid, data in escrows.items():
        if data.get("room_chat_id") == room_chat_id:
            return int(eid), data
    return None, None


async def init_telethon_client():
    global telethon_client
    if telethon_client is None and TELETHON_SESSION:
        telethon_client = TelegramClient(
            StringSession(TELETHON_SESSION),
            TELETHON_API_ID,
            TELETHON_API_HASH
        )
        await telethon_client.connect()
    return telethon_client


async def create_escrow_room(escrow_id):
    client = await init_telethon_client()
    if not client:
        return None

    escrow_id_str = f"{escrow_id:08d}"
    group_title = f"Elite Escrow Group No. {escrow_id_str}"

    result = await client(CreateChannelRequest(
        title=group_title,
        about="Private escrow room",
        megagroup=True
    ))

    channel = result.chats[0]
    room_chat_id = telethon_utils.get_peer_id(channel)

    bot_entity = await client.get_entity(BOT_USERNAME)

    await client(InviteToChannelRequest(
        channel=channel,
        users=[bot_entity]
    ))

    admin_rights = ChatAdminRights(
        change_info=True,
        post_messages=True,
        edit_messages=True,
        delete_messages=True,
        ban_users=True,
        invite_users=True,
        pin_messages=True,
        add_admins=False,
        anonymous=False,
        manage_call=True,
        other=True
    )

    await client(EditAdminRequest(
        channel=channel,
        user_id=bot_entity,
        admin_rights=admin_rights,
        rank="Admin"
    ))

    await client(LeaveChannelRequest(channel))

    update_escrow(escrow_id, {"room_chat_id": room_chat_id})

    return room_chat_id


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


def build_escrow_message(escrow_id, data, seller_confirmed=False,
                         buyer_confirmed=False):
    seller = escape_html(data["seller"])
    buyer = escape_html(data["buyer"])
    amount = data["amount"]
    rate = data["rate"]
    total_inr = data["total_inr"]
    time_val = escape_html(data["time"])

    escrow_id_str = f"{escrow_id:08d}"

    seller_emoji = "✅" if seller_confirmed else "⏳"
    buyer_emoji = "✅" if buyer_confirmed else "⏳"

    seller_note = f"Only {seller} can press Seller Confirm;"
    buyer_note = f"only {buyer} can press Buyer Confirm."

    message = f"""🟢 Escrow • <code>{escrow_id_str}</code>
━━━━━━━━━━━━━━━━━━━━
{seller_emoji} <b>Seller</b>: {seller}
{buyer_emoji} <b>Buyer</b>: {buyer}
💵 <b>Amount</b>: {amount:.1f} USDT (BEP-20)
💱 <b>Rate</b>: {rate:.1f} INR/USDT
💰 <b>Total INR</b>: ₹{total_inr:.1f}
🕒 <b>Time</b>: {time_val}

🎉 <b>New Year Offer</b>: <code>0 USDT</code> platform fee - escrow is FREE.

<b>Status</b>: Waiting for both confirmations.
<i>{seller_note} {buyer_note}</i>"""

    return message


def build_escrow_keyboard(escrow_id, seller_confirmed=False,
                          buyer_confirmed=False):
    buttons = []

    if not seller_confirmed:
        buttons.append(
            InlineKeyboardButton(
                "✅ Seller Confirm",
                callback_data=f"escrow:{escrow_id}:seller"
            )
        )

    if not buyer_confirmed:
        buttons.append(
            InlineKeyboardButton(
                "✅ Buyer Confirm",
                callback_data=f"escrow:{escrow_id}:buyer"
            )
        )

    if buttons:
        return InlineKeyboardMarkup([buttons])
    return None


def build_confirmed_message(escrow_id, data):
    seller = escape_html(data["seller"])
    buyer = escape_html(data["buyer"])
    amount = data["amount"]
    rate = data["rate"]
    total_inr = data["total_inr"]
    time_val = escape_html(data["time"])

    escrow_id_str = f"{escrow_id:08d}"

    message = f"""🟢 Escrow • <code>{escrow_id_str}</code>
━━━━━━━━━━━━━━━━━━━━
✅ <b>Seller</b>: {seller}
✅ <b>Buyer</b>: {buyer}
💵 <b>Amount</b>: {amount:.1f} USDT (BEP-20)
💱 <b>Rate</b>: {rate:.1f} INR/USDT
💰 <b>Total INR</b>: ₹{total_inr:.1f}
🕒 <b>Time</b>: {time_val}

<b>Status</b>: Moved to private escrow room.

✅<b>Private escrow room created.</b>
Continue the escrow steps <b>inside the private room</b>.
Use the buttons below to get your one-time join link.

<b>Status</b>: Opening private escrow room..."""

    return message


def build_opening_room_keyboard(escrow_id):
    button = InlineKeyboardButton(
        "⏳ Opening private escrow room...",
        callback_data=f"escrow:{escrow_id}:noop"
    )
    return InlineKeyboardMarkup([[button]])


def build_room_ready_message(escrow_id, data):
    seller = escape_html(data["seller"])
    buyer = escape_html(data["buyer"])
    amount = data["amount"]
    rate = data["rate"]
    total_inr = data["total_inr"]
    time_val = escape_html(data["time"])

    escrow_id_str = f"{escrow_id:08d}"

    message = f"""🟢 Escrow • <code>{escrow_id_str}</code>
━━━━━━━━━━━━━━━━━━━━
✅ <b>Seller</b>: {seller}
✅ <b>Buyer</b>: {buyer}
💵 <b>Amount</b>: {amount:.1f} USDT (BEP-20)
💱 <b>Rate</b>: {rate:.1f} INR/USDT
💰 <b>Total INR</b>: ₹{total_inr:.1f}
🕒 <b>Time</b>: {time_val}

<b>Status</b>: Moved to private escrow room.

✅<b>Private escrow room created.</b>
Continue the escrow steps <b>inside the private room</b>.
Use the buttons below to get your one-time join link."""

    return message


def build_join_buttons_keyboard(buyer_invite, seller_invite):
    buyer_button = InlineKeyboardButton(
        "🚪 Buyer: Join private escrow group",
        url=buyer_invite
    )
    seller_button = InlineKeyboardButton(
        "🚪 Seller: Join private escrow group",
        url=seller_invite
    )
    return InlineKeyboardMarkup([[buyer_button], [seller_button]])


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
        keyboard = build_escrow_keyboard(escrow_id)

        sent_msg = await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=escrow_message,
            parse_mode="HTML",
            reply_markup=keyboard
        )

        save_escrow(
            escrow_id,
            validated,
            update.message.chat_id,
            sent_msg.message_id
        )


def normalize_username(username):
    if not username:
        return None
    return username.lstrip("@").lower()


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if not query.data.startswith("escrow:"):
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Invalid callback data")
        return

    escrow_id = int(parts[1])
    action = parts[2]

    if action == "noop":
        await query.answer()
        return

    async with state_lock:
        escrow = get_escrow(escrow_id)
        if not escrow:
            await query.answer("Escrow not found")
            return

        user_username = normalize_username(
            query.from_user.username if query.from_user else None
        )

        if not user_username:
            await query.answer(
                "You need a username to confirm", show_alert=True
            )
            return

        if action == "seller":
            seller_username = normalize_username(escrow["seller"])
            if user_username != seller_username:
                await query.answer(
                    "Only the seller can press this button",
                    show_alert=True
                )
                return

            if escrow["seller_confirmed"]:
                await query.answer("Already confirmed")
                return

            seller_user_id = query.from_user.id
            update_escrow(escrow_id, {
                "seller_confirmed": True,
                "seller_user_id": seller_user_id
            })
            escrow["seller_confirmed"] = True
            escrow["seller_user_id"] = seller_user_id

        elif action == "buyer":
            buyer_username = normalize_username(escrow["buyer"])
            if user_username != buyer_username:
                await query.answer(
                    "Only the buyer can press this button",
                    show_alert=True
                )
                return

            if escrow["buyer_confirmed"]:
                await query.answer("Already confirmed")
                return

            buyer_user_id = query.from_user.id
            update_escrow(escrow_id, {
                "buyer_confirmed": True,
                "buyer_user_id": buyer_user_id
            })
            escrow["buyer_confirmed"] = True
            escrow["buyer_user_id"] = buyer_user_id

        seller_ok = escrow["seller_confirmed"]
        buyer_ok = escrow["buyer_confirmed"]
        both_confirmed = seller_ok and buyer_ok

        if both_confirmed:
            new_message = build_confirmed_message(escrow_id, escrow)
            new_keyboard = build_opening_room_keyboard(escrow_id)

            asyncio.create_task(create_escrow_room(escrow_id))
        else:
            new_message = build_escrow_message(
                escrow_id,
                escrow,
                seller_confirmed=escrow["seller_confirmed"],
                buyer_confirmed=escrow["buyer_confirmed"]
            )
            new_keyboard = build_escrow_keyboard(
                escrow_id,
                seller_confirmed=escrow["seller_confirmed"],
                buyer_confirmed=escrow["buyer_confirmed"]
            )

        await query.edit_message_text(
            text=new_message,
            parse_mode="HTML",
            reply_markup=new_keyboard
        )

        await query.answer("Confirmed!")


async def handle_new_chat_members(update: Update,
                                  context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat_id = update.effective_chat.id
    message_id = update.message.message_id

    new_members = update.message.new_chat_members or []
    bot_was_added = any(m.id == BOT_ID for m in new_members)

    if bot_was_added:
        escrow_id, escrow = get_escrow_by_room_chat_id(chat_id)
        if escrow_id:
            escrow_id_str = f"{escrow_id:08d}"
            welcome_msg = (
                f"<b>Escrow Room</b> <code>{escrow_id_str}</code>\n"
                "This is the private room for this deal.\n"
                "The escrow steps will continue here."
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text=welcome_msg,
                parse_mode="HTML"
            )

            try:
                buyer_link = await context.bot.create_chat_invite_link(
                    chat_id=chat_id,
                    creates_join_request=True,
                    name="Buyer link"
                )
                seller_link = await context.bot.create_chat_invite_link(
                    chat_id=chat_id,
                    creates_join_request=True,
                    name="Seller link"
                )

                update_escrow(escrow_id, {
                    "buyer_invite": buyer_link.invite_link,
                    "seller_invite": seller_link.invite_link
                })

                original_chat_id = escrow.get("chat_id")
                original_message_id = escrow.get("message_id")

                if original_chat_id and original_message_id:
                    updated_escrow = get_escrow(escrow_id)
                    new_message = build_room_ready_message(
                        escrow_id, updated_escrow
                    )
                    new_keyboard = build_join_buttons_keyboard(
                        buyer_link.invite_link,
                        seller_link.invite_link
                    )

                    await context.bot.edit_message_text(
                        chat_id=original_chat_id,
                        message_id=original_message_id,
                        text=new_message,
                        parse_mode="HTML",
                        reply_markup=new_keyboard
                    )
            except Exception:
                pass

    try:
        await asyncio.sleep(1)
        await context.bot.delete_message(
            chat_id=chat_id, message_id=message_id
        )
    except Exception:
        pass


async def handle_left_chat_member(update: Update,
                                  context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat_id = update.effective_chat.id
    message_id = update.message.message_id

    try:
        await asyncio.sleep(1)
        await context.bot.delete_message(
            chat_id=chat_id, message_id=message_id
        )
    except Exception:
        pass


async def handle_join_request(update: Update,
                              context: ContextTypes.DEFAULT_TYPE):
    join_request = update.chat_join_request
    if not join_request:
        return

    chat_id = join_request.chat.id
    user_id = join_request.from_user.id

    escrow_id, escrow = get_escrow_by_room_chat_id(chat_id)
    if not escrow_id or not escrow:
        try:
            await join_request.decline()
        except Exception:
            pass
        return

    seller_user_id = escrow.get("seller_user_id")
    buyer_user_id = escrow.get("buyer_user_id")

    if user_id == seller_user_id or user_id == buyer_user_id:
        try:
            await join_request.approve()
        except Exception:
            pass
    else:
        try:
            await join_request.decline()
        except Exception:
            pass


BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")

app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
)
app.add_handler(CallbackQueryHandler(handle_callback))
new_members_filter = filters.StatusUpdate.NEW_CHAT_MEMBERS
left_member_filter = filters.StatusUpdate.LEFT_CHAT_MEMBER
app.add_handler(MessageHandler(new_members_filter, handle_new_chat_members))
app.add_handler(MessageHandler(left_member_filter, handle_left_chat_member))
app.add_handler(ChatJoinRequestHandler(handle_join_request))
app.run_polling()
