"""Pure, research-only accounting for crypto universe discovery and scan coverage.

No execution decisions, portfolio state or provider calls are changed here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


def _symbols(values: Iterable[str]) -> set[str]:
    return {str(v).strip().upper() for v in values if str(v).strip()}


@dataclass(frozen=True)
class CryptoCoverage:
    discovered: int
    active_seed: int
    analyzed: int
    deferred: int
    rejected: int
    missing_from_seed: int
    unknown_analyzed: int
    unknown_rejected: int
    unknown_deferred: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def summarize_crypto_coverage(
    discovered: Iterable[str],
    active_seed: Iterable[str],
    analyzed: Iterable[str],
    rejected: Iterable[str] = (),
    deferred: Iterable[str] = (),
) -> CryptoCoverage:
    """Account for each unique provider symbol without inventing evaluations.

    Explicit rejection wins over deferred; actual analysis wins over both.
    Unaccounted discovered assets are deferred by default, never counted as
    analyzed. Unknown classifications are exposed as integrity diagnostics.
    """
    universe = _symbols(discovered)
    seed = _symbols(active_seed)
    analyzed_set = _symbols(analyzed)
    rejected_set = _symbols(rejected) - analyzed_set
    deferred_set = _symbols(deferred) - analyzed_set - rejected_set
    classified = analyzed_set | rejected_set | deferred_set
    unaccounted = universe - classified
    return CryptoCoverage(
        discovered=len(universe),
        active_seed=len(seed),
        analyzed=len(universe & analyzed_set),
        deferred=len((universe & deferred_set) | unaccounted),
        rejected=len(universe & rejected_set),
        missing_from_seed=len(universe - seed),
        unknown_analyzed=len(analyzed_set - universe),
        unknown_rejected=len(rejected_set - universe),
        unknown_deferred=len(deferred_set - universe),
    )
