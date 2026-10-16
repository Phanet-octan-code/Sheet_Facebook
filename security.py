"""Security, access control, and sanitization module for Telegram Bot & Google Sheets.

Implements:
1. Strict user authorization checks (whitelisted Telegram User IDs in .env).
2. Spreadsheet formula injection defense (CWE-1236) to safely store cookies, 2FA, and UIDs.
3. Safe audit logging.
"""

from __future__ import annotations

import logging
from typing import Any

from config import get_allowed_telegram_user_ids

# Setup audit logger
logger = logging.getLogger("telegram_sheets_bot.security")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [Security] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


# -----------------------------------------------------------------------------
# 1. Access Control
# -----------------------------------------------------------------------------
def is_user_authorized(telegram_user_id: int) -> bool:
    """Checks whether a Telegram User ID is in the allowed whitelist in .env.

    If ALLOWED_TELEGRAM_USER_IDS is empty, access is denied by default to
    prevent unauthorized writes.
    """
    allowed_ids = get_allowed_telegram_user_ids()
    if not allowed_ids:
        return False
    return telegram_user_id in allowed_ids


# -----------------------------------------------------------------------------
# 2. Spreadsheet Formula Injection Defense (CWE-1236)
# -----------------------------------------------------------------------------
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_for_sheets(value: Any) -> str:
    """Sanitizes user input to prevent Google Sheets Formula Injection (CWE-1236).

    If text begins with =, +, -, @, \\t, or \\r, prepend a single quote (')
    which instructs Google Sheets to treat the cell strictly as plain text.
    """
    if value is None:
        return ""

    text = str(value).strip()

    # If the text starts with a formula trigger character, escape with a single quote (')
    if text.startswith(FORMULA_PREFIXES):
        text = "'" + text

    # Strip dangerous non-printable control characters (keep regular space and newline)
    text = "".join(ch for ch in text if ch >= " " or ch in "\n\r\t")

    return text


# -----------------------------------------------------------------------------
# 3. Audit Logging
# -----------------------------------------------------------------------------
def audit_log(action: str, telegram_user_id: int, details: str = "") -> None:
    """Logs audit events without leaking entire session cookies.

    Args:
        action: Short action description (e.g., 'ACCOUNT_INSERTED', 'ACCOUNT_REPAIRED').
        telegram_user_id: User who initiated the action.
        details: Safe context (e.g., 'UID: 61595168473089, Row: 12').
    """
    clean_details = sanitize_for_sheets(details)
    logger.info(
        "AUDIT: action=%s user_id=%s details=%s",
        action,
        telegram_user_id,
        clean_details,
    )
