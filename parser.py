"""Facebook Account data parser and validator for Telegram bot messages.

Parses single-line, multiline, pipe-delimited, labeled, and bulk Facebook account data.
Extracts:
- UID (Facebook User ID)
- Password (Account Password)
- 2FA (Two-Factor Authentication Secret Key)
- Cookie (Full Session / Authentication Cookie)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class FacebookAccount:
    """Represents a structured Facebook account record ready for Google Sheets."""

    uid: str
    password: str = ""
    two_fa: str = ""
    cookie: str = ""
    no: int | None = None

    def to_sheet_row(self, no_val: int | str = "") -> list[str]:
        """Converts account to the Google Sheets 5-column layout."""
        clean_uid = str(self.uid).strip()
        return [
            str(no_val) if no_val != "" else (str(self.no) if self.no is not None else ""),
            clean_uid,
            self.password.strip(),
            self.two_fa.strip(),
            self.cookie.strip(),
        ]


def is_cookie(text: str) -> bool:
    """Detects if a string is a Facebook cookie string."""
    t = text.strip()
    if re.search(r"\b(c_user|datr|xs|sb|fr|presence|m_pixel_ratio)=[^;]+", t, re.IGNORECASE):
        return True
    if ";" in t and "=" in t and len(t) > 25:
        return True
    return False


def is_uid(text: str) -> bool:
    """Detects if a string is a Facebook numeric UID (typically 8 to 22 digits)."""
    t = text.strip()
    return bool(re.fullmatch(r"\d{8,25}", t))


def is_2fa(text: str) -> bool:
    """Detects if a string is a 2FA TOTP secret key."""
    clean = re.sub(r"[\s\-]+", "", text.strip())
    if 12 <= len(clean) <= 64 and clean.isalnum() and (clean.isupper() or " " in text.strip()):
        return True
    return False


def extract_uid_from_cookie(cookie_str: str) -> str | None:
    """Extracts numeric Facebook UID from c_user cookie parameter."""
    match = re.search(r"\bc_user=(\d+)", cookie_str)
    return match.group(1) if match else None


def parse_single_block(text: str) -> FacebookAccount | None:
    """Parses a single account block (multiline, pipe-separated, or labeled)."""
    text = text.strip()
    if not text:
        return None

    # 1. Pipe-separated format: UID|Password|2FA|Cookie
    if "|" in text:
        parts = [p.strip() for p in text.split("|")]
        if len(parts) >= 2:
            uid = ""
            pwd = ""
            two_fa = ""
            cookie = ""

            if len(parts) >= 4:
                uid = parts[0]
                pwd = parts[1]
                two_fa = parts[2]
                cookie = parts[3]
            elif len(parts) == 3:
                uid = parts[0]
                if is_cookie(parts[2]):
                    cookie = parts[2]
                    if is_2fa(parts[1]):
                        two_fa = parts[1]
                    else:
                        pwd = parts[1]
                elif is_cookie(parts[1]):
                    cookie = parts[1]
                    two_fa = parts[2]
                else:
                    pwd = parts[1]
                    two_fa = parts[2]
            elif len(parts) == 2:
                uid = parts[0]
                if is_cookie(parts[1]):
                    cookie = parts[1]
                elif is_2fa(parts[1]):
                    two_fa = parts[1]
                else:
                    pwd = parts[1]

            if not uid and cookie:
                uid = extract_uid_from_cookie(cookie) or ""

            if uid or cookie:
                return FacebookAccount(uid=uid, password=pwd, two_fa=two_fa, cookie=cookie)

    # 2. Key-Value labeled lines (e.g. UID: ..., Password: ..., 2FA: ..., Cookie: ...)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    labels_found = False
    labeled_fields: dict[str, str] = {}
    for line in lines:
        m = re.match(
            r"^(uid|user\s*id|id|pass|password|pwd|2fa|two_fa|totp|code|cookie|cookies)\s*[:=]\s*(.*)$",
            line,
            re.IGNORECASE,
        )
        if m:
            labels_found = True
            k = m.group(1).lower().replace(" ", "").replace("_", "")
            v = m.group(2).strip()
            if k in ("uid", "userid", "id"):
                labeled_fields["uid"] = v
            elif k in ("pass", "password", "pwd"):
                labeled_fields["password"] = v
            elif k in ("2fa", "twofa", "totp", "code"):
                labeled_fields["two_fa"] = v
            elif k in ("cookie", "cookies"):
                labeled_fields["cookie"] = v

    if labels_found and (labeled_fields.get("uid") or labeled_fields.get("cookie")):
        uid = labeled_fields.get("uid", "")
        cookie = labeled_fields.get("cookie", "")
        if not uid and cookie:
            uid = extract_uid_from_cookie(cookie) or ""
        return FacebookAccount(
            uid=uid,
            password=labeled_fields.get("password", ""),
            two_fa=labeled_fields.get("two_fa", ""),
            cookie=cookie,
        )

    # 3. Unlabeled multiline format (UID, Password/2FA/Cookie on separate lines)
    uid = ""
    pwd = ""
    two_fa = ""
    cookie = ""
    unassigned: list[str] = []

    for line in lines:
        if not cookie and is_cookie(line):
            cookie = line
        elif not uid and is_uid(line):
            uid = line
        elif not two_fa and is_2fa(line):
            two_fa = line
        else:
            unassigned.append(line)

    if not uid and cookie:
        uid = extract_uid_from_cookie(cookie) or ""

    if unassigned and not pwd:
        pwd = unassigned[0]

    if uid or cookie:
        return FacebookAccount(uid=uid, password=pwd, two_fa=two_fa, cookie=cookie)

    return None


def _merge_accounts_by_uid(accounts: list[FacebookAccount]) -> list[FacebookAccount]:
    """Merges duplicate parsed account records with the same UID."""
    merged: dict[str, FacebookAccount] = {}
    for acc in accounts:
        key = acc.uid.strip().lower() if acc.uid else (acc.cookie[:30] if acc.cookie else "unknown")
        if key in merged:
            existing = merged[key]
            if not existing.password and acc.password:
                existing.password = acc.password
            if not existing.two_fa and acc.two_fa:
                existing.two_fa = acc.two_fa
            if not existing.cookie and acc.cookie:
                existing.cookie = acc.cookie
        else:
            merged[key] = acc
    return list(merged.values())


def parse_input_accounts(text: str) -> list[FacebookAccount]:
    """Parses user input text into FacebookAccount objects with smart deduplication."""
    clean_text = text.strip()
    if not clean_text:
        return []

    raw_lines = [l.strip() for l in clean_text.splitlines() if l.strip()]

    # If all lines are pipe-separated accounts
    pipe_lines = [l for l in raw_lines if "|" in l and len(l.split("|")) >= 2]
    if len(pipe_lines) > 1 and len(pipe_lines) == len(raw_lines):
        accounts: list[FacebookAccount] = []
        for pl in pipe_lines:
            acc = parse_single_block(pl)
            if acc:
                accounts.append(acc)
        return _merge_accounts_by_uid(accounts)

    # Count distinct UIDs present in the message
    standalone_uids = [l for l in raw_lines if is_uid(l)]
    cookie_uids = [extract_uid_from_cookie(l) for l in raw_lines if extract_uid_from_cookie(l)]
    distinct_uids = set(standalone_uids + [u for u in cookie_uids if u])

    # If only 1 UID exists across all lines, it is ALWAYS a single account
    if len(distinct_uids) <= 1:
        acc = parse_single_block(clean_text)
        return [acc] if acc else []

    # If multiple distinct UIDs exist, split by multiline blocks
    blocks = re.split(r"\n\s*\n\s*\n+", clean_text)
    accounts = []
    if len(blocks) > 1:
        for b in blocks:
            acc = parse_single_block(b)
            if acc:
                accounts.append(acc)
    else:
        acc = parse_single_block(clean_text)
        if acc:
            accounts.append(acc)

    return _merge_accounts_by_uid(accounts)


def format_account_card(account: FacebookAccount, action: str, row_num: int | str = "", sheet_name: str = "") -> str:
    """Formats a short, clean 1-line Telegram message for an auto-synced account."""
    no_display = f"NO. {account.no}" if account.no is not None else ""
    if action == "REPAIRED":
        return f"🔄 <b>Updated:</b> <code>{account.uid}</code> ({no_display})"
    return f"✅ <b>Inserted:</b> <code>{account.uid}</code> ({no_display})"


def format_bulk_summary(total: int, inserted: int, repaired: int) -> str:
    """Formats a concise bulk summary message."""
    return f"✅ <b>Saved {total} accounts</b> ({inserted} inserted, {repaired} updated)"
