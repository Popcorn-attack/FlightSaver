"""Currency conversion so offers priced in CNY and GBP can be compared.

Rates come from the ECB via frankfurter.app (free, no key), cached for a day,
with conservative built-in fallbacks when offline.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request

# Units of currency per 1 EUR; only used if the live fetch fails.
_FALLBACK_PER_EUR = {"EUR": 1.0, "GBP": 0.86, "CNY": 8.3, "USD": 1.16, "HKD": 9.0}
_TTL = 24 * 3600
_lock = threading.Lock()
_cache: dict = {"at": 0.0, "rates": dict(_FALLBACK_PER_EUR)}


def _rates() -> dict[str, float]:
    with _lock:
        if time.time() - _cache["at"] < _TTL:
            return _cache["rates"]
        try:
            with urllib.request.urlopen(
                "https://api.frankfurter.app/latest?from=EUR", timeout=5
            ) as resp:
                data = json.load(resp)
            rates = {"EUR": 1.0, **{k: float(v) for k, v in data["rates"].items()}}
            _cache.update(at=time.time(), rates=rates)
        except Exception:
            # Retry in an hour rather than on every call.
            _cache["at"] = time.time() - _TTL + 3600
        return _cache["rates"]


def convert(amount: float, src: str, dst: str) -> float:
    src, dst = src.upper(), dst.upper()
    if src == dst:
        return amount
    rates = _rates()
    if src not in rates or dst not in rates:
        raise ValueError(f"no exchange rate for {src}->{dst}")
    return round(amount / rates[src] * rates[dst], 2)


def set_rates_for_tests(per_eur: dict[str, float]) -> None:
    with _lock:
        _cache.update(at=time.time(), rates={"EUR": 1.0, **per_eur})
