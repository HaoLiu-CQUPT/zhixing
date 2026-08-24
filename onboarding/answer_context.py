from __future__ import annotations

import re
from dataclasses import dataclass


MAX_QUOTED_QUESTION_CHARS = 120
ANSWER_SEPARATOR = "———————————"


@dataclass
class GroupAnswerContext:
    user_id: str
    room_id: str
    question: str
    prefix_pending: bool = True


def clean_question(
    value: object,
    trigger_words: list[str] | tuple[str, ...],
    *,
    maximum: int = MAX_QUOTED_QUESTION_CHARS,
) -> str:
    """Return one compact, trigger-free question suitable for a reply header."""
    if maximum < 2:
        raise ValueError("question_maximum_too_small")
    text = str(value or "")
    for trigger in sorted(
        {str(item).strip() for item in trigger_words if str(item).strip()},
        key=len,
        reverse=True,
    ):
        text = text.replace(trigger, " ")
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n,，:：;；")
    if len(text) > maximum:
        text = text[: maximum - 1].rstrip() + "…"
    return text


def format_group_answer(question: str, answer: object) -> str:
    body = str(answer or "").strip()
    if not question or not body:
        return body
    # The real mention is carried separately in sendGroupMessage's at_list.
    # A leading space keeps the rendered first line readable as
    # "@name question" without inserting a fake textual @name.
    return f" {question}\n{ANSWER_SEPARATOR}\n{body}"
