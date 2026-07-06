from fastapi import status


class ApiKeyInvalidError(Exception):
    """Неверный API-ключ."""

    status_code = status.HTTP_401_UNAUTHORIZED
    detail = "Неверный API-ключ."
