from __future__ import annotations

from contextvars import ContextVar
from types import ModuleType
from typing import Any

from answer_context import GroupAnswerContext, clean_question, format_group_answer


_CURRENT_GROUP_ANSWER: ContextVar[GroupAnswerContext | None] = ContextVar(
    "current_wecom_group_answer",
    default=None,
)


def _trigger_words(module: ModuleType) -> list[str]:
    values: list[str] = []
    for name in ("default_trigger_words", "custom_trigger_words"):
        candidate = getattr(module, name, [])
        if isinstance(candidate, (list, tuple)):
            values.extend(str(item) for item in candidate)
    return values


def install(module: ModuleType) -> None:
    """Patch the reviewed TextMessage module without modifying its PYC."""
    if getattr(module, "_group_answer_context_installed", False):
        return
    original_handle = module.handleTextMessage
    original_send_text = module.send_text_message

    def handle_text_with_context(data: Any, client_id: Any):
        context = None
        if isinstance(data, dict) and bool(data.get("is_room")):
            user_id = str(data.get("sender") or "").strip()
            room_id = str(data.get("room_conversation_id") or "").strip()
            question = clean_question(
                data.get("content"),
                _trigger_words(module),
            )
            if user_id and room_id and question:
                context = GroupAnswerContext(
                    user_id=user_id,
                    room_id=room_id,
                    question=question,
                )
        token = _CURRENT_GROUP_ANSWER.set(context)
        try:
            return original_handle(data, client_id)
        finally:
            _CURRENT_GROUP_ANSWER.reset(token)

    def send_text_with_context(
        text: Any,
        user_id: Any,
        user_name: Any,
        client_id: Any,
        is_room: Any,
        room_id: Any,
    ):
        context = _CURRENT_GROUP_ANSWER.get()
        rendered = text
        if (
            bool(is_room)
            and context is not None
            and context.prefix_pending
            and str(user_id).strip() == context.user_id
            and str(room_id).strip() == context.room_id
        ):
            context.prefix_pending = False
            rendered = format_group_answer(context.question, text)
        return original_send_text(
            rendered,
            user_id,
            user_name,
            client_id,
            is_room,
            room_id,
        )

    module.handleTextMessage = handle_text_with_context
    module.send_text_message = send_text_with_context
    module._group_answer_context_installed = True
