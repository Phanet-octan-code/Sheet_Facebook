# 🤖 Facebook Account Telegram Bot & Google Sheets Auto-Sync

A specialized, high-performance Python Telegram Bot designed to receive Facebook account credentials (`UID`, `Password`, `2FA`, and `Cookie`), automatically parse them, and instantly insert or repair them inside a Google Spreadsheet.

---

## 🌟 Key Features

- **⚡ Instant Auto-Sync:** Simply paste account data into Telegram — the bot automatically parses, formats, and syncs directly to Google Sheets with zero extra button clicks required!
- **🔄 Auto-Repair on Existing UID:** If an account UID is already present in the sheet, the bot **repairs / updates** its password, 2FA, and cookie instead of creating duplicate rows.
- **🔢 Auto-Incrementing `NO.`:** New rows automatically receive the next sequential index in Column A.
- **🛠️ Sheet Repair Command (`/repair`):** One-click command to re-number Column A (`NO.` 1, 2, 3... N) sequentially, apply professional styling, borders, and clip long cookie strings so the sheet remains neat.
- **📋 Multi-Format Support:**
  - Multiline with UID, 2FA, Cookie
  - 4-Line format (UID, Password, 2FA, Cookie)
  - Pipe-delimited (`UID|Password|2FA|Cookie`)
  - Key-Value labeled lines (`UID: ...`, `Pass: ...`, `2FA: ...`, `Cookie: ...`)
  - Cookie extraction (`c_user` auto-detects UID)
  - Bulk account pasting (multiple accounts at once)
- **🔒 Access Control:** Whitelist your Telegram User ID in `.env` so only you and authorized teammates can write to your sheet.
- **🛡️ Formula Injection Defense:** Sandboxes leading `=`, `+`, `@` characters to protect Google Sheets from spreadsheet injection attacks.

---

## 📊 Google Sheets Layout

Matching your spreadsheet layout:

| Row | Col A | Col B | Col C | Col D | Col E |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Row 1** | <center>**Account Facebook (Date)** *(Merged A1:E1, Soft Green Background)*</center> | | | | |
| **Row 2** | **NO.** | **UID** | **Password** | **2FA** | **Cookie** |
| **Row 3+** | 1 | 61593471027967 | Octan953149@! | D4R 7ZLL KU7R 6U7K... | datr=...; c_user=... |

---

## 🚀 Quick Setup Guide

### 1. Configure `.env`
Ensure your `.env` contains your Bot Token, Google Sheet ID, and your Telegram User ID:
```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
GOOGLE_SERVICE_ACCOUNT_FILE=credentials.json
GOOGLE_SHEET_ID=16GqNZYKqV1LCaG2C8B_etNfn-CRycB7NfNTv0mFqvNo
ALLOWED_TELEGRAM_USER_IDS=1407858984
```

### 2. Google Service Account Credentials
1. Go to [Google Cloud Console](https://console.cloud.google.com/) > **APIs & Services** > **Credentials**.
2. Create a Service Account and download its **JSON key**.
3. Rename the file to `credentials.json` and save it inside this folder:
   `c:\Users\phanet\Documents\File code\Python\Sheet Telegram\credentials.json`
4. Open your Google Sheet, click **Share**, and grant **Editor** access to the service account email (found inside `credentials.json`, e.g. `xxx@xxx.iam.gserviceaccount.com`).

### 3. Start the Bot
Run in PowerShell:
```powershell
py bot.py
```

---

## 💬 Usage Examples

### Example 1: UID + 2FA + Cookie (Multiline)
```text
61595168473089

OYNQ RCGJ VEIK 7TA3 SBK5 M53K PK24 RWFU

datr=j-HRamZvPsXLa2M9WxwCWBpG; sb=kOHRavBxIvLHzQPHXuBBEObr; c_user=61595168473089; xs=13%3A...
```
➡️ **Bot Action:** Detects UID `61595168473089`, 2FA, and Cookie. Checks if UID exists in sheet:
- If existing: **Repairs / Updates** row with new 2FA and Cookie.
- If new: **Inserts** new row with next `NO.`.

### Example 2: Pipe-Separated
```text
61593471027967|Octan953149@!|D4R 7ZLL KU7R 6U7K|datr=123; c_user=61593471027967
```

### Example 3: Bulk Accounts
```text
61593471027967|Octan953149@!|D4R 7ZLL|cookie1
61593126492686|Octan953149@!|ZWD 6NBS|cookie2
```
➡️ **Bot Action:** Processes both accounts, auto-numbers, and sends a summary report.

---

## 📌 Bot Commands

- `/start` — Check authorization status and get instructions.
- `/repair` — Auto-repair sheet numbering (`NO.` 1..N) and re-apply column formatting.
- `/list` — Display the latest Facebook accounts stored in the sheet.
- `/help` — View formatting guide and examples.
