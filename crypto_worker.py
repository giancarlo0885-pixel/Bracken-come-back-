import logging
import os

import market_worker
from binance_us_adapter import install_binance_us_reference_fallback
from capital_readiness_runtime import prepare_capital_readiness_runtime
from core_rebalance_observability import install_core_rebalance_observability
from core_rebalance_optimizer_trace import install_core_rebalance_optimizer_trace
from core_rebalance_score_compat import install_core_rebalance_score_compat
from crypto_execution_guard import install_crypto_execution_quote_guard
from crypto_forecast_runtime import install_crypto_short_horizon_forecast
from crypto_pipeline_integrity_runtime import install_crypto_pipeline_integrity_runtime
from crypto_v39_risk_bridge import install_crypto_v39_risk_bridge
from crypto_v39_spread_bridge import install_crypto_v39_spread_bridge
from current_model_readiness_fix import install_current_model_readiness_fix
from event_opportunity_runtime import install_event_opportunity_runtime
from massive_crypto_websocket import install_massive_crypto_websocket
from migration_runtime_safety import install_migration_runtime_safety
from optimizer_repair_runtime import install_optimizer_repairs
from paper_accounting_reconciliation_runtime import emit_paper_accounting_reconciliation
from paper_autonomous_learning import install_paper_autonomous_learning
from paper_core_rebalance_qualification import install_paper_core_rebalance_qualification
from paper_crypto_churn_guard import install_paper_crypto_churn_guard
from paper_crypto_learning_relaxation import install_paper_crypto_learning_relaxation
from paper_execution_accounting import install_paper_execution_accounting
from paper_execution_reality import install_paper_execution_reality
from paper_fee_policy import install_fee_aware_fifo_policy
from paper_lifecycle_observability import install_paper_lifecycle_observability
from paper_optimizer_size_handoff import install_paper_optimizer_size_handoff
from paper_regime_economics_shadow import install_paper_regime_economics_shadow
from paper_regime_entry_provenance import install_paper_regime_entry_provenance
from paper_shadow_exit_challenger import install_paper_shadow_exit_challenger
from paper_sell_db_verification import emit_recent_crypto_sell_db_verification
from paper_sell_execution_router import install_paper_sell_execution_router
from paper_sell_lot_atomic_repair import install_atomic_paper_sell_lot_repair
from paper_strategy_economics import install_paper_strategy_economics
from paper_strategy_execution_guard import install_paper_strategy_execution_guard
from paper_weekly_risk_period_runtime import install_paper_weekly_risk_period
from readiness_observability import emit_capital_readiness_report
from runtime_integrity_patch import install_runtime_integrity_patch
from runtime_provider_reliability import install_yahoo_runtime_reliability
from selective_model_leakage_fix import install_selective_model_leakage_fix
from strategic_core_rebalance_runtime import install_strategic_core_rebalance_producer
from strategic_rebalance_optimizer_bridge import install_strategic_rebalance_optimizer_bridge
from structured_logging import configure_structured_logging


configure_structured_logging(os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("crypto-worker")

install_migration_runtime_safety()
install_yahoo_runtime_reliability()
install_runtime_integrity_patch(market_worker)
install_event_opportunity_runtime(market_worker)
install_paper_autonomous_learning()
install_paper_crypto_learning_relaxation()
install_core_rebalance_score_compat()
install_strategic_core_rebalance_producer(market_worker)
install_binance_us_reference_fallback()
# Crypto research remains active; broker submission stays unavailable.
install_crypto_v39_spread_bridge(market_worker)
install_crypto_execution_quote_guard(market_worker)
install_crypto_short_horizon_forecast(market_worker)
install_crypto_v39_risk_bridge(market_worker)
install_paper_core_rebalance_qualification(market_worker)
install_strategic_rebalance_optimizer_bridge(market_worker)
# Synchronize planning with the same paper account limits execution enforces,
# normalize V39 evidence fields, explain universe shrinkage, and clearly label
# historical paper references before trace/observability wrappers capture them.
install_crypto_pipeline_integrity_runtime(market_worker)
install_core_rebalance_optimizer_trace(market_worker)
install_core_rebalance_observability(market_worker)
# Install before prepare_capital_readiness_runtime() so freshly generated model
# evidence uses the abstention-aware temporal causality probe and readiness judges
# only the latest validation generation for each symbol.
install_selective_model_leakage_fix()
install_current_model_readiness_fix()
if os.getenv("EXECUTION_MODE", "paper").strip().lower() == "paper":
    # Weekly risk must reset at the start of the current UTC calendar week rather
    # than carrying prior-week losses forward in a rolling seven-day window.
    # The configured loss threshold itself is unchanged.
    install_paper_weekly_risk_period()
    install_paper_execution_reality(market_worker)
    install_paper_execution_accounting(market_worker)
    install_fee_aware_fifo_policy()
    # Must run after fee-aware attribution is installed so the repair wraps the
    # final SELL attribution path and shares the caller's database transaction.
    install_atomic_paper_sell_lot_repair(market_worker)
    # Prevent fast generic SELL noise from immediately reversing a fresh paper
    # entry. Protective EXIT/CLOSE/risk-exit paths remain unrestricted.
    install_paper_crypto_churn_guard(market_worker)
    # The SELL router must be inside the lifecycle observer. This lets the
    # observer see both routed exits and optimized entries in the same returned
    # action list; the previous order made genuine SELLs appear as sells=0.
    install_paper_sell_execution_router(market_worker)
    install_paper_lifecycle_observability(market_worker)

# Massive remains a surveillance/reference feed only; it has no execution authority.
install_massive_crypto_websocket(market_worker)

# Install last so the optimizer repair sees the final wrapped execution chain.
# The module self-disables unless execution is paper-only and live submission is disarmed.
install_optimizer_repairs(market_worker)
# Then make the V39 optimizer-approved paper notional authoritative for ordinary
# unbounded learning entries, instead of letting the minimum-sample fallback
# collapse approved allocations to $2. This remains strictly paper-only.
install_paper_optimizer_size_handoff()
# Economic learning is deliberately outside the optimizer handoff: it observes
# canonical closed-trade attribution, keeps a post-fix epoch, scales paper size
# from net expectancy after costs, and rejects only explicit edges that cannot
# clear estimated round-trip costs. Positive size expansion additionally requires
# current walk-forward/calibration/leakage governance to pass.
install_paper_strategy_economics()
# Preserve raw entry-time regime evidence when upstream sends an explicit empty
# feature snapshot. This changes provenance only; execution behavior is untouched.
install_paper_regime_entry_provenance()
# Shadow-only regime segmentation plus forward price-path sampling for MFE/MAE.
# This writes telemetry only and has no execution/sizing/cooldown effect.
install_paper_regime_economics_shadow()
# Observe the incremental value of a persistent forward-evidence exit without
# changing Council, risk exits, sizing, cooldowns, or broker submission.
install_paper_shadow_exit_challenger("crypto")
install_paper_strategy_execution_guard()

if __name__ == "__main__":
    from crypto_execution_source import install_unavailable_crypto_execution_source
    from coinbase_paper_pricing import install_paper_pricing
    if not install_paper_pricing(market_worker):
        install_unavailable_crypto_execution_source(worker=market_worker)
    logger.info("CRYPTO BROKER | provider=NONE | robinhood=REMOVED | broker_submission=NONE | execution_authority=PAPER_ONLY_OR_UNAVAILABLE")
    prepare_capital_readiness_runtime(market_worker, "crypto")
    from kraken_reference_runtime import install_kraken_reference
    install_kraken_reference(market_worker)
    from kraken_primary_research import PrimaryResearch, enabled as kraken_research_enabled
    if kraken_research_enabled():
        market_worker._kraken_primary_research = PrimaryResearch()
        market_worker._kraken_primary_research.start()
    emit_capital_readiness_report(logger)
    if os.getenv("EXECUTION_MODE", "paper").strip().lower() == "paper":
        emit_recent_crypto_sell_db_verification()
        emit_paper_accounting_reconciliation("crypto")
        from paper_trade_evidence_audit import emit_trade_evidence_audit
        emit_trade_evidence_audit("crypto")
    market_worker.run_worker("crypto")
