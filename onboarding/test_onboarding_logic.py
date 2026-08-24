from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from onboarding_logic import OnboardingConfig, OnboardingWelcomeHandler



class OnboardingWelcomeHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.room_id = "fixture-room"
        self.message = "fixture welcome\nhttps://example.invalid/rule"
        self.config = OnboardingConfig(
            enabled=True,
            target_group_name="示例测试群",
            target_group_id_sha256=hashlib.sha256(
                self.room_id.encode("utf-8")
            ).hexdigest(),
            message=self.message,
            state_path=self.root / "state.json",
        )
        self.handler = OnboardingWelcomeHandler(self.config, now=lambda: 1000.0)
        self.calls: list[tuple[object, str, str, list[str]]] = []

    def send(self, client_id, room_id, message, mentions):
        self.calls.append((client_id, room_id, message, mentions))
        return True

    def event(self, **changes):
        value = {
            "room_conversation_id": self.room_id,
            "invitee": "fixture-inviter",
            "member_list": ["fixture-member"],
            "sync_key": "fixture-event",
            "send_time": "1000",
        }
        value.update(changes)
        return value

    def test_sends_exact_message_and_real_mention(self):
        self.assertTrue(self.handler.handle(self.event(), 7, self.send))
        self.assertEqual(
            self.calls,
            [(7, self.room_id, self.message, ["fixture-member"])],
        )
        state = json.loads(self.config.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["schema"], 1)
        self.assertEqual(len(state["sent"]), 1)
        self.assertNotIn(self.room_id, self.config.state_path.read_text("utf-8"))
        self.assertNotIn("fixture-member", self.config.state_path.read_text("utf-8"))
        self.assertNotIn("fixture-inviter", self.config.state_path.read_text("utf-8"))

    def test_excludes_inviter_from_malformed_member_list(self):
        self.assertTrue(
            self.handler.handle(
                self.event(
                    member_list=["fixture-inviter", "fixture-member"]
                ),
                7,
                self.send,
            )
        )
        self.assertEqual(
            self.calls,
            [(7, self.room_id, self.message, ["fixture-member"])],
        )

    def test_mentions_all_new_members_but_not_inviter(self):
        self.assertTrue(
            self.handler.handle(
                self.event(member_list=["fixture-member", "fixture-member-2"]),
                7,
                self.send,
            )
        )
        self.assertEqual(
            self.calls,
            [
                (
                    7,
                    self.room_id,
                    self.message,
                    ["fixture-member", "fixture-member-2"],
                )
            ],
        )

    def test_ignores_non_allowlisted_group(self):
        self.assertFalse(
            self.handler.handle(
                self.event(room_conversation_id="another-room"), 7, self.send
            )
        )
        self.assertEqual(self.calls, [])

    def test_suppresses_retry(self):
        self.assertTrue(self.handler.handle(self.event(), 7, self.send))
        self.assertFalse(self.handler.handle(self.event(), 7, self.send))
        self.assertEqual(len(self.calls), 1)

    def test_fails_closed_without_new_member_id(self):
        self.assertFalse(
            self.handler.handle(self.event(member_list=[]), 7, self.send)
        )
        self.assertEqual(self.calls, [])

    def test_fails_closed_when_only_inviter_is_listed(self):
        self.assertFalse(
            self.handler.handle(
                self.event(member_list=["fixture-inviter"]), 7, self.send
            )
        )
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
