from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from handlers.send.sendMessage import sendGroupMessage
from onboarding_logic import OnboardingConfig, OnboardingWelcomeHandler


LOGGER = logging.getLogger("wecom_onboarding")
_HANDLER: OnboardingWelcomeHandler | None = None


def _handler() -> OnboardingWelcomeHandler:
    global _HANDLER
    if _HANDLER is None:
        config_path = os.environ.get("YUCE_WECOM_ONBOARDING_CONFIG", "").strip()
        if not config_path:
            raise RuntimeError("onboarding_config_missing")
        _HANDLER = OnboardingWelcomeHandler(
            OnboardingConfig.load(Path(config_path))
        )
    return _HANDLER


def process_group_member_added_notification(data: Any, client_id: Any) -> None:
    """Handle the kernel's native GROUP_MEMBER_ADDED event without an LLM call."""
    try:
        _handler().handle(data, client_id, sendGroupMessage)
    except Exception as exc:
        # The callback must not crash the kernel. Do not log event payloads or IDs.
        LOGGER.error("onboarding_handler_failed error=%s", type(exc).__name__)
