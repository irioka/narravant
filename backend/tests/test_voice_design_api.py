from __future__ import annotations

import httpx
import pytest

from narravant.api.dependencies import (
    LOCAL_USER_DISPLAY_NAME,
    LOCAL_USER_EMAIL,
    LOCAL_USER_ID,
    CurrentUser,
    get_current_user,
    get_db_manager,
    get_document_repo,
    get_settings,
    get_storage_client,
    get_voice_design_client,
    get_voice_management_service,
    get_voice_registry_repo,
)
from narravant.db.database import DatabaseManager, DocumentRepository
from narravant.db.voice_registry import VoiceRegistryRepository
from narravant.main import app
from narravant.services.voice_design import FakeVoiceDesignClient
from narravant.services.voice_management import VoiceManagementService
from narravant.storage.gcs import InMemoryScriptStorageClient


@pytest.fixture
def fake_voice_client() -> FakeVoiceDesignClient:
    return FakeVoiceDesignClient()


@pytest.mark.asyncio
async def test_fake_voice_design_client_keeps_identical_trait_creations_distinct() -> None:
    provider = FakeVoiceDesignClient()

    first = await provider.create_voice("Alice", "Warm, clear voice", display_name="Book ; Alice")
    second = await provider.create_voice("Bob", "Warm, clear voice", display_name="Book ; Bob")

    assert first.voice_id.startswith("voice_fake_")
    assert second.voice_id.startswith("voice_fake_")
    assert first.voice_id != second.voice_id
    assert len((await provider.list_voices()).voices) == 2
    assert (await provider.get_voice(first.voice_id)).display_name == "Book ; Alice"
    assert (await provider.get_voice(second.voice_id)).display_name == "Book ; Bob"


@pytest.fixture
async def client(fake_voice_client: FakeVoiceDesignClient) -> httpx.AsyncClient:
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
        gemini_safety_threshold="BLOCK_NONE",
        emotion_arc_max_points=36,
        valence_max_arc_distance=1.0,
    )
    app.state.settings = mock_settings

    async def settings_provider():
        return mock_settings

    async def db_provider():
        return db

    async def document_repo_provider():
        return repo

    async def storage_provider():
        return storage

    async def current_user_provider():
        return CurrentUser(LOCAL_USER_ID, LOCAL_USER_EMAIL, LOCAL_USER_DISPLAY_NAME)

    async def voice_design_provider():
        return fake_voice_client

    voice_registry = VoiceRegistryRepository(db)

    async def voice_registry_provider():
        return voice_registry

    voice_service = VoiceManagementService(
        fake_voice_client,
        voice_registry,
        repo,
        storage,
        local_owner_id=LOCAL_USER_ID,
    )

    async def voice_management_provider():
        return voice_service

    app.dependency_overrides[get_settings] = settings_provider
    app.dependency_overrides[get_db_manager] = db_provider
    app.dependency_overrides[get_document_repo] = document_repo_provider
    app.dependency_overrides[get_storage_client] = storage_provider
    app.dependency_overrides[get_current_user] = current_user_provider
    app.dependency_overrides[get_voice_design_client] = voice_design_provider
    app.dependency_overrides[get_voice_management_service] = voice_management_provider
    app.dependency_overrides[get_voice_registry_repo] = voice_registry_provider

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


async def test_create_voice_success_and_does_not_advance_version(
    client: httpx.AsyncClient,
    fake_voice_client: FakeVoiceDesignClient,
):
    """Voice Design creates a voice without mutating document or advancing version_id."""
    doc_id = "doc-test-123"

    # Verify initial version is 1
    doc_resp = await client.get(f"/api/v1/documents/{doc_id}")
    assert doc_resp.status_code == 200
    assert doc_resp.json()["current_version_id"] == 1

    # Call POST /voices
    response = await client.post(
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
    assert data["voice_id"].startswith("voice_fake_")
    assert data["voice_traits"] == "落ち着いた男性の声"
    created_voice_id = data["voice_id"]

    # Verify version_id is STILL 1 (MF-2: no persistence, no version advance)
    doc_resp2 = await client.get(f"/api/v1/documents/{doc_id}")
    assert doc_resp2.status_code == 200
    assert doc_resp2.json()["current_version_id"] == 1

    # Verify that subsequent PUT with expected_version=1 succeeds (no 409 Conflict)
    put_resp = await client.put(
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


async def test_create_voice_nonexistent_document_returns_404(client: httpx.AsyncClient):
    response = await client.post(
        "/api/v1/documents/non-existent-doc/voices",
        json={
            "speaker": "ナレーター",
            "voice_traits": "落ち着いた男性の声",
        },
    )
    assert response.status_code == 404


async def test_create_draft_voice_does_not_require_a_persisted_document(
    client: httpx.AsyncClient,
    fake_voice_client: FakeVoiceDesignClient,
):
    response = await client.post(
        "/api/v1/voices",
        json={
            "speaker": "未保存の話者",
            "voice_traits": "落ち着いた声",
        },
    )
    assert response.status_code == 200
    assert response.json()["speaker"] == "未保存の話者"
    assert fake_voice_client.call_count == 1


async def test_create_voice_failure_returns_502_and_preserves_document(
    client: httpx.AsyncClient,
    fake_voice_client: FakeVoiceDesignClient,
):
    doc_id = "doc-test-123"
    fake_voice_client.should_fail = True

    response = await client.post(
        f"/api/v1/documents/{doc_id}/voices",
        json={
            "speaker": "ナレーター",
            "voice_traits": "落ち着いた男性の声",
        },
    )
    assert response.status_code == 502
    data = response.json()
    assert "error" in data
    assert data["error"]["message"] == "Voice creation was rejected. The submitted description was not changed."
    assert "Simulated Voice Design failure" not in data["error"]["message"]

    # Document version and data remain intact
    doc_resp = await client.get(f"/api/v1/documents/{doc_id}")
    assert doc_resp.status_code == 200
    assert doc_resp.json()["current_version_id"] == 1


async def test_create_voice_different_traits_produce_different_ids(
    client: httpx.AsyncClient,
    fake_voice_client: FakeVoiceDesignClient,
):
    doc_id = "doc-test-123"

    resp1 = await client.post(
        f"/api/v1/documents/{doc_id}/voices",
        json={"speaker": "アリス", "voice_traits": "元気な少女の声"},
    )
    assert resp1.status_code == 200
    voice_id_1 = resp1.json()["voice_id"]

    resp2 = await client.post(
        f"/api/v1/documents/{doc_id}/voices",
        json={"speaker": "アリス", "voice_traits": "落ち着いた大人の女性の声"},
    )
    assert resp2.status_code == 200
    voice_id_2 = resp2.json()["voice_id"]

    assert voice_id_1 != voice_id_2
    assert voice_id_1.startswith("voice_fake_")
    assert voice_id_2.startswith("voice_fake_")


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

    assert voice_id.voice_id == "voice_real_style_id_789"
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
async def test_gemini_voice_design_client_sends_optional_display_name(monkeypatch) -> None:
    import httpx

    from narravant.services.voice_design import GeminiVoiceDesignClient

    captured_payloads: list[dict[str, object]] = []

    async def mock_post(self, url, *args, **kwargs):
        captured_payloads.append(kwargs["json"])
        return httpx.Response(
            status_code=200,
            json={"id": "voice_display_name_test"},
            request=httpx.Request("POST", str(url)),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    display_name = "The Wonderful Wizard of Oz - III. How Dorothy Saved the Scarecrow ; Narrator"

    await GeminiVoiceDesignClient(api_key="synthetic-key").create_voice(
        speaker="Narrator",
        voice_traits="Warm, articulate storybook narrator",
        display_name=display_name,
    )

    assert captured_payloads == [
        {
            "store": True,
            "voice": {
                "type": "prompted",
                "prompted": {"input": "Warm, articulate storybook narrator"},
                "model": "gemini-3.8-flash-tts",
                "language_code": "ja-JP",
                "display_name": display_name,
            },
        }
    ]


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
async def test_invalid_argument_never_changes_or_retries_input(monkeypatch):
    from narravant.services.voice_design import GeminiVoiceDesignClient, VoiceDesignError

    requests = []

    async def post(self, url, **kwargs):
        requests.append(kwargs["json"])
        return httpx.Response(
            400 if len(requests) == 1 else 200,
            json={"error": {"status": "INVALID_ARGUMENT"}} if len(requests) == 1 else {"id": "voice_synthetic"},
            request=httpx.Request("POST", str(url)),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    client = GeminiVoiceDesignClient(api_key="synthetic-key", tts_model="gemini-3.8-flash-tts")
    with pytest.raises(VoiceDesignError):
        await client.create_voice("合成人物", "猫のような比喩を含む合成説明")
    assert len(requests) == 1
    assert requests[0]["voice"] == {
        "type": "prompted",
        "prompted": {"input": "猫のような比喩を含む合成説明"},
        "model": "gemini-3.8-flash-tts",
        "language_code": "ja-JP",
    }


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
                json={"id": "voice_test"},
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
        attempts.append(kwargs["json"])
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

    assert voice_id.voice_id == "voice_after_transient_retry"
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]


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
    assert raised.value.attempt == 3


def test_transient_provider_error_maps_to_503_service_unavailable() -> None:
    from narravant.api.voices import _is_transient_provider_error

    assert _is_transient_provider_error(503, "UNAVAILABLE")
    assert not _is_transient_provider_error(None, "NETWORK_ERROR")
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
        gemini_safety_threshold="BLOCK_NONE",
        gemini_connection_timeout_seconds=300,
        gemini_voice_design_max_attempts=5,
        gemini_generation_max_attempts=6,
        gemini_retry_backoff_seconds=3,
    )

    client = create_voice_design_client(settings)

    assert isinstance(client, GeminiVoiceDesignClient)
    assert client._max_attempts == 5
    assert client._retry_backoff_seconds == 3


@pytest.mark.parametrize(
    "status,code,expected",
    [
        (400, "INVALID_ARGUMENT", "invalid_argument"),
        (401, "UNAUTHENTICATED", "credentials"),
        (403, "PERMISSION_DENIED", "permission_denied"),
        (429, "RESOURCE_EXHAUSTED", "quota"),
        (503, "UNAVAILABLE", "provider_unavailable"),
        (418, "private-sentinel", "unknown"),
        (400, "RESOURCE_EXHAUSTED", "invalid_argument"),
        (503, "INVALID_ARGUMENT", "invalid_argument"),
    ],
)
def test_voice_failure_classification_is_fixed(status, code, expected):
    from narravant.services.voice_design_errors import classify_voice_design_failure

    assert classify_voice_design_failure(status, code) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "disconnect", "invalid_json", "invalid_metadata"])
async def test_unknown_creation_result_is_not_retried(monkeypatch, mode):
    from narravant.services.voice_design import GeminiVoiceDesignClient, VoiceDesignError

    calls = []

    async def post(self, url, **kwargs):
        calls.append(kwargs["json"])
        request = httpx.Request("POST", str(url))
        if mode == "timeout":
            raise httpx.ReadTimeout("private-sentinel", request=request)
        if mode == "disconnect":
            raise httpx.ConnectError("private-sentinel", request=request)
        if mode == "invalid_json":
            return httpx.Response(200, content=b"private-sentinel", request=request)
        return httpx.Response(200, json={"id": "private-sentinel/invalid"}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    with pytest.raises(VoiceDesignError) as raised:
        await GeminiVoiceDesignClient(api_key="synthetic-key").create_voice("合成人物", "合成説明")
    assert len(calls) == 1
    assert raised.value.reason == ("transport_unknown" if mode in {"timeout", "disconnect"} else "invalid_response")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/voices", "/api/v1/documents/doc-test-123/voices"])
@pytest.mark.parametrize("mode", ["rejected", "invalid_json", "configuration", "transport"])
async def test_creation_failure_safe_context_and_log(client, monkeypatch, caplog, path, mode):
    from narravant.services.voice_design import GeminiVoiceDesignClient

    provider = GeminiVoiceDesignClient(api_key="" if mode == "configuration" else "synthetic-secret-key")

    async def provider_override():
        return provider

    async def post(self, url, **kwargs):
        request = httpx.Request("POST", str(url))
        if mode == "transport":
            raise httpx.ReadTimeout("private-sentinel", request=request)
        if mode == "invalid_json":
            return httpx.Response(200, content=b"private-sentinel", request=request)
        return httpx.Response(
            400, json={"error": {"status": "private-sentinel", "message": "private-sentinel"}}, request=request
        )

    app.dependency_overrides[get_voice_design_client] = provider_override
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    # ASGIの送信自体はモック対象のpostを使わない。
    before = (await client.get("/api/v1/documents/doc-test-123")).json()
    response = await client.request(
        "POST", path, json={"speaker": "synthetic-speaker-secret", "voice_traits": "synthetic-traits-secret"}
    )
    expected = {
        "rejected": "invalid_argument",
        "invalid_json": "invalid_response",
        "configuration": "configuration",
        "transport": "transport_unknown",
    }[mode]
    assert response.status_code == 502
    context = response.json()["error"]["context"]
    assert context == {
        "operation": "voice_design",
        "reason": expected,
        "provider_status": 400 if mode == "rejected" else 200 if mode == "invalid_json" else None,
        "provider_code": "UNKNOWN",
        "retryable": False,
    }
    assert f"reason={expected}" in caplog.text
    for secret in ["private-sentinel", "synthetic-secret-key", "synthetic-traits-secret", "synthetic-speaker-secret"]:
        assert secret not in caplog.text + response.text
    assert (await client.get("/api/v1/documents/doc-test-123")).json() == before
    registry = await app.dependency_overrides[get_voice_registry_repo]()
    assert registry.list(LOCAL_USER_ID) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/voices", "/api/v1/documents/doc-test-123/voices"])
@pytest.mark.parametrize(
    "status,code,http_status,reason",
    [
        (400, "INVALID_ARGUMENT", 502, "invalid_argument"),
        (401, "UNAUTHENTICATED", 502, "credentials"),
        (403, "PERMISSION_DENIED", 502, "permission_denied"),
        (429, "RESOURCE_EXHAUSTED", 409, "quota"),
        (429, None, 409, "quota"),
        (503, "UNAVAILABLE", 503, "provider_unavailable"),
        (418, "private-sentinel", 502, "unknown"),
        (400, "RESOURCE_EXHAUSTED", 502, "invalid_argument"),
        (503, "INVALID_ARGUMENT", 502, "invalid_argument"),
        (400, "INVALID_RESPONSE", 502, "invalid_argument"),
    ],
)
async def test_creation_error_http_compatibility(client, monkeypatch, caplog, path, status, code, http_status, reason):
    from narravant.services.voice_design import GeminiVoiceDesignClient

    provider = GeminiVoiceDesignClient(api_key="synthetic-key", max_attempts=1)

    async def override():
        return provider

    async def post(self, url, **kwargs):
        return httpx.Response(
            status,
            json={"error": {"status": code, "message": "private-sentinel"}},
            request=httpx.Request("POST", str(url)),
        )

    app.dependency_overrides[get_voice_design_client] = override
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    response = await client.request("POST", path, json={"speaker": "合成人物", "voice_traits": "合成説明"})
    assert response.status_code == http_status
    error = response.json()["error"]
    if http_status == 409:
        assert error["code"] == "CONFLICT"
        assert error["message"].startswith("VOICE_LIMIT_EXCEEDED:")
        assert error["context"] is None
    elif http_status == 503:
        assert error["message"].startswith("VOICE_PROVIDER_UNAVAILABLE:")
        assert error["context"] is None
    else:
        assert error["context"]["reason"] == reason
    assert f"reason={reason}" in caplog.text
    assert "attempt=1" in caplog.text
    assert "private-sentinel" not in caplog.text + response.text


def test_voice_diagnostics_allowlist():
    from narravant.services.voice_design_errors import safe_provider_code, safe_provider_status

    for status in [True, False, "400", 99, 600, None]:
        assert safe_provider_status(status) is None
    assert safe_provider_status(200) == 200
    assert safe_provider_status(599) == 599
    for code in [None, [], "private-sentinel", "invalid_argument"]:
        assert safe_provider_code(code) == "UNKNOWN"
    assert safe_provider_code("INVALID_ARGUMENT") == "INVALID_ARGUMENT"


@pytest.mark.parametrize("path", ["/api/v1/documents/doc-test-123/voices", "/api/v1/voices"])
async def test_sectioned_traits_use_only_voice_section_and_preserve_full_profile(client, fake_voice_client, path):
    traits = "Voice: A synthetic clear voice.\nDelivery: Portray a fictional child naturally."
    response = await client.post(path, json={"speaker": "アリス", "voice_traits": traits})
    assert response.status_code == 200
    assert fake_voice_client.last_traits == "A synthetic clear voice."
    assert response.json()["voice_traits"] == traits


@pytest.mark.parametrize("explicit_style", [None, "", "Urgently."])
async def test_preview_uses_registered_delivery_unless_explicitly_overridden(client, explicit_style):
    from narravant.api.dependencies import get_tts_client
    from narravant.services.tts import FakeTtsClient

    tts = FakeTtsClient()

    async def tts_provider():
        return tts

    app.dependency_overrides[get_tts_client] = tts_provider
    traits = "Voice: A synthetic adult actor voice.\nDelivery: Portray a fictional child naturally."
    created = await client.post("/api/v1/voices", json={"speaker": "アリス", "voice_traits": traits})
    assert created.status_code == 200
    body = {"voice_id": created.json()["voice_id"], "text": "Hello."}
    if explicit_style is not None:
        body["style"] = explicit_style
    response = await client.post("/api/v1/voices/preview", json=body)
    assert response.status_code == 200
    assert tts.last_style == ("Portray a fictional child naturally." if explicit_style is None else explicit_style)
    registered = await client.get("/api/v1/voices/" + created.json()["voice_id"])
    assert registered.json()["voice_traits"] == traits


async def test_sectioned_import_profile_reaches_mock_http_without_delivery(client, monkeypatch):
    from narravant.services.ingestion import draft_response, validate_import
    from narravant.services.voice_design import GeminiVoiceDesignClient
    from test_import_api import FakeDocumentAnalyzer, _service_with_adapter

    traits = (
        "Voice: An adult actor with a clear light register.\n"
        "Delivery: Portray the source-established fictional child naturally."
    )
    analyzer = FakeDocumentAnalyzer()
    original = analyzer.analyze

    def analyze(*args, **kwargs):
        result = original(*args, **kwargs)
        result.characters[0]["voice_traits"] = traits
        return result

    analyzer.analyze = analyze
    service, _, _, _ = _service_with_adapter(analyzer=analyzer)
    content = b"Title: Synthetic\n\nINT. ROOM - DAY #1#\n\nAction.\n"
    imported = validate_import(
        "synthetic.fountain", content, 1024 * 1024, pdf_max_pages=100, fdx_max_depth=32, txt_minimum_confidence=0.7
    )
    task_id = service.start("owner-1", imported)
    service.run(task_id, content)
    draft = draft_response(service.drafts.get(task_id))
    assert draft.analysis.characters[0].voice_traits == traits
    captured = []

    async def post(_self, url, **kwargs):
        captured.append(kwargs["json"])
        return httpx.Response(200, json={"id": "voice_synthetic_sections"}, request=httpx.Request("POST", url))

    async def provider_override():
        return GeminiVoiceDesignClient(api_key="synthetic")

    app.dependency_overrides[get_voice_design_client] = provider_override
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    response = await client.request(
        "POST",
        "/api/v1/voices",
        json={"speaker": "Synthetic", "voice_traits": draft.analysis.characters[0].voice_traits},
    )
    assert response.status_code == 200
    assert response.json()["voice_traits"] == traits
    assert len(captured) == 1
    assert captured[0]["voice"]["prompted"]["input"] == "An adult actor with a clear light register."
    assert captured[0]["voice"]["language_code"] == "en-US"
