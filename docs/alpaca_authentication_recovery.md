# Alpaca credential rotation recovery

Alpaca account API keys authenticate IEX data at `https://data.alpaca.markets` using
`APCA_API_KEY_ID` and `APCA_API_SECRET_KEY`. Paper and live account keys use this
same market-data host. The paper trading host is not a market-data endpoint.
Broker OAuth/client credentials are not interchangeable with account API keys.
The IEX adapter pins that host and feed, ignores legacy URL/feed overrides and
does not follow HTTP redirects. Configuration diagnostics expose only whether
those overrides were ignored, never their potentially sensitive values.

An HTTP401 starts the existing shared authentication cooldown. Previously a
replacement pair inherited that cooldown and could not demonstrate recovery.
After stock-worker database bootstrap, `recover_alpaca_authentication()` now
performs a bounded recovery check when an old authentication cooldown exists.

The check:

- Requires paper mode, broker submission disabled and live trading disarmed.
- Uses the pinned canonical data host and IEX feed, with configured credentials and supported
  daily-history capability.
- Accepts only the exact persisted legacy HTTP401 reason. It preserves unknown,
  rate-limit and recently updated cooldowns. A recent update can hide a shorter
  HTTP429 underneath a longer HTTP401, so that case remains blocked.
  The timestamps must also match the original configured authentication duration;
  ambiguous legacy windows remain blocked even after the usual rate-limit wait.
- Uses a private HMAC identity and an advisory lock to commit at most one attempt
  per pair across workers, restarts and UTC dates. Neither credentials nor the
  identity are logged. Claims survive interruption after committing.
- Reserves both existing provider-wide and history quota before making one
  read-only MSFT daily-bar request. Exhausted quota remains exhausted. Pagination
  is capped at one HTTP request; an incomplete response cannot qualify.
- Requires nonempty, symbol-matched, timestamped, finite and consistent IEX bars.
  These probe bars are never handed to execution or treated as a live bid/ask.
- Expires only the unchanged original authentication cooldown records after that
  validation, using locked snapshot comparisons and conditional updates. Any
  intervening cooldown change prevents recovery from clearing it.
- Preserves old cooldowns on invalid/empty data or request/database failures.
  HTTP401 and HTTP429 responses reapply their existing protection durations.

`alpaca_auth_recovery_receipts` retains the committed claim, original cooldown
snapshots, quota receipts and result. It is protected from automatic retention.
No trade, fill, lot, Brain, AEVE, model or profitability evidence is rewritten.

The log prefix is `ALPACA AUTH RECOVERY`. `AUTHENTICATED_IEX` means the configured
pair returned validated IEX bars. It does not authorize trading or establish
profitability. `AUTHENTICATION_FAILED` with HTTP401 means the checked pair was
rejected; replace both values securely in Railway with a matching account API key
pair. Never paste keys into a chat, issue, test or log.

`ALREADY_CHECKED` prevents restart loops from retrying that pair. `CLAIMED` with no
completion can reflect an interrupted attempt; its quotas and claim remain
consumed. Normal provider routing still resumes when the original cooldown expires.
`COOLDOWN_PRESERVED`, `CONCURRENT_CHANGE_PRESERVED`, `BUDGET_BLOCKED` and `UNAVAILABLE`
are not successful connection results. None justifies clearing a cooldown manually
or weakening quote, budget or trading safeguards.
