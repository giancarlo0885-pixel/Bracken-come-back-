Verification targets for this patch:

1. `python -m compileall -q .`
2. `python -m pytest -q test_runtime_integrity_patch.py`
3. full `python -m pytest -q`
4. PostgreSQL integration workflow
5. Railway stock/crypto startup logs must report `paper_small_account_max_positions=10`
6. Yahoo handoff logs must show `provider_verified=False` and `paper_reference_verified=True` when the paper fallback is used
7. handoff `correlation_id` must be non-empty
8. Robinhood remains read-only/disarmed during validation
