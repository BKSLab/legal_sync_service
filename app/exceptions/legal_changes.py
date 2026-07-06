from fastapi import status


class LegalChangeServiceError(Exception):
    """Базовое исключение сервиса событий изменений."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, error_details: str):
        self.error_details = error_details
        super().__init__(self.error_details)

    def __str__(self) -> str:
        return f"Ошибка сервиса событий изменений. Подробности: {self.error_details}"

    @property
    def detail(self) -> str:
        return "Ошибка при обработке события изменения."


class LegalChangeRepositoryError(Exception):
    """Исключение репозитория событий изменений."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, error_details: str):
        self.error_details = error_details
        super().__init__(self.error_details)

    def __str__(self) -> str:
        return f"Ошибка репозитория событий изменений. Подробности: {self.error_details}"


class LegalChangeNotFoundError(Exception):
    """Событие изменения не найдено."""

    status_code = status.HTTP_404_NOT_FOUND

    def __init__(self, change_id: int):
        self.change_id = change_id
        super().__init__(self.change_id)

    def __str__(self) -> str:
        return f"Событие изменения не найдено: {self.change_id}"

    @property
    def detail(self) -> str:
        return f"Событие изменения не найдено: {self.change_id}"


class LegalChangeInvalidStatusError(Exception):
    """Операция невозможна для текущего статуса события."""

    status_code = status.HTTP_409_CONFLICT

    def __init__(self, change_id: int, current_status: str, expected_status: str):
        self.change_id = change_id
        self.current_status = current_status
        self.expected_status = expected_status
        super().__init__(self.change_id, self.current_status, self.expected_status)

    def __str__(self) -> str:
        return (
            f"Недопустимый статус события {self.change_id}: {self.current_status}. "
            f"Ожидался: {self.expected_status}"
        )

    @property
    def detail(self) -> str:
        return (
            f"Операция невозможна для статуса {self.current_status}. "
            f"Ожидался статус {self.expected_status}."
        )
