from unittest.mock import MagicMock

import pytest
from app.exceptions.configuration import ConfigurationUnavailableError
from app.services import configuration
from sqlalchemy.exc import SQLAlchemyError


@pytest.mark.parametrize("error", [ConnectionRefusedError("database is offline"), SQLAlchemyError("read failed")])
async def test_configuration_read_failure_stops_delivery_instead_of_using_environment(monkeypatch, error):
    factory = MagicMock()
    factory.return_value.__aenter__.side_effect = error
    monkeypatch.setattr(configuration, "async_session_factory", factory)
    with pytest.raises(ConfigurationUnavailableError):
        await configuration.load_configuration()
