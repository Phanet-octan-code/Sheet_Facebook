"""Telegram Bot entry point for Facebook Account & Google Sheets Manager.

Features:
- Instant auto-parsing of Facebook accounts (UID, Password, 2FA, Cookie).
- Auto-inserting new accounts into Google Sheets with auto NO.
- Auto-repairing/updating existing accounts matching by UID.
- Minimal, clean 1-line success alerts ("don't show alert lot").
- Sheet repair command (/repair) and accounts viewer (/list).
"""

from __future__ import annotations

import logging
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from parser import FacebookAccount, format_account_card, format_bulk_summary, parse_input_accounts
from security import audit_log, is_user_authorized
from sheets_service import sheets_service

# Configure console logging
logging.basicConfig(
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger("telegram_sheets_bot.bot")


# -----------------------------------------------------------------------------
# Command Handlers
# -----------------------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command."""
    if not update.effective_user or not update.message:
        return

    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "User"
    authorized = is_user_authorized(user_id)

    if not authorized:
        audit_log("UNAUTHORIZED_ACCESS_ATTEMPT", user_id, "Command: /start")
        unauth_msg = (
            f"👋 Hello, <b>{user_name}</b>!\n\n"
            "🔒 <b>Access Status:</b> <code>Unauthorized</code>\n"
            f"🆔 <b>Your Telegram User ID:</b> <code>{user_id}</code>\n\n"
            "Add this ID to <code>ALLOWED_TELEGRAM_USER_IDS</code> in <code>.env</code>."
        )
        await update.message.reply_text(unauth_msg, parse_mode=ParseMode.HTML)
        return

    audit_log("AUTHORIZED_START", user_id)
    welcome_msg = (
        f"👋 Welcome, <b>{user_name}</b>!\n\n"
        "✅ <b>Access Status:</b> <code>Authorized</code>\n"
        "Paste your Facebook accounts here anytime to auto-sync with Google Sheets!"
    )
    await update.message.reply_text(welcome_msg, parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /help command."""
    if not update.effective_user or not update.message:
        return

    user_id = update.effective_user.id
    if not is_user_authorized(user_id):
        return

    help_msg = (
        "📖 <b>Supported Formats:</b>\n\n"
        "<b>1️⃣ Multiline:</b>\n"
        "<pre>UID\n2FA\nCookie</pre>\n\n"
        "<b>2️⃣ Pipe-separated:</b>\n"
        "<pre>UID|Password|2FA|Cookie</pre>\n\n"
        "<b>3️⃣ Bulk:</b>\n"
        "Paste multiple accounts (one per line)."
    )
    await update.message.reply_text(help_msg, parse_mode=ParseMode.HTML)


async def repair_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /repair command to auto-repair numbering and formatting in the sheet."""
    if not update.effective_user or not update.message:
        return

    user_id = update.effective_user.id
    if not is_user_authorized(user_id):
        return

    try:
        res = sheets_service.repair_sheet()
        reply = f"🛠️ <b>Repaired:</b> {res['total_rows']} rows verified, {res['renumbered_count']} renumbered."
        await update.message.reply_text(reply, parse_mode=ParseMode.HTML)
    except Exception as exc:
        logger.error("Error running /repair: %s", exc)
        await update.message.reply_text(f"❌ <b>Error:</b> {exc}", parse_mode=ParseMode.HTML)


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /list command, showing recent accounts from Google Sheets."""
    if not update.effective_user or not update.message:
        return

    user_id = update.effective_user.id
    if not is_user_authorized(user_id):
        return

    try:
        recent = sheets_service.get_recent_accounts(limit=5)
        if not recent:
            await update.message.reply_text("📭 Sheet is empty.", parse_mode=ParseMode.HTML)
            return

        lines = ["📊 <b>Recent Accounts in Sheet:</b>"]
        for acc in recent:
            lines.append(f"• NO. {acc['no']} | <code>{acc['uid']}</code> | {acc['password'] or '-'}")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as exc:
        logger.error("Error in /list: %s", exc)
        await update.message.reply_text(f"❌ <b>Error:</b> {exc}", parse_mode=ParseMode.HTML)


# -----------------------------------------------------------------------------
# Main Message Handler (Clean & Short Alerts)
# -----------------------------------------------------------------------------
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processes incoming text messages and provides a short, minimal success alert."""
    if not update.effective_user or not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    raw_text = update.message.text.strip()

    # 1. Access Control
    if not is_user_authorized(user_id):
        await update.message.reply_text(
            f"⛔ <b>Access Denied.</b> User ID: <code>{user_id}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    # 2. Parse Accounts
    accounts = parse_input_accounts(raw_text)
    if not accounts:
        await update.message.reply_text(
            "❌ Could not recognize Facebook account data. Send /help for examples.",
            parse_mode=ParseMode.HTML,
        )
        return

    # 3. Single Account: 1-line short alert
    if len(accounts) == 1:
        account = accounts[0]
        try:
            result = sheets_service.insert_or_repair_account(account)
            card = format_account_card(
                account=result["account"],
                action=result["action"],
                row_num=result["row"],
                sheet_name=result["sheet_name"],
            )
            await update.message.reply_text(card, parse_mode=ParseMode.HTML)
        except Exception as exc:
            logger.error("Error auto-inserting/repairing account: %s", exc)
            await update.message.reply_text(
                f"❌ <b>Error:</b> <code>{exc}</code>",
                parse_mode=ParseMode.HTML,
            )
        return

    # 4. Bulk Accounts: 1-line short summary
    total = len(accounts)
    try:
        inserted_count = 0
        repaired_count = 0
        for acc in accounts:
            res = sheets_service.insert_or_repair_account(acc)
            if res["action"] == "INSERTED":
                inserted_count += 1
            else:
                repaired_count += 1

        summary = format_bulk_summary(total, inserted_count, repaired_count)
        await update.message.reply_text(summary, parse_mode=ParseMode.HTML)

    except Exception as exc:
        logger.error("Error in bulk processing: %s", exc)
        await update.message.reply_text(
            f"❌ <b>Error:</b> <code>{exc}</code>",
            parse_mode=ParseMode.HTML,
        )


# -----------------------------------------------------------------------------
# Global Error Handler
# -----------------------------------------------------------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catches unexpected exceptions safely without leaking sensitive information."""
    logger.error("Exception while handling update: %s", context.error)


# -----------------------------------------------------------------------------
# Bot Initialization & Startup
# -----------------------------------------------------------------------------
def build_application() -> Application:
    """Builds and configures the python-telegram-bot Application."""
    app = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .pool_timeout(30.0)
        .build()
    )

    # Register Command Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("repair", repair_command))
    app.add_handler(CommandHandler("list", list_command))

    # Register Message Handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    # Register Global Error Handler
    app.add_error_handler(error_handler)

    return app


def main() -> None:
    """Main execution function."""
    logger.info("Initializing Facebook Account Telegram Bot...")
    app = build_application()
    logger.info("Bot is starting polling... Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
