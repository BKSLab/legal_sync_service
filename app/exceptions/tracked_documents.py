from fastapi import status


class TrackedDocumentServiceError(Exception):
    """Базовое исключение сервиса отслеживаемых документов."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, error_details: str):
        self.error_details = error_details
        super().__init__(self.error_details)

    def __str__(self) -> str:
        return f"Ошибка сервиса отслеживаемых документов. Подробности: {self.error_details}"

    @property
    def detail(self) -> str:
        return "Ошибка при обработке отслеживаемого документа."


class TrackedDocumentRepositoryError(Exception):
    """Исключение репозитория отслеживаемых документов."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, error_details: str):
        self.error_details = error_details
        super().__init__(self.error_details)

    def __str__(self) -> str:
        return f"Ошибка репозитория отслеживаемых документов. Подробности: {self.error_details}"


class TrackedDocumentNotFoundError(Exception):
    """Документ не найден."""

    status_code = status.HTTP_404_NOT_FOUND

    def __init__(self, document_id: int | str):
        self.document_id = document_id
        super().__init__(self.document_id)

    def __str__(self) -> str:
        return f"Отслеживаемый документ не найден: {self.document_id}"

    @property
    def detail(self) -> str:
        return f"Отслеживаемый документ не найден: {self.document_id}"


class TrackedDocumentAlreadyExistsError(Exception):
    """Документ с таким идентификатором уже существует."""

    status_code = status.HTTP_409_CONFLICT

    def __init__(self, document_id: str):
        self.document_id = document_id
        super().__init__(self.document_id)

    def __str__(self) -> str:
        return f"Отслеживаемый документ уже существует: {self.document_id}"

    @property
    def detail(self) -> str:
        return f"Отслеживаемый документ уже существует: {self.document_id}"
