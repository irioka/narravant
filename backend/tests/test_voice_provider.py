from __future__ import annotations

import logging

import httpx
import pytest

from narravant.services.voice_design import GeminiVoiceDesignClient, VoiceDesignError


@pytest.mark.asyncio
async def test_list_voices_uses_page_token_and_returns_only_allowlisted_custom_metadata(monkeypatch) -> None:
    captured: list[dict[str, object]] = []

    async def mock_get(self, url, *args, **kwargs):
        captured.append({"url": str(url), **kwargs})
        return httpx.Response(
            status_code=200,
            json={
                "voices": [
                    {
                        "id": "voice_prompted_1",
                        "type": "VOICE_TYPE_PROMPTED",
                        "language_code": "ja-JP",
                        "expire_time": "2027-10-15T00:00:00Z",
                        "key": "must-not-be-retained",
                        "prompted": {"input": "provider prompt is not stored here"},
                    },
                    {"id": "Puck", "type": "VOICE_TYPE_PREBUILT"},
                    {"id": "voice_replicated_1", "type": "replicated"},
                ],
                "nextPageToken": "next-token",
            },
            request=httpx.Request("GET", str(url)),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    client = GeminiVoiceDesignClient(api_key="synthetic-key")

    page = await client.list_voices("previous-token")

    assert len(captured) == 1
    assert captured[0]["url"] == "https://generativelanguage.googleapis.com/v1beta/voices"
    assert captured[0]["headers"] == {"x-goog-api-key": "synthetic-key"}
    assert captured[0]["params"] == {"pageSize": 100, "pageToken": "previous-token"}
    assert [voice.voice_id for voice in page.voices] == ["voice_prompted_1", "voice_replicated_1"]
    assert page.voices[0].expire_time.isoformat() == "2027-10-15T00:00:00+00:00"
    assert page.voices[0].language_code == "ja-JP"
    assert page.next_page_token == "next-token"
    assert "key" not in vars(page.voices[0])
    assert "prompted" not in vars(page.voices[0])


@pytest.mark.asyncio
async def test_get_voice_converts_resource_name_to_safe_canonical_path(monkeypatch) -> None:
    urls: list[str] = []

    async def mock_get(self, url, *args, **kwargs):
        urls.append(str(url))
        return httpx.Response(
            status_code=200,
            json={"id": "voice_safe_1", "type": "prompted", "expire_time": "2027-10-15T00:00:00Z"},
            request=httpx.Request("GET", str(url)),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    voice = await GeminiVoiceDesignClient(api_key="synthetic-key").get_voice("voices/voice_safe_1")

    assert urls == ["https://generativelanguage.googleapis.com/v1beta/voices/voice_safe_1"]
    assert voice.voice_id == "voice_safe_1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "voice_id", ["Puck", "voice_a/../voice_b", "https://example.test/voice_a", "voice_a%2fvoice_b"]
)
async def test_get_and_delete_reject_unsafe_or_prebuilt_ids_before_network(voice_id, monkeypatch) -> None:
    async def unexpected_request(self, *args, **kwargs):
        raise AssertionError("request must not be sent for an invalid voice ID")

    monkeypatch.setattr(httpx.AsyncClient, "get", unexpected_request)
    monkeypatch.setattr(httpx.AsyncClient, "delete", unexpected_request)
    client = GeminiVoiceDesignClient(api_key="synthetic-key")

    with pytest.raises(ValueError, match="Invalid custom voice ID"):
        await client.get_voice(voice_id)
    with pytest.raises(ValueError, match="Invalid custom voice ID"):
        await client.delete_voice(voice_id)


@pytest.mark.asyncio
async def test_get_retries_only_rejected_transient_statuses(monkeypatch) -> None:
    statuses = [503, 502, 200]
    calls = 0

    async def mock_get(self, url, *args, **kwargs):
        nonlocal calls
        status_code = statuses[calls]
        calls += 1
        payload = (
            {"id": "voice_retry_1", "type": "prompted"} if status_code == 200 else {"error": {"status": "UNAVAILABLE"}}
        )
        return httpx.Response(status_code=status_code, json=payload, request=httpx.Request("GET", str(url)))

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    client = GeminiVoiceDesignClient(api_key="synthetic-key", retry_backoff_seconds=0)

    voice = await client.get_voice("voice_retry_1")

    assert voice.voice_id == "voice_retry_1"
    assert calls == 3


@pytest.mark.asyncio
async def test_get_does_not_retry_network_errors(monkeypatch) -> None:
    calls = 0

    async def mock_get(self, url, *args, **kwargs):
        nonlocal calls
        calls += 1
        raise httpx.ConnectTimeout("synthetic timeout")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    client = GeminiVoiceDesignClient(api_key="synthetic-key")

    with pytest.raises(VoiceDesignError) as raised:
        await client.get_voice("voice_timeout_1")

    assert raised.value.provider_code == "NETWORK_ERROR"
    assert calls == 1


@pytest.mark.asyncio
async def test_delete_uses_one_request_and_does_not_retry_timeout(monkeypatch) -> None:
    calls = 0
    urls: list[str] = []

    async def mock_delete(self, url, *args, **kwargs):
        nonlocal calls
        calls += 1
        urls.append(str(url))
        raise httpx.ReadTimeout("synthetic timeout")

    monkeypatch.setattr(httpx.AsyncClient, "delete", mock_delete)
    client = GeminiVoiceDesignClient(api_key="synthetic-key", max_attempts=5, retry_backoff_seconds=0)

    with pytest.raises(VoiceDesignError) as raised:
        await client.delete_voice("voice_delete_1")

    assert raised.value.provider_code == "NETWORK_ERROR"
    assert calls == 1
    assert urls == ["https://generativelanguage.googleapis.com/v1beta/voices/voice_delete_1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("operation", "status", "expected_level"), [
    ("get", 404, logging.DEBUG),
    ("list", 404, logging.WARNING),
    ("get", 403, logging.WARNING),
])
async def test_only_individual_missing_voice_is_debug_logged(operation, status, expected_level, monkeypatch, caplog):
    calls = 0

    async def mock_get(self, url, *args, **kwargs):
        nonlocal calls
        calls += 1
        return httpx.Response(
            status,
            json={"error": {"status": "NOT_FOUND" if status == 404 else "PERMISSION_DENIED",
                            "message": "synthetic-private-body"}},
            request=httpx.Request("GET", str(url)),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    caplog.set_level(logging.DEBUG, logger="narravant.services.voice_design")
    client = GeminiVoiceDesignClient(api_key="synthetic-secret-key")
    with pytest.raises(VoiceDesignError) as raised:
        if operation == "get":
            await client.get_voice("voice_missing_test")
        else:
            await client.list_voices()
    assert raised.value.provider_status == status
    assert calls == 1
    records = [record for record in caplog.records if "rejected status=" in record.message]
    assert len(records) == 1
    assert records[0].levelno == expected_level
    assert "synthetic-private-body" not in caplog.text
    assert "synthetic-secret-key" not in caplog.text
    assert "voice_missing_test" not in caplog.text


@pytest.mark.asyncio
async def test_non_json_provider_response_is_typed_without_echoing_response_body(monkeypatch) -> None:
    async def mock_get(self, url, *args, **kwargs):
        return httpx.Response(
            status_code=200,
            content=b"synthetic private provider body",
            request=httpx.Request("GET", str(url)),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    client = GeminiVoiceDesignClient(api_key="synthetic-key")

    with pytest.raises(VoiceDesignError) as raised:
        await client.get_voice("voice_json_1")

    assert raised.value.provider_code == "INVALID_RESPONSE"
    assert "synthetic private provider body" not in str(raised.value)
