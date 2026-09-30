from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from narravant.api.dependencies import (
    LOCAL_USER_DISPLAY_NAME,
    LOCAL_USER_EMAIL,
    LOCAL_USER_ID,
    CurrentUser,
    get_current_user,
    get_db_manager,
    get_settings,
    get_storage_client,
    get_voice_design_client,
)
from narravant.db.database import DatabaseManager, DocumentRepository
from narravant.main import app
from narravant.services.voice_design import FakeVoiceDesignClient
from narravant.storage.gcs import InMemoryScriptStorageClient


@pytest.fixture
def fake_voice_client() -> FakeVoiceDesignClient:
    return FakeVoiceDesignClient()


@pytest.fixture
def client(fake_voice_client: FakeVoiceDesignClient) -> TestClient:
    db = DatabaseManager(":memory:")
    storage = InMemoryScriptStorageClient()

    # Pre-populate user and a test document
    db.init_schema()
    repo = DocumentRepository(db)
    repo.ensure_user(LOCAL_USER_ID, LOCAL_USER_EMAIL, LOCAL_USER_DISPLAY_NAME)

    from narravant.core.emotion_arc_resolution import scene_mapping_payload

    doc_id = "doc-test-123"
    source_fountain = "Title: Test Title\n\nINT. ROOM - DAY #1#\n\nナレーター\nこんにちは。\n"
    stored = storage.write_structured_script(
        document_id=doc_id,
        version_id=1,
        data={
            "schema_version": 1,
            "document_id": doc_id,
            "version_id": 1,
            "owner_user_id": LOCAL_USER_ID,
            "metadata": {
                "title": "Test Title",
                "characters": ["ナレーター", "アリス"],
                "logline": "Test logline",
                "synopsis": "Test synopsis",
                "theme_setting": "テーマ",
            },
            "source": {
                "filename": "test.fountain",
                "media_type": "text/x-fountain",
                "sha256": "a" * 64,
            },
            "source_fountain": source_fountain,
            "scenes": [
                {
                    "scene_number": 1,
                    "heading": "INT. ROOM - DAY",
                    "text": "ナレーター\nこんにちは。\n",
                    "dialogues": [],
                }
            ],
            "analysis": {
                "status": "completed",
                "turning_points": [
                    {
                        "tp_number": number,
                        "label": label,
                        "availability": "identified",
                        "scene_number": 1,
                        "change": f"物語の変化{number}",
                        "involved_characters": [
                            {
                                "name": "A",
                                "goal": "g",
                                "conflict": "c",
                                "choice": "ch",
                                "action": "a",
                                "change": "d",
                            }
                        ],
                        "reason": None,
                    }
                    for number, label in enumerate(
                        [
                            "Opportunity",
                            "Change of Plans",
                            "Point of No Return",
                            "Major Setback",
                            "Climax",
                        ],
                        start=1,
                    )
                ],
                "characters": [
                    {
                        "name": "A",
                        "external_goal": "外的目標",
                        "internal_need": "内的欲求",
                        "fear_or_cost": "恐れ",
                        "obstacle": "障害",
                        "choice": "選択",
                        "agency": "主体性",
                        "goal_to_outcome": "目標→結果",
                        "related_turning_points": [1],
                    }
                ],
            },
            "emotion_arc": {
                "valence": [4],
                "tension": [0],
                "characters": {"A": [3]},
                "scene_mapping": scene_mapping_payload(1, 36),
                "valence_vector": [0.0] * 9 + [1.0],
            },
            "narrator": {"voice_traits": "落ち着いた声"},
            "voice_assignments": [],
        },
        if_generation_match=0,
    )
    storage.write_search_text(doc_id, source_fountain, if_generation_match=0)
    repo.create_document(
        doc_id,
        LOCAL_USER_ID,
        "Test Title",
        stored.uri,
        stored.generation,
        stored.sha256,
        [0.0] * 9 + [1.0],
    )

    app.state.db_manager = db
    app.state.storage_client = storage

    from types import SimpleNamespace

    mock_settings = SimpleNamespace(
        gemini_api_key="fake",
        gemini_tts_model="gemini-3.8-flash-tts",
        emotion_arc_max_points=36,
        valence_max_arc_distance=1.0,
    )
    app.state.settings = mock_settings

    app.dependency_overrides[get_settings] = lambda: mock_settings
    app.dependency_overrides[get_db_manager] = lambda: db
    app.dependency_overrides[get_storage_client] = lambda: storage
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        LOCAL_USER_ID, LOCAL_USER_EMAIL, LOCAL_USER_DISPLAY_NAME
    )
    app.dependency_overrides[get_voice_design_client] = lambda: fake_voice_client

    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_create_voice_success_and_does_not_advance_version(
    client: TestClient,
    fake_voice_client: FakeVoiceDesignClient,
):
    """Voice Design creates a voice without mutating document or advancing version_id."""
    doc_id = "doc-test-123"

    # Verify initial version is 1
    doc_resp = client.get(f"/api/v1/documents/{doc_id}")
    assert doc_resp.status_code == 200
    assert doc_resp.json()["current_version_id"] == 1

    # Call POST /voices
    response = client.post(
        f"/api/v1/documents/{doc_id}/voices",
        json={
            "speaker": "ナレーター",
            "voice_traits": "落ち着いた男性の声",
            "language_code": "ja",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["speaker"] == "ナレーター"
    assert "voices/fake-ナレーター-" in data["voice_id"]
    assert data["voice_traits"] == "落ち着いた男性の声"
    created_voice_id = data["voice_id"]

    # Verify version_id is STILL 1 (MF-2: no persistence, no version advance)
    doc_resp2 = client.get(f"/api/v1/documents/{doc_id}")
    assert doc_resp2.status_code == 200
    assert doc_resp2.json()["current_version_id"] == 1

    # Verify that subsequent PUT with expected_version=1 succeeds (no 409 Conflict)
    put_resp = client.put(
        f"/api/v1/documents/{doc_id}",
        json={
            "expected_version": 1,
            "title": "Test Title",
            "voice_assignments": [
                {
                    "speaker": "ナレーター",
                    "voice_id": created_voice_id,
                    "voice_traits": "落ち着いた男性の声",
                }
            ],
        },
    )
    assert put_resp.status_code == 200
    updated_doc = put_resp.json()
    assert updated_doc["current_version_id"] == 2
    assert len(updated_doc["voice_assignments"]) == 1
    assert updated_doc["voice_assignments"][0]["voice_id"] == created_voice_id


def test_create_voice_nonexistent_document_returns_404(client: TestClient):
    response = client.post(
        "/api/v1/documents/non-existent-doc/voices",
        json={
            "speaker": "ナレーター",
            "voice_traits": "落ち着いた男性の声",
        },
    )
    assert response.status_code == 404


def test_create_draft_voice_does_not_require_a_persisted_document(
    client: TestClient,
    fake_voice_client: FakeVoiceDesignClient,
):
    response = client.post(
        "/api/v1/voices",
        json={
            "speaker": "未保存の話者",
            "voice_traits": "落ち着いた声",
        },
    )
    assert response.status_code == 200
    assert response.json()["speaker"] == "未保存の話者"
    assert fake_voice_client.call_count == 1


def test_create_voice_failure_returns_502_and_preserves_document(
    client: TestClient,
    fake_voice_client: FakeVoiceDesignClient,
):
    doc_id = "doc-test-123"
    fake_voice_client.should_fail = True

    response = client.post(
        f"/api/v1/documents/{doc_id}/voices",
        json={
            "speaker": "ナレーター",
            "voice_traits": "落ち着いた男性の声",
        },
    )
    assert response.status_code == 502
    data = response.json()
    assert "error" in data
    assert data["error"]["message"] == "Gemini Voice Design request failed."
    assert "Simulated Voice Design failure" not in data["error"]["message"]

    # Document version and data remain intact
    doc_resp = client.get(f"/api/v1/documents/{doc_id}")
    assert doc_resp.status_code == 200
    assert doc_resp.json()["current_version_id"] == 1


def test_create_voice_different_traits_produce_different_ids(
    client: TestClient,
    fake_voice_client: FakeVoiceDesignClient,
):
    doc_id = "doc-test-123"

    resp1 = client.post(
        f"/api/v1/documents/{doc_id}/voices",
        json={"speaker": "アリス", "voice_traits": "元気な少女の声"},
    )
    assert resp1.status_code == 200
    voice_id_1 = resp1.json()["voice_id"]

    resp2 = client.post(
        f"/api/v1/documents/{doc_id}/voices",
        json={"speaker": "アリス", "voice_traits": "落ち着いた大人の女性の声"},
    )
    assert resp2.status_code == 200
    voice_id_2 = resp2.json()["voice_id"]

    assert voice_id_1 != voice_id_2
    assert "アリス" in voice_id_1
    assert "アリス" in voice_id_2


@pytest.mark.asyncio
async def test_gemini_voice_design_client_request_shape(monkeypatch):
    import httpx

    from narravant.services.voice_design import GeminiVoiceDesignClient

    captured_requests = []

    async def mock_post(self, url, *args, **kwargs):
        captured_requests.append({"url": str(url), "kwargs": kwargs})
        return httpx.Response(
            status_code=200,
            json={"id": "voice_real_style_id_789"},
            request=httpx.Request("POST", str(url)),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    client = GeminiVoiceDesignClient(
        api_key="test-api-key-123",
        tts_model="gemini-3.8-flash-tts",
    )

    voice_id = await client.create_voice(
        speaker="ナレーター",
        voice_traits="深みのある朗読調の声",
    )

    assert voice_id == "voice_real_style_id_789"
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert "https://generativelanguage.googleapis.com/v1beta/voices" in req["url"]
    # Voice Design documents API-key authentication via this header rather than
    # a URL query parameter. Keeping the key out of the URL also prevents it
    # from being captured by transport/proxy request logs.
    assert req["kwargs"]["headers"] == {"x-goog-api-key": "test-api-key-123"}
    assert "params" not in req["kwargs"]
    json_body = req["kwargs"]["json"]
    assert json_body["store"] is True
    assert json_body["voice"]["model"] == "gemini-3.8-flash-tts"
    assert json_body["voice"]["type"] == "prompted"
    # NARRAVANT keeps the speaker-to-voice mapping locally, so provider creation
    # must not depend on validating a local display label.
    assert "display_name" not in json_body["voice"]
    assert json_body["voice"]["language_code"] == "ja-JP"
    # Q-2: voice_traits is passed as-is in Japanese without translation
    assert json_body["voice"]["prompted"]["input"] == "深みのある朗読調の声"


@pytest.mark.asyncio
async def test_gemini_voice_design_client_exposes_provider_code_without_error_body(monkeypatch):
    """Provider diagnostics remain actionable without echoing user voice traits."""
    import httpx

    from narravant.services.voice_design import GeminiVoiceDesignClient, VoiceDesignError

    async def mock_post(self, url, *args, **kwargs):
        return httpx.Response(
            status_code=400,
            json={"error": {"status": "INVALID_ARGUMENT", "message": "private voice traits"}},
            request=httpx.Request("POST", str(url)),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    client = GeminiVoiceDesignClient(api_key="test-api-key-123")
    with pytest.raises(VoiceDesignError) as raised:
        await client.create_voice(speaker="ナレーター", voice_traits="非公開の声の特徴")

    error = raised.value
    assert error.provider_status == 400
    assert error.provider_code == "INVALID_ARGUMENT"
    assert "private voice traits" not in str(error)


@pytest.mark.asyncio
async def test_gemini_voice_design_retries_invalid_optional_fields_with_minimal_payload(monkeypatch):
    """A provider 400 on optional model/language metadata gets one safe retry."""
    import httpx

    from narravant.services.voice_design import GeminiVoiceDesignClient

    captured_payloads: list[dict] = []

    async def mock_post(self, url, *args, **kwargs):
        captured_payloads.append(kwargs["json"])
        if len(captured_payloads) == 1:
            return httpx.Response(
                status_code=400,
                json={"error": {"status": "INVALID_ARGUMENT"}},
                request=httpx.Request("POST", str(url)),
            )
        return httpx.Response(
            status_code=200,
            json={"id": "voice_after_fallback"},
            request=httpx.Request("POST", str(url)),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    client = GeminiVoiceDesignClient(api_key="test-api-key-123", tts_model="gemini-3.8-flash-tts")
    voice_id = await client.create_voice(
        speaker="紳士二",
        voice_traits="知ったかぶりをする理屈っぽいトーンの中低音",
    )

    assert voice_id == "voice_after_fallback"
    assert captured_payloads[0]["voice"]["model"] == "gemini-3.8-flash-tts"
    assert captured_payloads[0]["voice"]["language_code"] == "ja-JP"
    assert captured_payloads[1] == {
        "store": True,
        "voice": {
            "type": "prompted",
            "prompted": {"input": "知ったかぶりをする理屈っぽいトーンの中低音"},
        },
    }


@pytest.mark.asyncio
async def test_gemini_voice_design_retries_with_provider_safe_prompt_after_prompt_rejection(monkeypatch):
    """A safety-sensitive generated metaphor must not block the speaker forever."""
    import httpx

    from narravant.services.voice_design import GeminiVoiceDesignClient

    captured_payloads: list[dict] = []

    async def mock_post(self, url, *args, **kwargs):
        captured_payloads.append(kwargs["json"])
        if len(captured_payloads) < 3:
            return httpx.Response(
                status_code=400,
                json={"error": {"status": "INVALID_ARGUMENT"}},
                request=httpx.Request("POST", str(url)),
            )
        return httpx.Response(
            status_code=200,
            json={"id": "voice_after_safe_prompt"},
            request=httpx.Request("POST", str(url)),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    client = GeminiVoiceDesignClient(api_key="test-api-key-123")
    voice_id = await client.create_voice(
        speaker="老婆",
        voice_traits="70代女性のかすれた声質。獣の鳴き声のように震える語り口。",
    )

    assert voice_id == "voice_after_safe_prompt"
    assert len(captured_payloads) == 3
    assert captured_payloads[1]["voice"]["prompted"]["input"].startswith("70代女性")
    safe_input = captured_payloads[2]["voice"]["prompted"]["input"]
    assert "獣" not in safe_input
    assert "鳴き声" not in safe_input
    assert safe_input.startswith("A natural human voice speaking Japanese.")


def test_voice_quota_detection_uses_structured_provider_code() -> None:
    from narravant.api.voices import _is_voice_quota_error

    assert _is_voice_quota_error("Gemini Voice Design request was rejected", "RESOURCE_EXHAUSTED")


@pytest.mark.asyncio
async def test_gemini_voice_design_client_uses_the_configured_read_timeout(monkeypatch):
    """Voice Design can take longer than the old fixed 30 second client timeout."""
    import httpx

    from narravant.services.voice_design import GeminiVoiceDesignClient

    captured_timeouts: list[object] = []

    class FakeAsyncClient:
        def __init__(self, *, timeout: object) -> None:
            captured_timeouts.append(timeout)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, *_: object, **__: object) -> httpx.Response:
            return httpx.Response(
                status_code=200,
                json={"id": "voices/test"},
                request=httpx.Request("POST", "https://example.test/voices"),
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = GeminiVoiceDesignClient(api_key="test-api-key-123", timeout_seconds=300)
    await client.create_voice(speaker="ナレーター", voice_traits="落ち着いた声")

    assert captured_timeouts == [300]


@pytest.mark.asyncio
async def test_gemini_voice_design_client_missing_key_raises():
    from narravant.services.voice_design import (
        GeminiVoiceDesignClient,
        VoiceDesignError,
    )

    client = GeminiVoiceDesignClient(api_key="")
    with pytest.raises(VoiceDesignError, match="GEMINI_API_KEY is not configured"):
        await client.create_voice(
            speaker="ナレーター",
            voice_traits="落ち着いた声",
        )


@pytest.mark.asyncio
async def test_gemini_voice_design_retries_transient_unavailable_then_succeeds(monkeypatch):
    """A transient 503 UNAVAILABLE is retried and can still succeed."""
    import httpx

    from narravant.services import voice_design as voice_design_module
    from narravant.services.voice_design import GeminiVoiceDesignClient

    # Avoid slowing the test down with the real backoff sleep.
    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(voice_design_module.asyncio, "sleep", _no_sleep)

    attempts: list[int] = []

    async def mock_post(self, url, *args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(
                status_code=503,
                json={"error": {"status": "UNAVAILABLE"}},
                request=httpx.Request("POST", str(url)),
            )
        return httpx.Response(
            status_code=200,
            json={"id": "voice_after_transient_retry"},
            request=httpx.Request("POST", str(url)),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    client = GeminiVoiceDesignClient(api_key="test-api-key-123")
    voice_id = await client.create_voice(speaker="ナレーター", voice_traits="落ち着いた声")

    assert voice_id == "voice_after_transient_retry"
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_gemini_voice_design_gives_up_after_persistent_unavailable(monkeypatch):
    """A provider that stays unavailable eventually surfaces the transient code."""
    import httpx

    from narravant.services import voice_design as voice_design_module
    from narravant.services.voice_design import GeminiVoiceDesignClient, VoiceDesignError

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(voice_design_module.asyncio, "sleep", _no_sleep)

    attempts: list[int] = []

    async def mock_post(self, url, *args, **kwargs):
        attempts.append(1)
        return httpx.Response(
            status_code=503,
            json={"error": {"status": "UNAVAILABLE"}},
            request=httpx.Request("POST", str(url)),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    client = GeminiVoiceDesignClient(api_key="test-api-key-123")
    with pytest.raises(VoiceDesignError) as raised:
        await client.create_voice(speaker="ナレーター", voice_traits="落ち着いた声")

    # 1 initial attempt + the configured number of retries.
    assert len(attempts) == 3
    assert raised.value.provider_status == 503
    assert raised.value.provider_code == "UNAVAILABLE"


def test_transient_provider_error_maps_to_503_service_unavailable() -> None:
    from narravant.api.voices import _is_transient_provider_error

    assert _is_transient_provider_error(503, "UNAVAILABLE")
    assert _is_transient_provider_error(None, "NETWORK_ERROR")
    assert _is_transient_provider_error(504, None)
    assert not _is_transient_provider_error(400, "INVALID_ARGUMENT")
    assert not _is_transient_provider_error(None, None)


@pytest.mark.asyncio
async def test_gemini_voice_design_retry_count_and_backoff_are_configurable(monkeypatch):
    """max_attempts and retry_backoff_seconds control the retry loop and wait time."""
    import httpx

    from narravant.services import voice_design as voice_design_module
    from narravant.services.voice_design import GeminiVoiceDesignClient, VoiceDesignError

    slept: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(voice_design_module.asyncio, "sleep", _record_sleep)

    attempts: list[int] = []

    async def mock_post(self, url, *args, **kwargs):
        attempts.append(1)
        return httpx.Response(
            status_code=503,
            json={"error": {"status": "UNAVAILABLE"}},
            request=httpx.Request("POST", str(url)),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    # 4 total attempts with a 2 second base: waits scale with the attempt number.
    client = GeminiVoiceDesignClient(api_key="test-api-key-123", max_attempts=4, retry_backoff_seconds=2)
    with pytest.raises(VoiceDesignError):
        await client.create_voice(speaker="ナレーター", voice_traits="落ち着いた声")

    assert len(attempts) == 4
    # One wait before each of the 3 retries, proportional to the attempt number.
    assert slept == [2, 4, 6]


def test_create_voice_design_client_threads_retry_configuration() -> None:
    from types import SimpleNamespace

    from narravant.services.voice_design import GeminiVoiceDesignClient, create_voice_design_client

    settings = SimpleNamespace(
        gemini_api_key="key",
        gemini_tts_model="gemini-3.8-flash-tts",
        gemini_connection_timeout_seconds=300,
        gemini_voice_design_max_attempts=5,
        gemini_generation_max_attempts=6,
        gemini_retry_backoff_seconds=3,
    )

    client = create_voice_design_client(settings)

    assert isinstance(client, GeminiVoiceDesignClient)
    assert client._max_attempts == 5
    assert client._retry_backoff_seconds == 3
