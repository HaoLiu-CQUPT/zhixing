from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "adapter" / "dify_proxy.py"
SPEC = importlib.util.spec_from_file_location("dify_proxy", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
dify_proxy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dify_proxy)


RAW_ANSWER = (
    "<think>\n<!--dify-deepseek-reasoning-->内部推理，不应发送给用户。\n</think>"
    "根据文档摘录，测试团队的标准工作时间为工作日 **9:30至18:30**，"
    "午休时间为 **12:00至13:30**。"
)
EXPECTED_ANSWER = (
    "测试团队的标准工作时间为工作日 9:30 至 18:30，"
    "午休时间为 12:00 至 13:30。"
)


def sse(payload: dict[str, object]) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n\n"


class AdapterSanitizerTests(unittest.TestCase):
    def test_humanize_answer(self) -> None:
        self.assertEqual(dify_proxy.humanize_answer(RAW_ANSWER), EXPECTED_ANSWER)

    def test_streaming_fragments_become_one_plain_message(self) -> None:
        fragments = [
            "<thi",
            "nk>\n<!--dify-deepseek-reasoning-->",
            "内部推理，不应发送给用户。\n",
            "</think>根据文档摘录，测试团队的标准工作时间为工作日 **9:30",
            "至18:30**，午休时间为 **12:00至13:30**。",
        ]
        body = "".join(
            sse({"event": "message", "message_id": "m1", "answer": fragment})
            for fragment in fragments
        )
        body += sse(
            {
                "event": "node_finished",
                "data": {"outputs": {"text": RAW_ANSWER, "reasoning_content": "hidden"}},
            }
        )
        body += sse({"event": "message_end", "message_id": "m1"})

        cleaned, changed, answer_chars = dify_proxy.sanitize_sse_body(body.encode("utf-8"))
        payloads = [
            dify_proxy._parse_sse_payload(block)
            for block in cleaned.decode("utf-8").split("\n\n")
            if block
        ]
        messages = [payload for payload in payloads if payload and payload.get("event") == "message"]
        node = next(
            payload for payload in payloads if payload and payload.get("event") == "node_finished"
        )

        self.assertTrue(changed)
        self.assertEqual(answer_chars, len(EXPECTED_ANSWER))
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["answer"], EXPECTED_ANSWER)
        self.assertEqual(node["data"]["outputs"]["text"], EXPECTED_ANSWER)
        self.assertEqual(node["data"]["outputs"]["reasoning_content"], "")
        self.assertNotIn("<think>", cleaned.decode("utf-8"))
        self.assertNotIn("**", cleaned.decode("utf-8"))

    def test_blocking_json_answer_is_plain_text(self) -> None:
        body = json.dumps({"event": "message", "answer": RAW_ANSWER}).encode("utf-8")
        cleaned, changed, answer_chars = dify_proxy.sanitize_upstream_body(
            "application/json", body
        )
        payload = json.loads(cleaned)

        self.assertTrue(changed)
        self.assertEqual(payload["answer"], EXPECTED_ANSWER)
        self.assertEqual(answer_chars, len(EXPECTED_ANSWER))


if __name__ == "__main__":
    unittest.main()
