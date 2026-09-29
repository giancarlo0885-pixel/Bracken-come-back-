from types import SimpleNamespace

import paper_regime_entry_provenance as provenance


class OracleStub:
    @staticmethod
    def signal_value(signal, key, default=None):
        if isinstance(signal, dict):
            return signal.get(key, default)
        return getattr(signal, key, default)

    @staticmethod
    def feature_vector(signal):
        return {}


def test_entry_pattern_is_preserved_in_immutable_entry_features():
    signal = {
        "entry_pattern": "dip_rebound",
        "schwager_pattern_tag": "range_breakout_up",
        "dip_depth_pct": 0.021,
        "rebound_pct": 0.008,
    }
    features = provenance._enrich_entry_features(OracleStub, signal, {})
    assert features["entry_pattern"] == "dip_rebound"
    assert features["schwager_pattern_tag"] == "range_breakout_up"
    assert features["dip_depth_pct"] == 0.021
    assert features["rebound_pct"] == 0.008


def test_existing_entry_pattern_provenance_is_not_overwritten():
    signal = {"entry_pattern": "momentum_breakout"}
    features = provenance._enrich_entry_features(
        OracleStub, signal, {"entry_pattern": "dip_rebound"}
    )
    assert features["entry_pattern"] == "dip_rebound"


def test_signal_payload_carries_pattern_for_forward_attribution():
    signal = SimpleNamespace(entry_pattern="trend_continuation", schwager_pattern_tag="")
    payload = provenance._enrich_persisted_signal_payload(signal, {})
    assert payload["entry_pattern"] == "trend_continuation"
