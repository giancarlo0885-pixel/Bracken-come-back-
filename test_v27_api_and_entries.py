from unittest.mock import Mock

from provider_diagnostics import _classify_http


def _response(code: int, text: str = ""):
    response = Mock()
    response.status_code = code
    response.text = text
    return response


def test_http_402_is_plan_limited():
    result = _classify_http("TEST_API_KEY", _response(402, "payment required"), 12.0)
    assert result.status == "plan_limited"
    assert result.capability == "limited"


def test_http_200_soft_rate_limit_is_not_healthy():
    result = _classify_http("TEST_API_KEY", _response(200, '{"Note":"API call frequency limit reached"}'), 12.0)
    assert result.status == "rate_limited"


def test_http_200_soft_invalid_key_is_not_healthy():
    result = _classify_http("TEST_API_KEY", _response(200, '{"Error Message":"Invalid API key"}'), 12.0)
    assert result.status == "invalid_key"
