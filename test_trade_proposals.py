from __future__ import annotations

from types import SimpleNamespace

import shadow_broker_runtime as runtime
import trade_proposals


class Client:
    def best_bid_ask_quotes(self, *symbols):
        assert symbols == ("BTC-USD",)
        return [{"symbol": "BTC-USD", "bid": "99990", "ask": "100010"}]

    def estimated_price(self, symbol, side, quantity):
        assert symbol == "BTC-USD"
        assert side == "buy"
        assert float(quantity) == 0.01
        return [{"symbol": symbol, "side": side, "price": "100008"}]


def test_signal_context_keeps_strategy_and_risk_evidence():
    signal = SimpleNamespace(
        symbol="BTC-USD",
        strategy="mean_reversion",
        reason="15-minute downside displacement",
        score=82,
        confidence=0.79,
        risk_reward_ratio=2.4,
        target_price=103000,
        stop_loss=98500,
        mean_reversion_zscore=-1.8,
    )
    context = runtime._proposal_context([signal])["BTC-USD"]
    assert context["strategy"] == "mean_reversion"
    assert context["risk_reward_ratio"] == 2.4
    assert context["mean_reversion_zscore"] == -1.8


def test_capture_creates_proposal_from_verified_shadow_fill(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ROBINHOOD_CRYPTO_ENABLED", "true")
    monkeypatch.setattr(
        runtime,
        "_recent_untracked_fills",
        lambda limit=25: [
            {
                "fill_id": "fill-1",
                "order_id": "order-1",
                "symbol": "BTC-USD",
                "side": "BUY",
                "quantity": 0.01,
                "reference_price": 100000,
                "fill_price": 100005,
                "quote_provider": "Coinbase",
                "quote_timestamp": "2026-09-01T18:00:00+00:00",
                "order_reason": "entry",
                "requested_notional": 1000,
                "fee_pct": 0.001,
                "slippage_pct": 0.002,
                "spread_pct": 0.003,
                "market_impact_pct": 0.001,
                "liquidity_value": 1000000,
                "participation_rate": 0.0001,
            }
        ],
    )
    captured = {}

    def record_shadow_order(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(runtime, "record_shadow_order", record_shadow_order)
    result = runtime.capture_recent_paper_fills(
        Client(),
        proposal_context={
            "BTC-USD": {
                "strategy": "mean_reversion",
                "reason": "short-horizon reversal",
                "risk_reward_ratio": 2.0,
            }
        },
    )

    assert result["captured"] == 1
    assert captured["proposal_id"] == "proposal:fill-1"
    assert captured["payload"]["proposal_status"] == "AWAITING_HUMAN_APPROVAL"
    assert captured["payload"]["human_approval_required"] is True
    assert captured["payload"]["submission_allowed"] is False
    assert captured["payload"]["strategy"] == "mean_reversion"
    assert captured["payload"]["risk_reward_ratio"] == 2.0
    assert captured["payload"]["broker_estimated_price"][0]["price"] == "100008"


def test_trade_proposal_query_normalizes_review_fields(monkeypatch):
    monkeypatch.setattr(
        trade_proposals,
        "rows",
        lambda *args, **kwargs: [
            {
                "proposal_id": "proposal:fill-1",
                "shadow_order_id": "shadow-1",
                "paper_fill_id": "fill-1",
                "decision_id": "decision-1",
                "symbol": "BTC-USD",
                "side": "BUY",
                "quantity": 0.01,
                "notional": 1000.10,
                "oracle_reference_price": 100000,
                "paper_fill_price": 100005,
                "broker_bid": 99990,
                "broker_ask": 100010,
                "broker_mid": 100000,
                "broker_spread_pct": 0.02,
                "hypothetical_fill_price": 100010,
                "shadow_status": "OPEN",
                "payload": {
                    "proposal_status": "AWAITING_HUMAN_APPROVAL",
                    "strategy": "mean_reversion",
                    "reason": "short-horizon reversal",
                    "risk_reward_ratio": 2.0,
                    "human_approval_required": True,
                    "submission_allowed": False,
                },
                "created_at": "2026-09-01T18:00:00+00:00",
            }
        ],
    )
    proposal = trade_proposals.list_trade_proposals(limit=10)[0]
    assert proposal["proposal_id"] == "proposal:fill-1"
    assert proposal["strategy"] == "mean_reversion"
    assert proposal["risk_reward_ratio"] == 2.0
    assert proposal["human_approval_required"] is True
    assert proposal["submission_allowed"] is False
