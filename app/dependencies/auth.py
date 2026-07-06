from typing import Annotated

from fastapi import Depends, Header, HTTPException

from app.core.settings import get_settings
from app.exceptions.auth import ApiKeyInvalidError


def verify_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    """Проверяет API-ключ служебного REST API."""

    settings = get_settings()
    expected_key = settings.api.api_key.get_secret_value()
    if not x_api_key or x_api_key != expected_key:
        error = ApiKeyInvalidError()
        raise HTTPException(status_code=error.status_code, detail=error.detail)


VerifyApiKeyDep = Annotated[None, Depends(verify_api_key)]
