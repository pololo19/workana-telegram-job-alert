"""Print your Telegram chat ID after you send /start to your bot."""

from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Set TELEGRAM_BOT_TOKEN in .env first.", file=sys.stderr)
        raise SystemExit(1)

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        response = httpx.get(url, params={"limit": 10, "timeout": 0}, timeout=20.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"Could not reach Telegram API: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    data = response.json()
    if not data.get("ok"):
        print(f"Telegram API error: {data}", file=sys.stderr)
        raise SystemExit(1)

    updates = data.get("result") or []
    if not updates:
        print(
            "No messages yet.\n"
            "1. Open https://t.me/my_workana_alert_bot\n"
            "2. Tap Start and send /start\n"
            "3. Run this script again: python get_chat_id.py"
        )
        raise SystemExit(1)

    seen: set[int] = set()
    for item in updates:
        message = item.get("message") or item.get("edited_message")
        if not message:
            continue
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None or chat_id in seen:
            continue
        seen.add(chat_id)
        name = chat.get("first_name") or chat.get("username") or "you"
        print(f"Chat ID: {chat_id}  ({name})")
    print("\nCopy the Chat ID into TELEGRAM_CHAT_ID in your .env file.")


if __name__ == "__main__":
    main()
