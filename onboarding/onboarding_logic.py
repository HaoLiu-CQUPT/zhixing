from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


LOGGER = logging.getLogger("wecom_onboarding")


@dataclass(frozen=True)
class OnboardingConfig:
    enabled: bool
    target_group_name: str
    target_group_id_sha256: str
    message: str
    state_path: Path
    dedupe_retention_seconds: int = 2_592_000

    @classmethod
    def load(cls, path: Path) -> "OnboardingConfig":
        raw = json.loads(path.read_text(encoding="utf-8"))
        config = cls(
            enabled=bool(raw.get("enabled", False)),
            target_group_name=str(raw.get("target_group_name", "")).strip(),
            target_group_id_sha256=str(
                raw.get("target_group_id_sha256", "")
            ).strip().lower(),
            message=str(raw.get("message", "")).strip(),
            state_path=Path(str(raw.get("state_path", ""))),
            dedupe_retention_seconds=int(
                raw.get("dedupe_retention_seconds", 2_592_000)
            ),
        )
        if config.enabled:
            if not config.target_group_name:
                raise ValueError("target_group_name_required")
            if (
                len(config.target_group_id_sha256) != 64
                or any(
                    char not in "0123456789abcdef"
                    for char in config.target_group_id_sha256
                )
            ):
                raise ValueError("target_group_hash_invalid")
            if not config.message:
                raise ValueError("welcome_message_required")
            if not str(config.state_path):
                raise ValueError("state_path_required")
        return config


class OnboardingWelcomeHandler:
    """Send one fixed, allowlisted welcome message for a member-added event.

    Native group/member identifiers are used only in memory. Persistent state
    contains one-way event hashes and timestamps, never raw IDs or names.
    """

    def __init__(
        self,
        config: OnboardingConfig,
        *,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self._now = now
        self._lock = threading.Lock()

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _mentions(value: Any) -> list[str]:
        if isinstance(value, str):
            candidates = [value]
        elif isinstance(value, (list, tuple)):
            candidates = list(value)
        else:
            return []
        result: list[str] = []
        for candidate in candidates:
            text = str(candidate).strip()
            if text and text not in result:
                result.append(text)
        return result

    def _event_hash(
        self,
        *,
        room_id: str,
        mentions: list[str],
        sync_key: str,
        send_time: str,
    ) -> str:
        canonical = json.dumps(
            [room_id, sorted(mentions), sync_key, send_time],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return self._sha256(canonical)

    def _load_sent(self) -> dict[str, int]:
        path = self.config.state_path
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            LOGGER.warning("onboarding_state_unreadable")
            return {}
        sent = raw.get("sent", {}) if isinstance(raw, dict) else {}
        if not isinstance(sent, dict):
            return {}
        now = int(self._now())
        cutoff = now - self.config.dedupe_retention_seconds
        result: dict[str, int] = {}
        for key, timestamp in sent.items():
            if (
                isinstance(key, str)
                and len(key) == 64
                and isinstance(timestamp, int)
                and timestamp >= cutoff
            ):
                result[key] = timestamp
        return result

    def _save_sent(self, sent: dict[str, int]) -> None:
        path = self.config.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        payload = json.dumps(
            {"schema": 1, "sent": sent},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        temporary.write_text(payload + "\n", encoding="utf-8")
        os.replace(temporary, path)

    def handle(
        self,
        data: Any,
        client_id: Any,
        send_message: Callable[[Any, str, str, list[str]], bool],
    ) -> bool:
        if not self.config.enabled or not isinstance(data, dict):
            return False

        room_id = str(data.get("room_conversation_id") or "").strip()
        if not room_id:
            LOGGER.warning("onboarding_event_missing_room")
            return False
        if self._sha256(room_id) != self.config.target_group_id_sha256:
            return False

        # In the reviewed 115003 callback, ``invitee`` is the inviter while
        # ``member_list`` contains the newly added members.  Exclude the
        # inviter defensively even if a malformed event repeats it in both.
        inviter_ids = set(self._mentions(data.get("invitee")))
        mentions = [
            member_id
            for member_id in self._mentions(data.get("member_list"))
            if member_id not in inviter_ids
        ]
        if not mentions:
            LOGGER.warning("onboarding_event_missing_new_member")
            return False

        event_hash = self._event_hash(
            room_id=room_id,
            mentions=mentions,
            sync_key=str(data.get("sync_key") or ""),
            send_time=str(data.get("send_time") or ""),
        )

        with self._lock:
            sent = self._load_sent()
            if event_hash in sent:
                LOGGER.info(
                    "onboarding_duplicate_suppressed event=%s", event_hash[:12]
                )
                return False

            success = bool(
                send_message(
                    client_id,
                    room_id,
                    self.config.message,
                    mentions,
                )
            )
            if not success:
                LOGGER.error("onboarding_welcome_send_failed event=%s", event_hash[:12])
                return False

            sent[event_hash] = int(self._now())
            self._save_sent(sent)
            LOGGER.info(
                "onboarding_welcome_sent event=%s mentions=%d",
                event_hash[:12],
                len(mentions),
            )
            return True
