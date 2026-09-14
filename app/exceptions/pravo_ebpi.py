from fastapi import status


class PravoEbpiClientError(Exception):
    """Базовое исключение клиента банка редакций actual.pravo.gov.ru."""

    status_code = status.HTTP_502_BAD_GATEWAY

    def __init__(self, error_details: str):
        self.error_details = error_details
        super().__init__(self.error_details)

    def __str__(self) -> str:
        return f"Ошибка клиента pravo ebpi. Подробности: {self.error_details}"

    @property
    def detail(self) -> str:
        return "Источник консолидированных редакций недоступен."


class PravoEbpiRequestError(PravoEbpiClientError):
    """Запрос к порталу не удался после всех повторных попыток."""

    def __str__(self) -> str:
        return f"Запрос к pravo ebpi не выполнен. Подробности: {self.error_details}"


class PravoEbpiResponseError(PravoEbpiClientError):
    """Портал ответил успешно, но структура ответа не соответствует ожидаемой.

    Контракт API недокументирован, поэтому расхождение структуры — это явная
    ошибка, а не повод молча разобрать ответ частично и отправить в RAG
    неполный юридический текст.
    """

    def __str__(self) -> str:
        return f"Неожиданная структура ответа pravo ebpi. Подробности: {self.error_details}"


class PravoEbpiDocumentNotFoundError(PravoEbpiClientError):
    """Документ не найден в банке правовых актов."""

    status_code = status.HTTP_404_NOT_FOUND

    def __init__(self, search_key: str):
        self.search_key = search_key
        super().__init__(search_key)

    def __str__(self) -> str:
        return f"Документ не найден в банке правовых актов: {self.search_key}"

    @property
    def detail(self) -> str:
        return f"Документ не найден в банке правовых актов: {self.search_key}"
