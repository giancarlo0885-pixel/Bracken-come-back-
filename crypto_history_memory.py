"""Versioned historical context for Oracle's crypto reasoning layer.

The catalog is curated, deterministic context. It never supplies prices,
predicts returns, adjusts scores, or authorizes an order.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import math
from typing import Any


CATALOG_VERSION = "2026.08.31"


@dataclass(frozen=True, slots=True)
class CryptoHistoryEvent:
    event_id: str
    event_date: str
    title: str
    category: str
    assets: tuple[str, ...]
    tags: tuple[str, ...]
    context: str
    durable_lesson: str
    primary_source: str

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["assets"] = list(self.assets)
        row["tags"] = list(self.tags)
        return row


EVENTS: tuple[CryptoHistoryEvent, ...] = (
    CryptoHistoryEvent(
        "bitcoin-genesis", "2009-01-03", "Bitcoin genesis block", "protocol",
        ("BTC",), ("bitcoin", "protocol", "monetary-policy", "proof-of-work"),
        "Bitcoin's first block began a scarce, peer-to-peer monetary network.",
        "Protocol rules and verifiable supply are distinct from exchange prices.",
        "https://github.com/bitcoin/bitcoin",
    ),
    CryptoHistoryEvent(
        "mt-gox-collapse", "2014-02-28", "Mt. Gox bankruptcy", "counterparty",
        ("BTC",), ("bitcoin", "exchange", "custody", "failure", "counterparty-risk"),
        "The dominant Bitcoin exchange failed after prolonged operational and custody problems.",
        "Exchange balances create counterparty and custody risk that is separate from Bitcoin protocol risk.",
        "https://www.justice.gov/opa/pr/russian-nationals-charged-hacking-one-cryptocurrency-exchange-and-illicitly-operating",
    ),
    CryptoHistoryEvent(
        "bitcoin-halving-2016", "2016-07-09", "Second Bitcoin halving", "monetary-policy",
        ("BTC",), ("bitcoin", "halving", "supply", "mining"),
        "Bitcoin's block subsidy fell from 25 BTC to 12.5 BTC.",
        "Halvings are known supply events; market impact is neither immediate nor guaranteed.",
        "https://github.com/bitcoin/bitcoin",
    ),
    CryptoHistoryEvent(
        "segwit-activation", "2017-08-24", "Segregated Witness activation", "protocol",
        ("BTC",), ("bitcoin", "protocol", "segwit", "scaling", "fees"),
        "SegWit changed transaction serialization and expanded effective block capacity.",
        "Protocol upgrades can alter capacity and transaction mechanics without dictating asset price.",
        "https://github.com/bitcoin/bips/blob/master/bip-0141.mediawiki",
    ),
    CryptoHistoryEvent(
        "bitcoin-halving-2020", "2020-05-11", "Third Bitcoin halving", "monetary-policy",
        ("BTC",), ("bitcoin", "halving", "supply", "mining"),
        "Bitcoin's block subsidy fell from 12.5 BTC to 6.25 BTC.",
        "Supply schedules interact with liquidity, demand, leverage, and macro conditions.",
        "https://github.com/bitcoin/bitcoin",
    ),
    CryptoHistoryEvent(
        "el-salvador-bitcoin", "2021-09-07", "El Salvador Bitcoin law took effect", "adoption",
        ("BTC",), ("bitcoin", "adoption", "sovereign", "regulation"),
        "El Salvador implemented legislation recognizing Bitcoin as legal tender.",
        "Sovereign adoption can change narrative and access while creating implementation and policy risk.",
        "https://www.asamblea.gob.sv/sites/default/files/documents/decretos/27B5BC4A-3E0B-428E-B52A-49E1CFAE60E2.pdf",
    ),
    CryptoHistoryEvent(
        "terra-collapse", "2022-05-09", "TerraUSD lost its dollar peg", "stablecoin",
        ("UST", "LUNA"), ("stablecoin", "depeg", "failure", "contagion", "liquidity"),
        "The algorithmic stablecoin structure entered a destructive redemption spiral.",
        "A stablecoin label is not proof of reserve quality, redemption capacity, or price stability.",
        "https://www.sec.gov/newsroom/press-releases/2023-32",
    ),
    CryptoHistoryEvent(
        "ftx-bankruptcy", "2022-11-11", "FTX entered bankruptcy", "counterparty",
        ("BTC", "ETH", "FTT"), ("exchange", "custody", "failure", "contagion", "counterparty-risk"),
        "A major centralized exchange failed amid misuse-of-assets allegations and a liquidity crisis.",
        "Broker solvency, custody, reconciliation, and withdrawal access are first-class risk inputs.",
        "https://www.justice.gov/opa/pr/ftx-founder-indicted-fraud-money-laundering-and-campaign-finance-offenses",
    ),
    CryptoHistoryEvent(
        "us-spot-bitcoin-etfs", "2024-01-10", "U.S. spot Bitcoin ETP approvals", "institutional-adoption",
        ("BTC",), ("bitcoin", "etf", "institutional", "flows", "regulation"),
        "The SEC approved rule changes allowing multiple spot Bitcoin exchange-traded products to list.",
        "Regulated access can change market structure and flows but does not remove volatility or tracking risk.",
        "https://www.sec.gov/newsroom/speeches-statements/gensler-statement-spot-bitcoin-011023",
    ),
    CryptoHistoryEvent(
        "bitcoin-halving-2024", "2024-04-20", "Fourth Bitcoin halving", "monetary-policy",
        ("BTC",), ("bitcoin", "halving", "supply", "mining"),
        "Bitcoin's block subsidy fell from 6.25 BTC to 3.125 BTC.",
        "Known issuance changes should be evaluated with miner economics, demand, liquidity, and positioning.",
        "https://github.com/bitcoin/bitcoin",
    ),
    CryptoHistoryEvent(
        "strategic-bitcoin-reserve", "2025-03-06", "U.S. Strategic Bitcoin Reserve ordered", "government-policy",
        ("BTC",), ("bitcoin", "sovereign", "regulation", "reserve", "policy"),
        "A U.S. executive order established a reserve initially capitalized with forfeited bitcoin.",
        "Government policy can affect expectations; an order is not equivalent to open-market buying.",
        "https://www.whitehouse.gov/presidential-actions/2025/03/establishment-of-the-strategic-bitcoin-reserve-and-united-states-digital-asset-stockpile/",
    ),
    CryptoHistoryEvent(
        "genius-act", "2025-07-18", "GENIUS Act signed into law", "regulation",
        ("USDC", "USDT"), ("stablecoin", "regulation", "reserves", "payments", "policy"),
        "The United States enacted a federal framework for payment stablecoins.",
        "Legal status, implementation dates, issuer compliance, reserves, and redemption terms must be distinguished.",
        "https://www.whitehouse.gov/briefings-statements/2025/07/the-president-signed-into-law-s-1582/",
    ),
)


def _value(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _base_asset(symbol: str) -> str:
    normalized = str(symbol or "").upper().strip()
    for suffix in ("-USD", "USD", "/USD", "-USDT", "USDT"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            return normalized[: -len(suffix)].rstrip("-/")
    return normalized


def _query_tags(signal: Any) -> set[str]:
    symbol = _base_asset(str(_value(signal, "symbol", "")))
    tags = {"crypto"}
    if symbol:
        tags.add(symbol.lower())
    if symbol == "BTC":
        tags.update({"bitcoin", "halving", "institutional", "protocol", "supply"})
    if symbol in {"USDC", "USDT", "DAI", "UST"}:
        tags.update({"stablecoin", "reserves", "depeg", "payments"})
    if _number(_value(signal, "event_risk_score", 0.0)) >= 60:
        tags.update({"failure", "contagion", "regulation", "counterparty-risk"})
    if _number(_value(signal, "volatility_20d", 0.0)) >= 0.70:
        tags.update({"liquidity", "contagion", "failure"})
    extra = _value(signal, "history_tags", ())
    if isinstance(extra, str):
        extra = extra.split(",")
    for tag in extra or ():
        cleaned = str(tag).strip().lower()
        if cleaned:
            tags.add(cleaned)
    return tags


def crypto_history_context(
    signal: Any,
    *,
    market: str = "crypto",
    as_of: date | None = None,
    limit: int = 6,
) -> dict[str, Any]:
    """Return relevant completed history for reasoning, never execution.

    Future-dated events are excluded to make backtests point-in-time safe.
    """
    if str(market or "").lower() != "crypto":
        return {
            "catalog_version": CATALOG_VERSION,
            "enabled": False,
            "events": [],
            "influences_decision": False,
            "summary": "Crypto history context is not applied outside the crypto research layer.",
        }

    cutoff = (as_of or date.today()).isoformat()
    tags = _query_tags(signal)
    ranked: list[tuple[int, str, CryptoHistoryEvent]] = []
    for event in EVENTS:
        if event.event_date > cutoff:
            continue
        overlap = len(tags.intersection(event.tags))
        asset_match = _base_asset(str(_value(signal, "symbol", ""))) in event.assets
        score = overlap * 3 + (5 if asset_match else 0)
        if score > 0:
            ranked.append((score, event.event_date, event))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    selected = [event.to_dict() for _, _, event in ranked[: max(1, min(limit, 12))]]
    return {
        "catalog_version": CATALOG_VERSION,
        "enabled": True,
        "as_of": cutoff,
        "query_tags": sorted(tags),
        "events": selected,
        "influences_decision": False,
        "score_adjustment": 0.0,
        "summary": (
            f"{len(selected)} relevant historical crypto event(s) supplied for context only; "
            "current verified data and existing risk gates remain authoritative."
        ),
    }
