# Coinbase public-depth PAPER pricing — research candidate

This change **does not** activate crypto paper fills. The existing `crypto_execution_source.py` fail-closed guard remains unchanged, as do paper mode, broker submission disabled and live trading disarmed.

The new `paper_coinbase_execution.py` fetches public Coinbase Exchange level-2 depth and ticker without credentials, rejects stale, future, crossed, locked, auction-mode, insufficient-depth, wide-spread, misaligned and malformed books, then sweeps visible depth for a conservative price. Estimated 0.6% taker fees and 5 bps adverse slippage are explicit modeling assumptions, not verified account costs.

The original book/sequence/ticker-time and calculated economic fields form an internally hashed receipt. SHA-256 is not a signature authenticating Coinbase. Coinbase REST level-2 depth lacks a book-specific exchange timestamp, so public ticker alignment and bounded local fetch time do not eliminate sequencing races.

An **optional** receipt in future crypto quote metadata is replayed against exact side, symbol, quantity, price and fees and committed into the additive `paper_fill_pricing_books` table in the same transaction as trade, fill, order and FIFO lot links. Any invalid receipt or failed insert aborts the transaction. Legacy paper fills remain unchanged; the optional receipt **never grants execution authority**.

Full activation remains blocked. Before paper execution is authorized, independent review must validate sequenced book freshness, realistic fee/slippage assumptions, final execution/risk veto, atomic rollback, idempotency and out-of-sample forward economics. Original Kraken forecasts and abstentions must enter Brain strictly as research with real source IDs; no invented forecasts or completed trades.

Unit tests: `test_paper_coinbase_execution.py`. Local-test-database PostgreSQL rollback test: `test_coinbase_paper_pricing_pg.py`; included in exact-head CI. No destructive database operation or production configuration change is part of this draft.
