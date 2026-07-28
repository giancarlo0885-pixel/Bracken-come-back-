from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class GuardState:
    disabled_until: float = 0.0
    reason: str = ""
    failures: int = 0


_lock = threading.RLock()
_states: dict[str, GuardState] = {}


def available(provider: str) -> bool:
    with _lock:
        return time.time() >= _states.get(provider, GuardState()).disabled_until


def disable(provider: str, seconds: int, reason: str) -> None:
    with _lock:
        state = _states.setdefault(provider, GuardState())
        state.disabled_until = max(state.disabled_until, time.time() + max(60, seconds))
        state.reason = str(reason)
        state.failures += 1


def state(provider: str) -> dict[str, object]:
    with _lock:
        item = _states.get(provider, GuardState())
        remaining = max(0, int(item.disabled_until - time.time()))
        return {
            "available": remaining == 0,
            "cooldown_remaining_seconds": remaining,
            "reason": item.reason,
            "failures": item.failures,
        }
