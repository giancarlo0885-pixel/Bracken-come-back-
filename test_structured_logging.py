from __future__ import annotations

import json
import logging

from structured_logging import StructuredFormatter


def _payload(message: str, **extra):
    record = logging.LogRecord("unit", logging.INFO, __file__, 1, message, (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return json.loads(StructuredFormatter().format(record))


def test_structured_logging_extracts_common_key_value_fields():
    payload = _payload(
        "Provider route | market=cash | symbol=AAPL | provider=Google RSS | "
        "scan_type=fast | execution_enabled=false"
    )
    assert payload["market"] == "cash"
    assert payload["symbol"] == "AAPL"
    assert payload["provider"] == "Google RSS"
    assert payload["scan_type"] == "fast"
    assert payload["execution_enabled"] == "false"


def test_structured_logging_explicit_fields_win_over_message_fallbacks():
    payload = _payload("market=cash | symbol=AAPL", market="crypto", symbol="BTC-USD")
    assert payload["market"] == "crypto"
    assert payload["symbol"] == "BTC-USD"
