"""
CLI helper to register a stream for monitoring.

Usage:
  python scripts/register_stream.py --channel xqc --platform twitch --preset fps
  python scripts/register_stream.py --channel @MrBeast --platform youtube
"""

import argparse
import asyncio
import json
from redis.asyncio import from_url as redis_from_url

from config.settings import settings


async def register(channel: str, platform: str, preset: str) -> None:
    redis = await redis_from_url(settings.redis_url, decode_responses=True)
    payload = json.dumps({"channel": channel, "platform": platform, "preset": preset})
    await redis.publish("superclipbot:new_streams", payload)
    print(f"Registered: {channel} ({platform}, preset={preset})")
    await redis.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Register a stream for SuperClipBot monitoring")
    parser.add_argument("--channel", required=True)
    parser.add_argument("--platform", default="twitch", choices=["twitch", "youtube"])
    parser.add_argument("--preset", default="default", choices=["default", "chess", "fps", "irl"])
    args = parser.parse_args()

    asyncio.run(register(args.channel, args.platform, args.preset))
