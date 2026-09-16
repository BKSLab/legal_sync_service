class ConfigurationConflictError(Exception):
    """Оператор пытается сохранить устаревшую форму конфигурации."""


class ConfigurationUnavailableError(Exception):
    """При недоступных настройках отправка должна останавливаться."""

    detail = "Не удалось прочитать конфигурацию сервиса. Отправка не выполняется."
    status_code = 503
