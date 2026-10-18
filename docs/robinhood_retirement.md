# Robinhood integration removal

Robinhood is no longer an active Oracle provider or broker. The crypto worker no
longer installs its market-data bridge, pagination/quote/transport retry patches,
dynamic broker-discovered universe, provider-health hooks, broker-readiness sampler,
account-capital adapters, broker-specific paper-fill adapter, or startup preflight.
Capital readiness skips its shadow-broker tracker. The entrypoint no longer reads
or normalizes Robinhood credentials.

Configuration permanently disables this integration and does not load its API
key or private key. Legacy `ROBINHOOD_CRYPTO_ENABLED=true` and API-mode values are
ignored; the client fails before signing or network I/O with
`ROBINHOOD_INTEGRATION_REMOVED`. No Railway variable edit is required. Configured
credentials are not revoked or deleted from the external account by this change.

Historical trades, lots, fills, fees, journals, Brain/AEVE records, source labels,
and original accounting are preserved. Offline parsers and legacy adapter modules
remain available to reproduce historical evidence; they are not installed in the
active worker. No database schema, retention budget, or cleanup is changed.

## Crypto execution is unavailable; research remains independent

Robinhood was the only configured crypto broker quote authority. The worker now
installs an explicit no-execution-source guard before readiness and trading loops.
Crypto quotes, including cached Robinhood payloads or mislabeled research quotes,
cannot authorize BUY or SELL fills. No fallback source inherits the removed
broker's authority. Existing positions are not liquidated or rewritten; their
broker-dependent fills remain unavailable. The stock quote path is unchanged.

Kraken ingestion no longer requires a broker quote observer. Its existing elected,
shared ingestion service, checksum validation, snapshot quorum, symbol discovery,
stale/crossed-book protections and bounded storage remain active under their
existing opt-ins. Without an execution provider, venue comparison explicitly
reports `EXECUTION_SOURCE_NOT_CONFIGURED`, rather than a stale broker diagnostic.
Existing Kraken primary research and Brain readers continue under their existing
configuration, with execution authority NONE. No new research or trading flags
are enabled.

Paper mode and real-money disarmament remain required. This removal neither fixes
all regular historical-bar forecast abstentions nor establishes positive post-cost
expectancy. A future execution provider requires a separately validated integration
with its own exact symbols, prices, timestamps and authority.
