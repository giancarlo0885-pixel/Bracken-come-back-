from __future__ import annotations

import sys
import types

# Lightweight psycopg stub for test environments where PostgreSQL drivers are absent.
if "psycopg" not in sys.modules:
    psycopg = types.ModuleType("psycopg")
    psycopg.Connection = object
    psycopg.connect = lambda *args, **kwargs: None
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = object()
    sys.modules["psycopg"] = psycopg
    sys.modules["psycopg.rows"] = rows

if "yfinance" not in sys.modules:
    yf = types.ModuleType("yfinance")
    yf.download = lambda *args, **kwargs: None
    sys.modules["yfinance"] = yf

import pandas as pd

import global_market_scanner as scanner


def sample_history() -> pd.DataFrame:
    return pd.DataFrame({
        "Open": [100,101,102,103,104,105],
        "High": [102,103,104,105,107,111],
        "Low": [99,100,101,102,103,104],
        "Close": [101,102,103,104,106,110],
        "Volume": [1_000_000,1_050_000,1_100_000,1_000_000,1_200_000,2_500_000],
    })


def test_yahoo_symbol_mapping():
    assert scanner._to_yahoo_symbol("7203", "TSE") == "7203.T"
    assert scanner._to_yahoo_symbol("700", "HK") == "0700.HK"
    assert scanner._to_yahoo_symbol("SAP", "XETRA") == "SAP.DE"


def test_candidate_metrics_detects_liquid_mover(monkeypatch):
    monkeypatch.setattr(scanner, "get_history", lambda *args, **kwargs: sample_history())
    candidate = scanner._candidate_metrics({"symbol":"SAP.DE","name":"SAP","exchange":"XETRA","region":"Europe","sector":"Technology"})
    assert candidate is not None
    assert candidate.mover_score > 0
    assert candidate.relative_volume > 1


def test_seed_universe_is_worldwide(monkeypatch):
    monkeypatch.setattr(scanner, "EODHD_API_KEY", "")
    universe = scanner._load_universe()
    regions = {item["region"] for item in universe}
    assert "Europe" in regions
    assert "Japan" in regions
    assert "India" in regions
    assert "Latin America" in regions
