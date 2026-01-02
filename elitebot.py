from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters

ESCROW_TEXT = """🛡 Private Escrow Form
Copy, fill and send in the group.
Only deals with @usernames mentioned will be accepted (anti-scam).

Seller: @
Buyer: @
Amount[USDT]: 
Rate: 
Time:
"""

async def group_dd_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type in ("group", "supergroup"):
        if update.message.text and update.message.text.strip().lower() == "dd":
            await update.message.reply_text(ESCROW_TEXT)

app = ApplicationBuilder().token("YOUR_BOT_TOKEN").build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, group_dd_reply))
app.run_polling()
