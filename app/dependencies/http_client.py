from collections.abc import AsyncGenerator
from typing import Annotated

import httpx
from fastapi import Depends


async def get_http_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Возвращает HTTP-клиент на время запроса."""

    async with httpx.AsyncClient() as client:
        yield client


HTTPClientDep = Annotated[httpx.AsyncClient, Depends(get_http_client)]
