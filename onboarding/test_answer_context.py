from __future__ import annotations

import threading
import unittest
from types import ModuleType

from answer_context import clean_question, format_group_answer
from patched_text_message import install


class AnswerContextFormattingTests(unittest.TestCase):
    def test_cleans_trigger_and_normalizes_whitespace(self):
        self.assertEqual(
            clean_question("  @AI答疑助手   怎么结算？\n", ["@AI答疑助手"]),
            "怎么结算？",
        )

    def test_truncates_only_the_quoted_question(self):
        question = clean_question("问" * 130, [], maximum=10)
        self.assertEqual(question, "问" * 9 + "…")

    def test_formats_content_for_a_real_at_list_mention(self):
        self.assertEqual(
            format_group_answer("怎么结算？", "按验收结果结算。"),
            " 怎么结算？\n———————————\n按验收结果结算。",
        )

    def test_preserves_answer_evidence_below_the_separator(self):
        answer = (
            "薪资通常在次月中下旬结算。\n"
            "规则/证据依据\n"
            "• 《示例知识库》\n"
            "docs/sources/example.md"
        )
        self.assertEqual(
            format_group_answer("薪资啥时候结算啊", answer),
            " 薪资啥时候结算啊\n"
            "———————————\n"
            f"{answer}",
        )


class TextMessagePatchTests(unittest.TestCase):
    def new_module(self):
        module = ModuleType("fixture_text_message")
        module.default_trigger_words = ["@AI答疑助手"]
        module.custom_trigger_words = []
        module.sent = []

        def send_text_message(text, user_id, user_name, client_id, is_room, room_id):
            module.sent.append(
                (text, user_id, user_name, client_id, is_room, room_id)
            )
            return True

        def handle_text_message(data, client_id):
            return module.send_text_message(
                data["answer"],
                data["sender"],
                data.get("sender_name", ""),
                client_id,
                data["is_room"],
                data["room_conversation_id"],
            )

        module.send_text_message = send_text_message
        module.handleTextMessage = handle_text_message
        install(module)
        return module

    def test_group_reply_keeps_real_recipient_and_adds_question_header(self):
        module = self.new_module()
        module.handleTextMessage(
            {
                "content": "@AI答疑助手 怎么结算？",
                "answer": "按验收结果结算。",
                "sender": "user-a",
                "sender_name": "A",
                "is_room": 1,
                "room_conversation_id": "room-1",
            },
            7,
        )
        self.assertEqual(
            module.sent,
            [
                (
                    " 怎么结算？\n———————————\n按验收结果结算。",
                    "user-a",
                    "A",
                    7,
                    1,
                    "room-1",
                )
            ],
        )

    def test_private_reply_is_unchanged(self):
        module = self.new_module()
        module.handleTextMessage(
            {
                "content": "怎么结算？",
                "answer": "正常回答",
                "sender": "user-a",
                "is_room": 0,
                "room_conversation_id": "user-a",
            },
            7,
        )
        self.assertEqual(module.sent[0][0], "正常回答")

    def test_concurrent_users_do_not_cross_questions(self):
        module = self.new_module()
        barrier = threading.Barrier(2)

        def handle_text_message(data, client_id):
            barrier.wait(timeout=2)
            return module.send_text_message(
                data["answer"],
                data["sender"],
                data["sender_name"],
                client_id,
                data["is_room"],
                data["room_conversation_id"],
            )

        # Reinstall on a fresh module so the wrapper captures this concurrent handler.
        concurrent = ModuleType("fixture_concurrent_text_message")
        concurrent.default_trigger_words = ["@AI答疑助手"]
        concurrent.custom_trigger_words = []
        concurrent.sent = []

        def send(text, user_id, user_name, client_id, is_room, room_id):
            concurrent.sent.append((user_id, text))
            return True

        def concurrent_handle(data, client_id):
            barrier.wait(timeout=2)
            return concurrent.send_text_message(
                data["answer"],
                data["sender"],
                data["sender_name"],
                client_id,
                data["is_room"],
                data["room_conversation_id"],
            )

        concurrent.send_text_message = send
        concurrent.handleTextMessage = concurrent_handle
        install(concurrent)

        fixtures = (
            {
                "content": "@AI答疑助手 问题A？",
                "answer": "回答A",
                "sender": "user-a",
                "sender_name": "A",
                "is_room": 1,
                "room_conversation_id": "room-1",
            },
            {
                "content": "@AI答疑助手 问题B？",
                "answer": "回答B",
                "sender": "user-b",
                "sender_name": "B",
                "is_room": 1,
                "room_conversation_id": "room-1",
            },
        )
        threads = [
            threading.Thread(target=concurrent.handleTextMessage, args=(item, 7))
            for item in fixtures
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        rendered = dict(concurrent.sent)
        self.assertIn("问题A？", rendered["user-a"])
        self.assertIn("回答A", rendered["user-a"])
        self.assertNotIn("问题B？", rendered["user-a"])
        self.assertIn("问题B？", rendered["user-b"])
        self.assertIn("回答B", rendered["user-b"])
        self.assertNotIn("问题A？", rendered["user-b"])


if __name__ == "__main__":
    unittest.main()
