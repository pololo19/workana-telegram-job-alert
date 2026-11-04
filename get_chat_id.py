"""Print Telegram chat IDs from recent bot updates (DM or channel)."""

from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()


def _print_chat(chat: dict) -> None:
    chat_id = chat.get("id")
    if chat_id is None:
        return
    title = chat.get("title") or chat.get("first_name") or chat.get("username") or "chat"
    username = chat.get("username")
    kind = chat.get("type") or "unknown"
    if username and kind == "channel":
        print(f"Channel: @{username}  (numeric id: {chat_id})")
        print(f"  → TELEGRAM_CHAT_ID=@{username}")
    else:
        print(f"Chat ID: {chat_id}  ({title}, type={kind})")
        if kind == "channel":
            print(f"  → TELEGRAM_CHAT_ID={chat_id}")


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Set TELEGRAM_BOT_TOKEN in .env first.", file=sys.stderr)
        raise SystemExit(1)

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        response = httpx.get(url, params={"limit": 100, "timeout": 0}, timeout=20.0)
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
            "No updates yet.\n\n"
            "For a **Telegram channel** (recommended):\n"
            "  1. Create your channel in Telegram.\n"
            "  2. Channel settings → Administrators → Add your bot.\n"
            "     Enable **Post messages** (and Edit messages if you want).\n"
            "  3. Post any message in the channel (or re-add the bot).\n"
            "  4. Run this script again: python get_chat_id.py\n\n"
            "For a private DM instead:\n"
            "  1. Open your bot in Telegram → Start → send /start\n"
            "  2. Run this script again.\n"
        )
        raise SystemExit(1)

    seen: set[int | str] = set()
    for item in updates:
        for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
            message = item.get(key)
            if not message:
                continue
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            dedupe = chat_id if chat_id is not None else chat.get("username")
            if dedupe in seen:
                continue
            seen.add(dedupe)
            _print_chat(chat)

        member = item.get("my_chat_member")
        if member:
            chat = member.get("chat") or {}
            chat_id = chat.get("id")
            if chat_id not in seen:
                seen.add(chat_id)
                print("(Bot was added or permissions changed)")
                _print_chat(chat)

    print(
        "\nCopy TELEGRAM_CHAT_ID into .env.\n"
        "Channels: use @YourChannelUsername (public) or the numeric id (e.g. -100…)."
    )


if __name__ == "__main__":
    main()
