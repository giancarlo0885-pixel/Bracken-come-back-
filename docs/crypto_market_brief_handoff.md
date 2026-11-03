# GitHub brief intake while stock research is paused

The full stock-worker standby introduced for crypto-only cost control also stops
its scheduled GitHub intelligence-inbox polling. The crypto worker continues its
own Brain learning sync, but previously had no scheduled inbox replacement.

An optional GitHub-only handoff now reuses the existing intelligence executor,
single in-flight future and `INTELLIGENCE_REFRESH_SECONDS` cadence. The default
900-second cadence polls at most 96 times/day per process. It calls only the
existing inbox validator and durable payload/event deduplication path. It does
not start stock price scans, `collect_all()` providers, entry/exit routes or cash
Brain synchronization. It adds no table, changes no quote authority and certifies
no claim contained in a brief as profitable evidence.

The handoff remains off unless the crypto worker has all of:

```
ENABLE_CRYPTO_MARKET_BRIEF_HANDOFF=true
ENABLE_CASH_MARKET_RESEARCH=false
MARKET_BRIEF_GITHUB_ISSUE=<existing configured inbox issue number>
EXECUTION_MODE=paper
ENABLE_BROKER_SUBMISSION=false
LIVE_TRADING_ARMED=false
```

Existing optional repository, owner and title settings must retain their original
contract. No issue number or research record is invented by this change. Missing
paper controls disable the handoff. Enabling cash research on this worker's
configuration disables its crypto handoff, avoiding two scheduled polling paths
under a consistent service configuration. Cross-service settings must be reviewed
before activation: the cash and crypto services have independent variables.
Existing deterministic event keys and durable payload hashes are retained; this
change does not introduce a cross-service ownership lock or promise one HTTP
request across independently misconfigured service replicas.

HTTP failures retain status/rate-limit/request-ID diagnostics. They do not create
replacement intelligence records. Configuration changes require process restart;
this change makes no production settings or release changes.
