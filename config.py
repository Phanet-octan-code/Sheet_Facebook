"""Configuration management for Facebook Account Telegram Bot & Google Sheets Manager.

Loads environment variables from .env file and provides typed access.
Supports:
1. Google Apps Script Web App (GOOGLE_APPS_SCRIPT_URL)
2. Direct JSON content in environment variable (GOOGLE_CREDENTIALS_JSON) for cloud hosting
3. Google Service Account credentials.json file
"""

from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Base directory of the project
BASE_DIR: Path = Path(__file__).resolve().parent

# Load environment variables from .env file
load_dotenv(dotenv_path=BASE_DIR / ".env", override=True)

# -----------------------------------------------------------------------------
# Telegram Settings
# -----------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# -----------------------------------------------------------------------------
# Google Sheets Settings
# -----------------------------------------------------------------------------
# Option A: Google Apps Script Web App URL
GOOGLE_APPS_SCRIPT_URL: str = os.getenv("GOOGLE_APPS_SCRIPT_URL", "").strip()

# Option B1: Direct JSON Content in Environment Variable (Ideal for cloud hosting like Render/Railway)
GOOGLE_CREDENTIALS_JSON: str = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()

# Option B2: Google Service Account JSON file on disk
_creds_path_str = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "credentials.json").strip()
GOOGLE_SERVICE_ACCOUNT_FILE: Path = (
    Path(_creds_path_str) if Path(_creds_path_str).is_absolute() else (BASE_DIR / _creds_path_str)
)

GOOGLE_SHEET_NAME: str = os.getenv("GOOGLE_SHEET_NAME", "Telegram Safe Records").strip()
GOOGLE_SHEET_ID: str = os.getenv("GOOGLE_SHEET_ID", "16GqNZYKqV1LCaG2C8B_etNfn-CRycB7NfNTv0mFqvNo").strip()
GOOGLE_WORKSHEET_NAME: str = os.getenv("GOOGLE_WORKSHEET_NAME", "").strip()

# -----------------------------------------------------------------------------
# Access Control / Security
# -----------------------------------------------------------------------------
def get_allowed_telegram_user_ids() -> set[int]:
    """Retrieves allowed Telegram User IDs, reloading from .env if updated."""
    load_dotenv(dotenv_path=BASE_DIR / ".env", override=True)
    raw_ids = os.getenv("ALLOWED_TELEGRAM_USER_IDS", "").strip()
    allowed: set[int] = set()
    if raw_ids:
        for raw_id in raw_ids.split(","):
            cleaned = raw_id.strip()
            if cleaned.isdigit():
                allowed.add(int(cleaned))
    return allowed


ALLOWED_TELEGRAM_USER_IDS: set[int] = get_allowed_telegram_user_ids()


def validate_config() -> dict[str, list[str]]:
    """Validates configuration parameters and returns errors and warnings."""
    errors: list[str] = []
    warnings: list[str] = []

    if not TELEGRAM_BOT_TOKEN or "your_telegram_bot_token" in TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN is missing or set to a placeholder in .env.")

    has_apps_script = bool(GOOGLE_APPS_SCRIPT_URL and "script.google.com" in GOOGLE_APPS_SCRIPT_URL)
    has_credentials_env = bool(GOOGLE_CREDENTIALS_JSON and "client_email" in GOOGLE_CREDENTIALS_JSON)
    has_credentials_file = GOOGLE_SERVICE_ACCOUNT_FILE.exists()

    if not has_apps_script and not has_credentials_env and not has_credentials_file:
        warnings.append(
            "Google Sheets credentials not configured. Set GOOGLE_CREDENTIALS_JSON, "
            "place credentials.json in project root, or set GOOGLE_APPS_SCRIPT_URL."
        )

    if not ALLOWED_TELEGRAM_USER_IDS:
        warnings.append(
            "ALLOWED_TELEGRAM_USER_IDS is empty in .env. "
            "To secure the bot, add your Telegram User ID."
        )

    return {"errors": errors, "warnings": warnings}
