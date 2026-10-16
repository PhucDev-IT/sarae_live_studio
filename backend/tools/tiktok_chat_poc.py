"""TikTokLive standalone POC — does not start FastAPI or OBS.

Usage:
  python -m pip install TikTokLive==7.0.1
  python tools/tiktok_chat_poc.py @your_account
"""
import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

from TikTokLive import TikTokLiveClient
from TikTokLive.events import CommentEvent, ConnectEvent, DisconnectEvent, LiveEndEvent


def stamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Read TikTok LIVE comments in terminal")
    parser.add_argument("username", help="TikTok broadcaster username, e.g. @yourshop")
    parser.add_argument("--debug", action="store_true", help="Enable library debug output")
    args = parser.parse_args()
    username = args.username.strip().lstrip("@")
    if not username:
        parser.error("Username is required")
    client = TikTokLiveClient(unique_id="@" + username)
    if args.debug:
        client.logger.setLevel(logging.DEBUG)

    @client.on(ConnectEvent)
    async def on_connect(event: ConnectEvent):
        print(f"[{stamp()}] CONNECTED @{username} room_id={client.room_id}", flush=True)

    @client.on(CommentEvent)
    async def on_comment(event: CommentEvent):
        user = event.user
        handle = getattr(user, "unique_id", None) or getattr(user, "uniqueId", None) or ""
        nickname = getattr(user, "nickname", None) or handle or "unknown"
        print(f"[{stamp()}] CHAT @{handle} ({nickname}): {event.comment}", flush=True)

    @client.on(DisconnectEvent)
    async def on_disconnect(event: DisconnectEvent):
        print(f"[{stamp()}] DISCONNECTED", flush=True)

    @client.on(LiveEndEvent)
    async def on_live_end(event: LiveEndEvent):
        print(f"[{stamp()}] LIVE ENDED", flush=True)

    print(f"[{stamp()}] Connecting to @{username} ... Ctrl+C to stop.", flush=True)
    try:
        await client.connect()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"[{stamp()}] ERROR {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        if client.connected:
            await client.disconnect()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nStopped by operator.")
