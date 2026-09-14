from fastapi import status


class RedactionParseError(Exception):
    """Редакцию не удалось разобрать.

    Разметка портала недокументирована и может измениться. Явная ошибка здесь
    предпочтительнее частичного разбора: неполный юридический текст, молча
    ушедший в RAG, обнаружить намного сложнее, чем упавшую задачу.
    """

    status_code = status.HTTP_502_BAD_GATEWAY

    def __init__(self, error_details: str):
        self.error_details = error_details
        super().__init__(self.error_details)

    def __str__(self) -> str:
        return f"Ошибка разбора редакции. Подробности: {self.error_details}"

    @property
    def detail(self) -> str:
        return "Не удалось разобрать редакцию документа."


class RedactionSectionNotFoundError(Exception):
    """Статья не найдена в редакции документа."""

    status_code = status.HTTP_404_NOT_FOUND

    def __init__(self, section_number: str):
        self.section_number = section_number
        super().__init__(self.section_number)

    def __str__(self) -> str:
        return f"Статья не найдена в редакции: {self.section_number}"

    @property
    def detail(self) -> str:
        return f"Статья не найдена в редакции документа: {self.section_number}"
