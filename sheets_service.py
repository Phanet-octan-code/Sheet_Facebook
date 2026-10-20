"""Google Sheets integration service for Facebook Account Organizer.

Supports two connection backends:
1. Google Apps Script Web App (EASIEST: No credentials.json needed, zero Google Cloud setup).
2. Google Cloud Service Account (credentials.json via gspread).

Features:
- Auto-inserting new Facebook accounts with sequential NO.
- Auto-repairing/updating existing accounts matching by UID.
- Full sheet repair (renumbering Column A 1..N and styling).
- Recent accounts viewer.
- Automatic retries with exponential backoff.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

# pyrefly: ignore[missing-import]
import httpx

import config
from parser import FacebookAccount
from security import audit_log, sanitize_for_sheets

logger = logging.getLogger("telegram_sheets_bot.sheets")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [Sheets] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

# Standard Sheet Columns
SHEET_HEADERS = ["NO.", "UID", "Password", "2FA", "Cookie"]

# Column Widths in Pixels
COLUMN_WIDTHS = {
    1: 70,   # NO.
    2: 170,  # UID
    3: 160,  # Password
    4: 260,  # 2FA
    5: 380,  # Cookie
}


class SheetsService:
    """Encapsulates all Google Sheets API & Apps Script interactions for Facebook accounts."""

    def __init__(self) -> None:
        self.client: Any = None
        self.spreadsheet: Any = None
        self.worksheet: Any = None
        self.header_row_index: int = 2
        self.data_start_row: int = 3

    @property
    def is_apps_script_mode(self) -> bool:
        """Returns True if Google Apps Script Web App URL is configured."""
        return bool(config.GOOGLE_APPS_SCRIPT_URL and "script.google.com" in config.GOOGLE_APPS_SCRIPT_URL)

    # =========================================================================
    # BACKEND 1: Google Apps Script Web App (Fastest, zero credentials setup)
    # =========================================================================
    def _apps_script_request(self, payload: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        """Sends a JSON POST request to the Google Apps Script Web App."""
        url = config.GOOGLE_APPS_SCRIPT_URL
        if not url:
            raise FileNotFoundError("GOOGLE_APPS_SCRIPT_URL is not set.")

        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") == "error":
                    raise RuntimeError(data.get("message", "Unknown Apps Script error."))
                return data
        except Exception as exc:
            logger.error("Apps Script request error: %s", exc)
            raise exc

    def _insert_or_repair_via_apps_script(self, account: FacebookAccount) -> dict[str, Any]:
        """Inserts or repairs account using Google Apps Script Web App."""
        payload = {
            "action": "insert_or_repair",
            "uid": account.uid,
            "password": account.password,
            "two_fa": account.two_fa,
            "cookie": account.cookie,
        }
        res = self._apps_script_request(payload)
        acc_data = res.get("account", {})
        account.password = acc_data.get("password", account.password)
        account.two_fa = acc_data.get("two_fa", account.two_fa)
        account.cookie = acc_data.get("cookie", account.cookie)
        if str(acc_data.get("no", "")).isdigit():
            account.no = int(acc_data["no"])

        audit_log(f"APPS_SCRIPT_{res.get('action')}", 0, f"UID: {account.uid}, Row: {res.get('row')}")

        return {
            "action": res.get("action", "INSERTED"),
            "row": res.get("row", "-"),
            "no": str(account.no if account.no is not None else res.get("no", "-")),
            "account": account,
            "sheet_name": res.get("sheet_name", "Google Sheet"),
        }

    def _repair_sheet_via_apps_script(self) -> dict[str, Any]:
        """Repairs sheet numbering and formatting via Google Apps Script."""
        payload = {"action": "repair_sheet"}
        res = self._apps_script_request(payload)
        return {
            "total_rows": res.get("total_rows", 0),
            "renumbered_count": res.get("renumbered_count", 0),
            "sheet_name": res.get("sheet_name", "Google Sheet"),
        }

    def _get_recent_via_apps_script(self, limit: int = 5) -> list[dict[str, str]]:
        """Fetches recent accounts via Google Apps Script."""
        payload = {"action": "get_recent", "limit": limit}
        res = self._apps_script_request(payload)
        return res.get("accounts", [])

    # =========================================================================
    # BACKEND 2: Google Service Account (credentials.json via gspread)
    # =========================================================================
    def _authenticate_gspread(self) -> Any:
        """Authenticates with Google Sheets API using Service Account Credentials."""
        import json
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]

        # 1. Check direct JSON string in environment variable (Ideal for cloud hosting)
        if config.GOOGLE_CREDENTIALS_JSON:
            try:
                raw_json = config.GOOGLE_CREDENTIALS_JSON.strip()
                # Strip outer single or double quotes if added by shell or UI
                if (raw_json.startswith("'") and raw_json.endswith("'")) or (
                    raw_json.startswith('"') and raw_json.endswith('"')
                ):
                    raw_json = raw_json[1:-1].strip()

                try:
                    info = json.loads(raw_json)
                except Exception:
                    # Attempt base64 decoding if base64 encoded
                    import base64
                    decoded = base64.b64decode(raw_json).decode("utf-8")
                    info = json.loads(decoded)

                creds = Credentials.from_service_account_info(info, scopes=scopes)
                return gspread.authorize(creds)
            except Exception as e:
                logger.error("Failed to parse GOOGLE_CREDENTIALS_JSON env var: %s", e)

        # 2. Check credentials.json file on disk
        if not config.GOOGLE_SERVICE_ACCOUNT_FILE.exists():
            raise FileNotFoundError(
                f"Google service account file '{config.GOOGLE_SERVICE_ACCOUNT_FILE.name}' was not found. "
                "Please place your credentials.json in the project root or set GOOGLE_CREDENTIALS_JSON."
            )

        creds = Credentials.from_service_account_file(
            str(config.GOOGLE_SERVICE_ACCOUNT_FILE),
            scopes=scopes,
        )
        return gspread.authorize(creds)

    def connect(self, force_refresh: bool = False) -> Any:
        """Connects to Google Spreadsheet and ensures the worksheet is properly initialized."""
        if self.worksheet is not None and not force_refresh:
            return self.worksheet

        self.client = self._authenticate_gspread()

        # Open Spreadsheet by ID or Name
        if config.GOOGLE_SHEET_ID:
            try:
                self.spreadsheet = self.client.open_by_key(config.GOOGLE_SHEET_ID)
            except Exception as e:
                err_str = str(e) or str(getattr(e, "__cause__", ""))
                if "has not been used in project" in err_str or "disabled" in err_str:
                    raise RuntimeError(
                        "Google Sheets API is not enabled in your Google Cloud project!\n"
                        "Click here to enable it: https://console.developers.google.com/apis/api/sheets.googleapis.com/overview"
                    ) from e
                raise ValueError(
                    f"Spreadsheet with ID '{config.GOOGLE_SHEET_ID}' could not be opened. "
                    "Make sure it is shared with your service account email as Editor."
                ) from e
        else:
            try:
                self.spreadsheet = self.client.open(config.GOOGLE_SHEET_NAME)
            except Exception as e:
                err_str = str(e) or str(getattr(e, "__cause__", ""))
                if "has not been used in project" in err_str or "disabled" in err_str:
                    raise RuntimeError(
                        "Google Sheets API is not enabled in your Google Cloud project!\n"
                        "Click here to enable it: https://console.developers.google.com/apis/api/sheets.googleapis.com/overview"
                    ) from e
                raise ValueError(
                    f"Spreadsheet named '{config.GOOGLE_SHEET_NAME}' was not found. "
                    "Please verify the sheet name or use GOOGLE_SHEET_ID."
                ) from e

        # Open Worksheet: First check target worksheet name, then fallback to first sheet
        target_ws_name = config.GOOGLE_WORKSHEET_NAME
        try:
            if target_ws_name:
                self.worksheet = self.spreadsheet.worksheet(target_ws_name)
            else:
                self.worksheet = self.spreadsheet.get_worksheet(0)
        except Exception:
            try:
                self.worksheet = self.spreadsheet.get_worksheet(0)
            except Exception:
                self.worksheet = self.spreadsheet.add_worksheet(
                    title=target_ws_name or "Sheet1", rows=500, cols=5
                )

        if self.worksheet is None:
            self.worksheet = self.spreadsheet.get_worksheet(0)

        # Detect headers and layout
        self._detect_and_initialize_layout()

        return self.worksheet

    def _detect_and_initialize_layout(self) -> None:
        """Detects whether headers are on row 1 or row 2, and initializes if empty."""
        if not self.worksheet:
            return

        try:
            all_values = self.worksheet.get_all_values()
        except Exception:
            all_values = []

        row1 = [c.strip().upper() for c in all_values[0]] if len(all_values) > 0 else []
        row2 = [c.strip().upper() for c in all_values[1]] if len(all_values) > 1 else []

        if any("UID" in col for col in row2):
            self.header_row_index = 2
            self.data_start_row = 3
            return

        if any("UID" in col for col in row1):
            self.header_row_index = 1
            self.data_start_row = 2
            return

        # Setup layout matching user's screenshot
        logger.info("Initializing Facebook Account headers on worksheet '%s'...", self.worksheet.title)
        today_str = datetime.now().strftime("%d/%m/%Y")
        title_text = f"Account Facebook {today_str}"

        self.worksheet.update(values=[[title_text]], range_name="A1")
        self.worksheet.update(values=[SHEET_HEADERS], range_name="A2:E2")

        self.header_row_index = 2
        self.data_start_row = 3
        self._apply_full_styling()

    def _apply_full_styling(self) -> None:
        """Applies professional styling matching the user's screenshot."""
        if not self.spreadsheet or not self.worksheet:
            return

        sheet_id = self.worksheet.id

        try:
            self.worksheet.merge_cells("A1:E1", merge_type="MERGE_ALL")
            self.worksheet.format(
                "A1:E1",
                {
                    "backgroundColor": {"red": 0.58, "green": 0.77, "blue": 0.49},
                    "textFormat": {
                        "foregroundColor": {"red": 0.1, "green": 0.1, "blue": 0.1},
                        "bold": True,
                        "fontSize": 12,
                    },
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                },
            )

            self.worksheet.format(
                "A2:E2",
                {
                    "backgroundColor": {"red": 0.62, "green": 0.77, "blue": 0.91},
                    "textFormat": {
                        "foregroundColor": {"red": 0.0, "green": 0.0, "blue": 0.0},
                        "bold": True,
                        "fontSize": 11,
                    },
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                },
            )

            self.worksheet.freeze(rows=2)

            requests: list[dict[str, Any]] = [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 2,
                            "startColumnIndex": 4,
                            "endColumnIndex": 5,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "wrapStrategy": "CLIP",
                            }
                        },
                        "fields": "userEnteredFormat.wrapStrategy",
                    }
                },
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 2,
                            "startColumnIndex": 1,
                            "endColumnIndex": 2,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "numberFormat": {"type": "TEXT"},
                                "horizontalAlignment": "CENTER",
                            }
                        },
                        "fields": "userEnteredFormat.numberFormat,userEnteredFormat.horizontalAlignment",
                    }
                },
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 2,
                            "startColumnIndex": 0,
                            "endColumnIndex": 1,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "horizontalAlignment": "CENTER",
                            }
                        },
                        "fields": "userEnteredFormat.horizontalAlignment",
                    }
                },
            ]

            for col_idx, width in COLUMN_WIDTHS.items():
                requests.append(
                    {
                        "updateDimensionProperties": {
                            "range": {
                                "sheetId": sheet_id,
                                "dimension": "COLUMNS",
                                "startIndex": col_idx - 1,
                                "endIndex": col_idx,
                            },
                            "properties": {"pixelSize": width},
                            "fields": "pixelSize",
                        }
                    }
                )

            self.spreadsheet.batch_update({"requests": requests})
        except Exception as e:
            logger.warning("Formatting warning (best-effort): %s", e)

    def _insert_or_repair_via_gspread(
        self, account: FacebookAccount, max_retries: int = 3
    ) -> dict[str, Any]:
        """Inserts or repairs account using gspread."""
        for attempt in range(1, max_retries + 1):
            try:
                ws = self.connect()
                all_values = ws.get_all_values()

                data_start_idx = self.data_start_row - 1
                data_rows = all_values[data_start_idx:] if len(all_values) > data_start_idx else []

                target_uid = str(account.uid).strip().lower()

                found_row_idx: int | None = None
                old_no = ""
                old_pwd = ""
                old_2fa = ""
                old_cookie = ""

                for idx, row in enumerate(data_rows):
                    row_uid = row[1].strip().lower() if len(row) > 1 else ""
                    if target_uid and row_uid == target_uid:
                        found_row_idx = data_start_idx + idx + 1
                        old_no = row[0].strip() if len(row) > 0 else ""
                        old_pwd = row[2].strip() if len(row) > 2 else ""
                        old_2fa = row[3].strip() if len(row) > 3 else ""
                        old_cookie = row[4].strip() if len(row) > 4 else ""
                        break

                # UID FOUND -> REPAIR EXISTING ROW
                if found_row_idx is not None:
                    new_pwd = account.password.strip() if account.password.strip() else old_pwd
                    new_2fa = account.two_fa.strip() if account.two_fa.strip() else old_2fa
                    new_cookie = account.cookie.strip() if account.cookie.strip() else old_cookie

                    update_values = [
                        [
                            sanitize_for_sheets(new_pwd),
                            sanitize_for_sheets(new_2fa),
                            sanitize_for_sheets(new_cookie),
                        ]
                    ]

                    ws.update(
                        values=update_values,
                        range_name=f"C{found_row_idx}:E{found_row_idx}",
                        value_input_option="USER_ENTERED",
                    )

                    account.no = int(old_no) if old_no.isdigit() else None
                    account.password = new_pwd
                    account.two_fa = new_2fa
                    account.cookie = new_cookie

                    audit_log("ACCOUNT_REPAIRED", 0, f"UID: {account.uid}, Row: {found_row_idx}")

                    return {
                        "action": "REPAIRED",
                        "row": found_row_idx,
                        "no": old_no or str(account.no or "-"),
                        "account": account,
                        "sheet_name": ws.title,
                    }

                # UID NOT FOUND -> INSERT NEW ROW
                max_no = 0
                for row in data_rows:
                    if row and row[0].strip().isdigit():
                        num = int(row[0].strip())
                        if num > max_no:
                            max_no = num

                new_no = max_no + 1 if max_no > 0 else (len(data_rows) + 1)
                account.no = new_no

                new_row = [
                    str(new_no),
                    "'" + str(account.uid).strip(),
                    sanitize_for_sheets(account.password),
                    sanitize_for_sheets(account.two_fa),
                    sanitize_for_sheets(account.cookie),
                ]

                ws.append_row(new_row, value_input_option="USER_ENTERED")
                new_row_idx = len(all_values) + 1

                audit_log("ACCOUNT_INSERTED", 0, f"UID: {account.uid}, NO: {new_no}, Row: {new_row_idx}")

                return {
                    "action": "INSERTED",
                    "row": new_row_idx,
                    "no": str(new_no),
                    "account": account,
                    "sheet_name": ws.title,
                }

            except Exception as exc:
                wait_time = 2**attempt
                logger.warning("Sheets API error (attempt %s/%s): %s. Retrying...", attempt, max_retries, exc)
                if attempt == max_retries:
                    raise exc
                time.sleep(wait_time)

        raise RuntimeError("Failed to insert or repair account.")

    def _repair_sheet_via_gspread(self) -> dict[str, Any]:
        """Repairs numbering and layout using gspread."""
        ws = self.connect(force_refresh=True)
        all_values = ws.get_all_values()

        data_start_idx = self.data_start_row - 1
        if len(all_values) <= data_start_idx:
            self._apply_full_styling()
            return {"total_rows": 0, "renumbered_count": 0, "sheet_name": ws.title}

        data_rows = all_values[data_start_idx:]
        renumbered_count = 0
        updates: list[list[str]] = []

        for idx, row in enumerate(data_rows, start=1):
            expected_no = str(idx)
            current_no = row[0].strip() if len(row) > 0 else ""
            if current_no != expected_no:
                renumbered_count += 1
            updates.append([expected_no])

        if renumbered_count > 0:
            end_row = data_start_idx + len(updates)
            ws.update(
                values=updates,
                range_name=f"A{self.data_start_row}:A{end_row}",
                value_input_option="USER_ENTERED",
            )

        self._apply_full_styling()

        return {
            "total_rows": len(data_rows),
            "renumbered_count": renumbered_count,
            "sheet_name": ws.title,
        }

    def _get_recent_via_gspread(self, limit: int = 5) -> list[dict[str, str]]:
        """Retrieves recent accounts using gspread."""
        ws = self.connect()
        all_rows = ws.get_all_values()

        data_start_idx = self.data_start_row - 1
        if len(all_rows) <= data_start_idx:
            return []

        data_rows = all_rows[data_start_idx:]
        recent_rows = data_rows[-limit:]
        recent_rows.reverse()

        results: list[dict[str, str]] = []
        for r in recent_rows:
            results.append(
                {
                    "no": r[0] if len(r) > 0 else "-",
                    "uid": r[1] if len(r) > 1 else "-",
                    "password": r[2] if len(r) > 2 else "-",
                    "two_fa": r[3] if len(r) > 3 else "-",
                    "cookie": r[4] if len(r) > 4 else "-",
                }
            )
        return results

    # =========================================================================
    # PUBLIC UNIFIED INTERFACE (Auto-switches to active backend)
    # =========================================================================
    def insert_or_repair_account(
        self, account: FacebookAccount, max_retries: int = 3
    ) -> dict[str, Any]:
        """Inserts or repairs account using the active backend (Apps Script or gspread)."""
        if self.is_apps_script_mode:
            return self._insert_or_repair_via_apps_script(account)
        return self._insert_or_repair_via_gspread(account, max_retries=max_retries)

    def repair_sheet(self) -> dict[str, Any]:
        """Repairs sheet numbering and formatting using the active backend."""
        if self.is_apps_script_mode:
            return self._repair_sheet_via_apps_script()
        return self._repair_sheet_via_gspread()

    def get_recent_accounts(self, limit: int = 5) -> list[dict[str, str]]:
        """Retrieves recent accounts using the active backend."""
        if self.is_apps_script_mode:
            return self._get_recent_via_apps_script(limit=limit)
        return self._get_recent_via_gspread(limit=limit)


# Global singleton instance
sheets_service = SheetsService()
