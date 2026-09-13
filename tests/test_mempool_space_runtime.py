from __future__ import annotations

import io
import json
from types import SimpleNamespace

import mempool_space_runtime as runtime
from mempool_space_client import MempoolSpaceClient, MempoolSpaceError, MempoolSpaceSnapshot


class _Response:
    def __init__(self, body: str, status: int = 200):
        self.body = body.encode("utf-8")
        self.status = status

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _opener_factory(payloads):
    def opener(request, timeout=5.0):
        path = request.full_url.split("mempool.space", 1)[-1]
        return _Response(payloads[path])
    return opener


def test_client_builds_observed_snapshot_from_public_endpoints():
    hashrates = [{"timestamp": i, "avgHashrate": 100.0 + i} for i in range(20)]
    payloads = {
        "/api/blocks/tip/height": "900000",
        "/api/v1/mining/hashrate/1m": json.dumps({
            "hashrates": hashrates,
            "currentHashrate": 123456789.0,
            "currentDifficulty": 987654.0,
        }),
        "/api/v1/difficulty-adjustment": json.dumps({
            "difficultyChange": 5.0,
            "timeAvg": 570000,
        }),
        "/api/mempool": json.dumps({
            "count": 50000,
            "vsize": 25000000,
            "total_fee": 123456,
        }),
        "/api/v1/fees/recommended": json.dumps({
            "fastestFee": 25,
            "halfHourFee": 20,
            "hourFee": 15,
            "economyFee": 5,
            "minimumFee": 1,
        }),
    }
    client = MempoolSpaceClient(opener=_opener_factory(payloads))
    snapshot = client.snapshot()

    assert snapshot.block_height == 900000
    assert snapshot.current_hashrate == 123456789.0
    assert snapshot.current_difficulty == 987654.0
    assert snapshot.difficulty_change == 0.05
    assert snapshot.block_interval_seconds == 570.0
    assert snapshot.mempool_count == 50000
    assert snapshot.mempool_pressure == 0.25
    assert snapshot.fee_pressure is not None and 0.0 < snapshot.fee_pressure <= 1.0
    assert snapshot.hash_rate_change is not None and snapshot.hash_rate_change > 0


def _snapshot() -> MempoolSpaceSnapshot:
    return MempoolSpaceSnapshot(
        observed_at=123.0,
        source="mempool.space",
        block_height=900000,
        current_hashrate=1.0e21,
        current_difficulty=1.2e14,
        hash_rate_change=0.05,
        difficulty_change=0.02,
        block_interval_seconds=590.0,
        mempool_count=50000,
        mempool_vsize=25000000.0,
        mempool_total_fee=123456.0,
        fastest_fee_sat_vb=20.0,
        half_hour_fee_sat_vb=15.0,
        hour_fee_sat_vb=10.0,
        economy_fee_sat_vb=3.0,
        minimum_fee_sat_vb=1.0,
        fee_pressure=0.4,
        mempool_pressure=0.25,
    )


class _Client:
    def __init__(self, snapshot=None, error=None):
        self.value = snapshot
        self.error = error
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.value


def test_runtime_attaches_context_only_to_btc_and_does_not_change_action(monkeypatch):
    runtime._CACHE = None
    runtime._CACHE_FETCHED_AT = 0.0
    client = _Client(snapshot=_snapshot())

    def analyze_market(symbol, history, sentiment=0.0):
        return SimpleNamespace(symbol=symbol, action="BUY", score=0.61)

    worker = SimpleNamespace(analyze_market=analyze_market)
    assert runtime.install_mempool_space_network_context(worker, client=client)

    eth = worker.analyze_market("ETH-USD", object(), 0.0)
    assert eth.action == "BUY"
    assert not hasattr(eth, "btc_network_source")
    assert client.calls == 0

    btc = worker.analyze_market("BTC-USD", object(), 0.0)
    assert btc.action == "BUY"
    assert btc.score == 0.61
    assert btc.btc_network_source == "mempool.space"
    assert btc.btc_network_execution_impact == "NONE"
    assert btc.btc_network_block_height == 900000
    assert btc.btc_network_security_score != 0.0
    assert client.calls == 1


def test_runtime_uses_cache_and_fails_soft_when_provider_is_unavailable(monkeypatch):
    runtime._CACHE = None
    runtime._CACHE_FETCHED_AT = 0.0
    client = _Client(snapshot=_snapshot())
    first = runtime._snapshot(client)
    assert first is not None
    assert client.calls == 1

    # Cache prevents repeated public API requests within the TTL.
    again = runtime._snapshot(client)
    assert again is first
    assert client.calls == 1

    # With no cache, an outage is missing evidence rather than a fabricated value.
    runtime._CACHE = None
    runtime._CACHE_FETCHED_AT = 0.0
    down = _Client(error=MempoolSpaceError("HTTP_429"))
    assert runtime._snapshot(down) is None
