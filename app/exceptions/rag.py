from fastapi import status


class RagClientError(Exception):
    """RAG Service недоступен или ответил ошибкой на своей стороне.

    Такой отказ имеет смысл повторить: он относится к доставке, а не к
    содержимому отправленной статьи.
    """

    status_code = status.HTTP_502_BAD_GATEWAY

    def __init__(self, error_details: str):
        self.error_details = error_details
        super().__init__(self.error_details)

    def __str__(self) -> str:
        return f"Ошибка обращения к RAG Service. Подробности: {self.error_details}"

    @property
    def detail(self) -> str:
        return "RAG Service недоступен."


class RagRejectedError(RagClientError):
    """RAG Service отклонил переданную статью.

    Повторять такую отправку бессмысленно: тот же текст с теми же
    метаданными будет отклонён снова, событие требует вмешательства оператора.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY

    def __str__(self) -> str:
        return f"RAG Service отклонил статью. Подробности: {self.error_details}"

    @property
    def detail(self) -> str:
        return "RAG Service отклонил переданную статью."


class RagStaleRevisionError(RagRejectedError):
    """В RAG уже есть более поздняя редакция; событие больше не требуется."""

    def __init__(self, response: dict):
        self.response = response
        super().__init__(str(response["detail"]["message"]))
