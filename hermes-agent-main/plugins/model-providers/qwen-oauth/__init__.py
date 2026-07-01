"""Qwen Portal provider profile."""

import copy
from typing import Any

from providers import register_provider
from providers.base import ProviderProfile


class QwenProfile(ProviderProfile):
    """Qwen Portal — message normalization, vl_high_resolution, metadata top-level."""

    def prepare_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize content to list-of-dicts format.

        Inject cache_control on system message.

        Matches the behavior of run_agent.py:_qwen_prepare_chat_messages().
        """
        if not messages:
            return list(messages)

        # Shallow-copy the list; only rebuild the individual message dicts we
        # actually normalize (and deep-copy the parts we mutate) so long
        # histories aren't deep-copied and re-walked wholesale each turn.
        prepared: list[dict[str, Any]] = list(messages)

        for idx, msg in enumerate(prepared):
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if isinstance(content, str):
                new_msg = dict(msg)
                new_msg["content"] = [{"type": "text", "text": content}]
                prepared[idx] = new_msg
            elif isinstance(content, list):
                normalized_parts = []
                for part in content:
                    if isinstance(part, str):
                        normalized_parts.append({"type": "text", "text": part})
                    elif isinstance(part, dict):
                        normalized_parts.append(part)
                if normalized_parts:
                    new_msg = dict(msg)
                    new_msg["content"] = normalized_parts
                    prepared[idx] = new_msg

        # Inject cache_control on the last part of the system message.
        for idx, msg in enumerate(prepared):
            if isinstance(msg, dict) and msg.get("role") == "system":
                content = msg.get("content")
                if (
                    isinstance(content, list)
                    and content
                    and isinstance(content[-1], dict)
                ):
                    # Deep-copy only the part we mutate so the caller's dict is
                    # never modified in place.
                    new_content = list(content)
                    new_last = copy.deepcopy(new_content[-1])
                    new_last["cache_control"] = {"type": "ephemeral"}
                    new_content[-1] = new_last
                    new_msg = dict(msg)
                    new_msg["content"] = new_content
                    prepared[idx] = new_msg
                break

        return prepared

    def build_extra_body(
        self, *, session_id: str | None = None, **context
    ) -> dict[str, Any]:
        return {"vl_high_resolution_images": True}

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict | None = None,
        qwen_session_metadata: dict | None = None,
        **context,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Qwen metadata goes to top-level api_kwargs, not extra_body."""
        top_level = {}
        if qwen_session_metadata:
            top_level["metadata"] = qwen_session_metadata
        return {}, top_level


qwen = QwenProfile(
    name="qwen-oauth",
    aliases=("qwen", "qwen-portal", "qwen-cli"),
    env_vars=("QWEN_API_KEY",),
    base_url="https://portal.qwen.ai/v1",
    auth_type="oauth_external",
    default_max_tokens=65536,
)

register_provider(qwen)
