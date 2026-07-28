from __future__ import annotations
import copy, hashlib, json, threading, time
from dataclasses import dataclass
from typing import Any, Callable

@dataclass
class CacheEntry:
    value: Any
    expires_at: float
    created_at: float

_lock = threading.RLock()
_cache: dict[str, CacheEntry] = {}
_stats = {"hits": 0, "misses": 0, "sets": 0, "errors": 0}

def make_key(namespace: str, *parts: Any, **kwargs: Any) -> str:
    payload = json.dumps([parts, sorted(kwargs.items())], default=str, sort_keys=True)
    return f"{namespace}:{hashlib.sha256(payload.encode()).hexdigest()}"

def get(key: str) -> Any | None:
    now = time.time()
    with _lock:
        entry = _cache.get(key)
        if not entry or entry.expires_at <= now:
            if entry: _cache.pop(key, None)
            _stats["misses"] += 1
            return None
        _stats["hits"] += 1
        return copy.deepcopy(entry.value)

def set_value(key: str, value: Any, ttl: int) -> Any:
    now = time.time()
    with _lock:
        _cache[key] = CacheEntry(copy.deepcopy(value), now + max(1, ttl), now)
        _stats["sets"] += 1
    return value

def cached_call(namespace: str, ttl: int, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    key = make_key(namespace, *args, **kwargs)
    cached = get(key)
    if cached is not None: return cached
    try:
        return set_value(key, fn(*args, **kwargs), ttl)
    except Exception:
        with _lock: _stats["errors"] += 1
        raise

def stats() -> dict[str, Any]:
    with _lock:
        live = sum(1 for entry in _cache.values() if entry.expires_at > time.time())
        return {**_stats, "live_entries": live}
