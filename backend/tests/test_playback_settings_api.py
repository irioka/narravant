from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from narravant.api.dependencies import CurrentUser, get_current_user, get_settings
from narravant.api.playback import PlaybackStartRequest
from narravant.main import app

FIELDS = ("scene_pause_duration_ms", "speaker_change_pause_duration_ms", "narrator_sentence_pause_duration_ms")


@pytest.mark.parametrize("field", FIELDS)
def test_start_accepts_explicit_zero_pause(field):
    request = PlaybackStartRequest.model_validate({"action": "start", field: 0})
    assert getattr(request, field) == 0


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", [-1, 60001, True, 1.2, "1200", float("nan"), float("inf")])
def test_start_rejects_invalid_pause(field, value):
    with pytest.raises(ValidationError):
        PlaybackStartRequest.model_validate({"action": "start", field: value})


@pytest.mark.asyncio
async def test_get_playback_defaults_uses_settings_and_exposes_only_pauses():
    settings = SimpleNamespace(
        playback_scene_pause_duration_ms=1700,
        playback_speaker_change_pause_duration_ms=250,
        playback_narrator_sentence_pause_duration_ms=800,
        gemini_api_key="SYNTHETIC_PRIVATE",
    )

    async def current_user():
        return CurrentUser("synthetic-user", "synthetic@example.test")

    async def current_settings():
        return settings

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_settings] = current_settings
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/playback/settings")
        assert response.status_code == 200
        assert response.json() == {
            "scene_pause_duration_ms": 1700,
            "speaker_change_pause_duration_ms": 250,
            "narrator_sentence_pause_duration_ms": 800,
            "min_ms": 0,
            "max_ms": 60000,
            "step_ms": 100,
        }
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_settings, None)
