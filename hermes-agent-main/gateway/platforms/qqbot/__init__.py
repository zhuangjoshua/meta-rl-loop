"""
QQBot platform package.

Re-exports the main adapter symbols from ``adapter.py`` (the original
``qqbot.py``) so that **all existing import paths remain unchanged**::

    from gateway.platforms.qqbot import QQAdapter          # works
    from gateway.platforms.qqbot import check_qq_requirements  # works

New modules:
    - ``constants`` — shared constants (API URLs, timeouts, message types)
    - ``utils`` — User-Agent builder, config helpers
    - ``crypto`` — AES-256-GCM key generation and decryption
    - ``onboard`` — QR-code scan-to-configure flow
"""

import importlib
from typing import TYPE_CHECKING

# Map each public attribute to the submodule that owns it.  Attribute access
# on this package lazily imports the owning submodule (PEP 562 __getattr__),
# so processes that only need constants/utils don't pay the cost of loading
# websocket, HTTP, cryptography, chunked-upload, and keyboard machinery.
_LAZY_ATTRS = {
    # adapter (original qqbot.py)
    "QQAdapter": ".adapter",
    "QQCloseError": ".adapter",
    "check_qq_requirements": ".adapter",
    "_coerce_list": ".adapter",
    "_ssrf_redirect_guard": ".adapter",
    # onboard (QR-code scan-to-configure)
    "BindStatus": ".onboard",
    "build_connect_url": ".onboard",
    "qr_register": ".onboard",
    # crypto
    "decrypt_secret": ".crypto",
    "generate_bind_key": ".crypto",
    # utils
    "build_user_agent": ".utils",
    "get_api_headers": ".utils",
    "coerce_list": ".utils",
    # chunked upload
    "ChunkedUploader": ".chunked_upload",
    "UploadDailyLimitExceededError": ".chunked_upload",
    "UploadFileTooLargeError": ".chunked_upload",
    # keyboards
    "ApprovalRequest": ".keyboards",
    "ApprovalSender": ".keyboards",
    "InlineKeyboard": ".keyboards",
    "InteractionEvent": ".keyboards",
    "build_approval_keyboard": ".keyboards",
    "build_approval_text": ".keyboards",
    "build_update_prompt_keyboard": ".keyboards",
    "parse_approval_button_data": ".keyboards",
    "parse_interaction_event": ".keyboards",
    "parse_update_prompt_button_data": ".keyboards",
}


def __getattr__(name: str):  # noqa: D401 — PEP 562 module __getattr__
    module_name = _LAZY_ATTRS.get(name)
    if module_name is None:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        )
    module = importlib.import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value  # cache for subsequent accesses
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY_ATTRS))


if TYPE_CHECKING:  # pragma: no cover — imports for type-checkers only
    from .adapter import (  # noqa: F401
        QQAdapter,
        QQCloseError,
        check_qq_requirements,
        _coerce_list,
        _ssrf_redirect_guard,
    )
    from .onboard import (  # noqa: F401
        BindStatus,
        build_connect_url,
        qr_register,
    )
    from .crypto import decrypt_secret, generate_bind_key  # noqa: F401
    from .utils import build_user_agent, get_api_headers, coerce_list  # noqa: F401
    from .chunked_upload import (  # noqa: F401
        ChunkedUploader,
        UploadDailyLimitExceededError,
        UploadFileTooLargeError,
    )
    from .keyboards import (  # noqa: F401
        ApprovalRequest,
        ApprovalSender,
        InlineKeyboard,
        InteractionEvent,
        build_approval_keyboard,
        build_approval_text,
        build_update_prompt_keyboard,
        parse_approval_button_data,
        parse_interaction_event,
        parse_update_prompt_button_data,
    )

__all__ = [
    # adapter
    "QQAdapter",
    "QQCloseError",
    "check_qq_requirements",
    "_coerce_list",
    "_ssrf_redirect_guard",
    # onboard
    "BindStatus",
    "build_connect_url",
    "qr_register",
    # crypto
    "decrypt_secret",
    "generate_bind_key",
    # utils
    "build_user_agent",
    "get_api_headers",
    "coerce_list",
    # chunked upload
    "ChunkedUploader",
    "UploadDailyLimitExceededError",
    "UploadFileTooLargeError",
    # keyboards
    "ApprovalRequest",
    "ApprovalSender",
    "InlineKeyboard",
    "InteractionEvent",
    "build_approval_keyboard",
    "build_approval_text",
    "build_update_prompt_keyboard",
    "parse_approval_button_data",
    "parse_interaction_event",
    "parse_update_prompt_button_data",
]
