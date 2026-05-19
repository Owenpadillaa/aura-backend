"""Push notification service.

Wraps pywebpush to send Web Push notifications to all stored subscriptions.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pywebpush import webpush, WebPushException

from config.settings import settings
from services.supabase_service import (
    get_all_push_subscriptions,
    delete_push_subscription,
)

logger = logging.getLogger("aura.push")


async def send_push_to_all(
    title: str,
    body: str,
    tag: str = "aura-push",
) -> dict[str, Any]:
    """Send a push notification to all stored subscriptions.

    Failed subscriptions (410 Gone) are automatically cleaned up.

    Returns:
        Dict with "sent" count and "failed" count.
    """
    if not settings.vapid_private_key:
        logger.warning("VAPID private key not configured, skipping push send.")
        return {"sent": 0, "failed": 0, "error": "not configured"}

    subscriptions = await get_all_push_subscriptions()
    if not subscriptions:
        logger.info("No push subscriptions found.")
        return {"sent": 0, "failed": 0}

    payload = json.dumps({
        "title": title,
        "body": body,
        "tag": tag,
    })

    sent = 0
    failed_endpoints: list[str] = []

    for sub in subscriptions:
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": settings.vapid_email},
            )
            sent += 1
        except WebPushException as e:
            logger.warning(f"Push failed for {sub.get('endpoint', '')[:50]}: {e}")
            failed_endpoints.append(sub.get("endpoint", ""))

    # Clean up failed subscriptions
    for endpoint in failed_endpoints:
        if endpoint:
            await delete_push_subscription(endpoint)

    logger.info(f"Push sent: {sent}, failed: {len(failed_endpoints)}")
    return {"sent": sent, "failed": len(failed_endpoints)}
