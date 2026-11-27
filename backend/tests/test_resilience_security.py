import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image, PngImagePlugin
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

import test_api  # Load development settings.
from app.core.circuit_breaker import CircuitBreaker
from app.core.flood_guard import FloodGuard
from app.services.advisor import provider_reply
from app.services.storage_service import sanitize_image, upload_ticket


def test_receipt_strips_metadata_and_rejects_trailing_bytes():
    encoded = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("memo", "private")
    Image.new("RGB", (2, 2)).save(encoded, "PNG", pnginfo=metadata)
    mime, extension, clean = sanitize_image(encoded.getvalue())
    assert (mime, extension) == ("image/png", "png")
    with Image.open(BytesIO(clean)) as image:
        assert "memo" not in image.info and image.size == (2, 2)
    with pytest.raises(HTTPException) as error:
        sanitize_image(encoded.getvalue() + b"extra")
    assert error.value.status_code == 400
    with patch.object(Image, "MAX_IMAGE_PIXELS", 1), pytest.raises(HTTPException) as error:
        sanitize_image(encoded.getvalue())
    assert error.value.status_code == 400


@pytest.mark.parametrize("filename", ["../sample.png", "folder\\sample.jpg", "sample.svg", "sample.php"])
def test_invalid_filename_rejected_before_ticket(filename):
    with pytest.raises(HTTPException) as error:
        asyncio.run(upload_ticket(1, "group-receipts/1", SimpleNamespace(filename=filename)))
    assert error.value.status_code == 400


def test_provider_timeout_bulkhead_and_recovery():
    async def scenario():
        started = asyncio.Event()
        async def hanging(*args):
            started.set()
            await asyncio.Event().wait()

        breaker = CircuitBreaker(failures=1, timeout=0.05, concurrency=1)
        config = SimpleNamespace(gemini_api_key=SecretStr("fixture"), gemini_model="fixture")
        async with httpx.AsyncClient() as client:
            async def reply():
                return await provider_reply("summary", {}, client, breaker)
            with patch("app.services.advisor.settings", return_value=config), patch(
                    "app.services.advisor.request_advice", side_effect=hanging) as transport:
                pending = asyncio.create_task(reply())
                await started.wait()
                assert await reply() is None and not pending.done()
                assert await pending is None and breaker.state == "OPEN"
                assert await reply() is None and transport.await_count == 1
                breaker.cooldown = 0
                transport.side_effect = None
                transport.return_value = "Summary"
                assert await reply() == "Summary" and breaker.state == "CLOSED"
    asyncio.run(scenario())


def test_admission_before_session_checkout_and_release():
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=AsyncAdaptedQueuePool,
                                     pool_size=2, max_overflow=0, pool_timeout=0.1)
        sessions = async_sessionmaker(engine)
        saturated, release = asyncio.Event(), asyncio.Event()

        async def endpoint(scope, receive, send):
            async with sessions.begin() as session:
                await session.execute(text("SELECT 1"))
                if engine.pool.checkedout() == 2:
                    saturated.set()
                await release.wait()

        guard = FloodGuard(endpoint, 2, 1024)
        scope = {"type": "http", "headers": []}
        send = AsyncMock()
        try:
            await guard(dict(scope, headers=[(b"test", b"a" * 1024)]), AsyncMock(), send)
            assert send.await_args_list[0].args[0]["status"] == 413
            tasks = [asyncio.create_task(guard(scope, AsyncMock(), send)) for _ in range(2)]
            await asyncio.wait_for(saturated.wait(), 2)
            send.reset_mock()
            await asyncio.gather(*(guard(scope, AsyncMock(), send) for _ in range(8)))
            starts = [call.args[0] for call in send.await_args_list if "status" in call.args[0]]
            assert len(starts) == 8 and all(m["status"] == 503 for m in starts)
            assert all(dict(m["headers"])[b"retry-after"] == b"1" for m in starts)
            release.set()
            await asyncio.gather(*tasks)
            assert engine.pool.checkedout() == 0
        finally:
            release.set()
            await engine.dispose()
    asyncio.run(scenario())
