import pytest
from httpx import ASGITransport, AsyncClient

from narravant.main import app


@pytest.mark.asyncio
async def test_healthz():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "narravant"}


@pytest.mark.asyncio
async def test_api_version():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/v1/version")
    assert response.status_code == 200
    assert response.json() == {"version": "0.1.2", "name": "NARRAVANT"}
