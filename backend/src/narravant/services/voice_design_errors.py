"""Voice Design の外部へ出せる固定分類と診断値。"""

from typing import Literal

VoiceDesignFailureReason = Literal[
    "invalid_argument",
    "credentials",
    "permission_denied",
    "quota",
    "provider_unavailable",
    "transport_unknown",
    "configuration",
    "invalid_response",
    "unknown",
]

PROVIDER_CODES = frozenset(
    {
        "INVALID_ARGUMENT",
        "UNAUTHENTICATED",
        "PERMISSION_DENIED",
        "RESOURCE_EXHAUSTED",
        "UNAVAILABLE",
        "INTERNAL",
        "DEADLINE_EXCEEDED",
    }
)


def safe_provider_code(code: object) -> str:
    return code if isinstance(code, str) and code in PROVIDER_CODES else "UNKNOWN"


def safe_provider_status(status: object) -> int | None:
    return status if type(status) is int and 100 <= status <= 599 else None


def classify_voice_design_failure(
    status: int | None,
    code: str | None,
    *,
    transport_failed: bool = False,
    configured: bool = True,
) -> VoiceDesignFailureReason:
    if not configured:
        return "configuration"
    if transport_failed:
        return "transport_unknown"
    status = safe_provider_status(status)
    if status == 400 or code == "INVALID_ARGUMENT":
        return "invalid_argument"
    if status == 401 or code == "UNAUTHENTICATED":
        return "credentials"
    if status == 403 or code == "PERMISSION_DENIED":
        return "permission_denied"
    if status == 429 or code == "RESOURCE_EXHAUSTED":
        return "quota"
    if status in {502, 503, 504} or code == "UNAVAILABLE":
        return "provider_unavailable"
    return "unknown"
