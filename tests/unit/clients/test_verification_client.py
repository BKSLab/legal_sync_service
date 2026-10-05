import json

import httpx
import pytest
from app.clients.verification import (
    VerificationClient,
    VerificationInvalid,
    VerificationUnavailable,
)
from app.core.settings import VerificationSettings
from tests.automation_fixtures import report


async def review(payload, status=200, **settings):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status,json=payload))) as client:
        return await VerificationClient(client, VerificationSettings(_env_file=None, verification_api_key="test", **settings)).review({"law":"text"}, "test-model")


async def test_valid_structured_response_is_parsed():
    result, metadata = await review({"choices":[{"finish_reason":"stop","message":{"content":report().model_dump_json()}}],"usage":{"total_tokens":100}})
    assert result.verdict == "pass" and metadata["usage"] == {"total_tokens":100}


async def test_provider_schema_is_compatible_but_response_validation_remains_strict():
    invalid = report().model_dump(mode="json")
    invalid["expected_articles"][0]["law_quote"] = "x"

    def handle(request):
        schema = json.loads(request.content)["response_format"]["json_schema"]["schema"]
        serialized = json.dumps(schema)
        assert "$ref" not in serialized and "minLength" not in serialized and '"format"' not in serialized
        assert schema["additionalProperties"] is False
        assert schema["properties"]["expected_articles"]["items"]["additionalProperties"] is False
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(invalid)}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(VerificationInvalid):
            await VerificationClient(client, VerificationSettings(_env_file=None, verification_api_key="test")).review({}, "test-model")


@pytest.mark.parametrize("payload", [None, {}, {"choices":[]}, {"choices":[{"finish_reason":"length","message":{"content":"{}"}}]},
    {"choices":[{"finish_reason":"stop","message":{"refusal":"no","content":"{}"}}]},
    {"choices":[{"finish_reason":"stop","message":{"content":json.dumps({"verdict":"pass"})}}]}])
async def test_partial_or_refused_response_is_an_exception(payload):
    with pytest.raises(VerificationInvalid):
        await review(payload)


@pytest.mark.parametrize("status", [429,500,503])
async def test_only_transient_provider_errors_can_retry(status):
    with pytest.raises(VerificationUnavailable):
        await review({},status)


async def test_invalid_key_and_oversized_input_are_not_silently_ignored():
    with pytest.raises(VerificationInvalid):
        await review({},401)
    with pytest.raises(VerificationInvalid, match="не обрезан"):
        await review({},verification_max_input_characters=1)
