from collections.abc import Callable
from typing import Any

import httpx
import pytest


@pytest.fixture
def mock_routing_http(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable[[httpx.Request], httpx.Response]], list[httpx.Request]]:
    """Replace provider HTTP clients with an in-process httpx transport."""

    def install(handler: Callable[[httpx.Request], httpx.Response]) -> list[httpx.Request]:
        requests: list[httpx.Request] = []
        transport = httpx.MockTransport(lambda request: _handle_request(request, handler, requests))
        original_async_client = httpx.AsyncClient

        class MockedAsyncClient:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs["transport"] = transport
                self._client = original_async_client(*args, **kwargs)

            async def __aenter__(self) -> httpx.AsyncClient:
                return await self._client.__aenter__()

            async def __aexit__(self, *args: Any) -> None:
                await self._client.__aexit__(*args)

        monkeypatch.setattr(httpx, "AsyncClient", MockedAsyncClient)
        return requests

    return install


def _handle_request(
    request: httpx.Request,
    handler: Callable[[httpx.Request], httpx.Response],
    requests: list[httpx.Request],
) -> httpx.Response:
    requests.append(request)
    return handler(request)
