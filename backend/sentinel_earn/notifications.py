import logging
import os
from typing import Dict

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "").strip()
NTFY_URL = os.getenv("NTFY_URL", "https://ntfy.sh").rstrip("/")


def notifications_enabled() -> bool:
    return bool(NTFY_TOPIC)


def send_notification(title: str, message: str, priority: str = "default", tags: str = "robot") -> Dict:
    if not notifications_enabled():
        logger.info("Notification skipped; NTFY_TOPIC not configured: %s", title)
        return {"sent": False, "reason": "NTFY_TOPIC not configured"}

    headers = {
        "Title": title[:120],
        "Priority": priority,
        "Tags": tags,
    }
    try:
        response = httpx.post(
            f"{NTFY_URL}/{NTFY_TOPIC}",
            content=message.encode("utf-8"),
            headers=headers,
            timeout=10,
        )
        return {"sent": response.status_code < 400, "status_code": response.status_code}
    except Exception as exc:
        logger.warning("Notification failed: %s", exc)
        return {"sent": False, "error": str(exc)}
