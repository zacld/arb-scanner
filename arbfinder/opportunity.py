"""Turn validated price comparisons into profit-first arbitrage opportunities.

This is where money — not popularity — decides. Each comparison becomes an
``ArbitrageOpportunity`` with net profit, ROI and confidence scores. Hard
thresholds (min net, min ROI, min match) exclude weak items *before* ranking,
and the final sort is profit-first: net → ROI → demand → match → trend/seasonal.
``opportunity_score`` (40% net + 25% ROI + 15% demand + 10% match + 5% trend +
5% seasonal) is a headline blend, shown but never allowed to float a low-profit
item above a more profitable one.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .matching import title_similarity
from .models import Comparison
from .trends.base import ArbitrageOpportunity

# Saturation caps so £/% values fold into the 0..1 blend.
NET_CAP = 50.0     # £50+ net = full marks on the profit component
ROI_CAP = 1.0      # 100%+ ROI = full marks on the ROI component
DEMAND_CAP = 8     # 8+ credible listings = full demand confidence


def roi(net: float, buy: float) -> float:
    return net / buy if buy > 0 else 0.0


def demand_confidence(n_listings: int) -> float:
    return min(1.0, n_listings / DEMAND_CAP)


def match_confidence(comp: Comparison) -> float:
    if comp.matched_by == "ean":
        return 1.0
    if not comp.listings:
        return 0.0
    best = max((title_similarity(comp.product.name, l.title)
                for l in comp.listings if l.title), default=0.0)
    return best / 100.0


def opportunity_score(net, roi_, demand, match, trend, seasonal) -> float:
    def clamp(x):
        return max(0.0, min(1.0, x))
    return round(
        0.40 * clamp(net / NET_CAP)
        + 0.25 * clamp(roi_ / ROI_CAP)
        + 0.15 * clamp(demand)
        + 0.10 * clamp(match)
        + 0.05 * clamp(trend)
        + 0.05 * clamp(seasonal),
        4,
    )


def build_opportunity(comp: Comparison, fees_pct: float, postage: float,
                      trend_strength: float = 0.0,
                      seasonal_strength: float = 0.0) -> ArbitrageOpportunity | None:
    """Score one comparison. None for non-resale markets (no sell price)."""
    net = comp.net_profit(fees_pct, postage)
    if net is None:  # retail-comparison markets (PriceRunner/Google) can't net
        return None
    buy = comp.product.price
    r = roi(net, buy)
    dem = demand_confidence(comp.n_listings)
    mat = match_confidence(comp)
    fees_amount = comp.market_price * fees_pct / 100.0 + postage
    return ArbitrageOpportunity(
        retailer_product=comp.product,
        marketplace_match=comp,
        buy_price=buy,
        expected_sale_price=comp.market_price,
        fees=round(fees_amount, 2),
        estimated_net_profit=round(net, 2),
        roi=round(r, 4),
        demand_confidence=round(dem, 3),
        match_confidence=round(mat, 3),
        trend_strength=round(trend_strength, 3),
        seasonal_strength=round(seasonal_strength, 3),
        opportunity_score=opportunity_score(net, r, dem, mat, trend_strength, seasonal_strength),
    )


def rank_opportunities(opps, *, min_net=None, min_roi=None, min_match=None):
    """Exclude sub-threshold items, then sort PROFIT-FIRST."""
    kept = [
        o for o in opps
        if (min_net is None or o.estimated_net_profit >= min_net)
        and (min_roi is None or o.roi >= min_roi)
        and (min_match is None or o.match_confidence >= min_match)
    ]
    kept.sort(
        key=lambda o: (o.estimated_net_profit, o.roi, o.demand_confidence,
                       o.match_confidence, o.trend_strength + o.seasonal_strength),
        reverse=True,
    )
    return kept


# -- output -----------------------------------------------------------------

def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def format_opportunities(opps, name_width: int = 38) -> str:
    if not opps:
        return "No opportunities cleared the profit/ROI/match thresholds."
    market = getattr(opps[0].marketplace_match, "market", "resale")
    header = (
        f"{'Product':<{name_width}}  {'Buy £':>7}  {market[:4] + '£*':>8}  "
        f"{'Net £':>7}  {'ROI':>6}  {'Score':>5}  {'Dem':>4}  {'Mat':>4}  {'T/S':>4}"
    )
    lines = [header, "-" * len(header)]
    for o in opps:
        ts = max(o.trend_strength, o.seasonal_strength)
        lines.append(
            f"{_trunc(o.retailer_product.name, name_width):<{name_width}}  "
            f"{o.buy_price:>7.2f}  {o.expected_sale_price:>8.2f}  "
            f"{o.estimated_net_profit:>+7.2f}  {o.roi * 100:>5.0f}%  "
            f"{o.opportunity_score:>5.2f}  {o.demand_confidence:>4.2f}  "
            f"{o.match_confidence:>4.2f}  {ts:>4.2f}"
        )
    lines.append(f"* {market}£ = median of ACTIVE listings (asking prices, NOT completed/"
                 "sold). Net/ROI derive from it — verify sold volume & price before buying.")
    lines.append("(sorted profit-first: net → ROI → demand → match → trend/seasonal. "
                 "Dem/Mat/T-S are 0-1 confidences.)")
    return "\n".join(lines)


OPP_CSV_COLUMNS = [
    "product", "source", "buy_price", "market", "expected_sale_price", "fees",
    "estimated_net_profit", "roi", "demand_confidence", "match_confidence",
    "trend_strength", "seasonal_strength", "opportunity_score",
    "product_url", "market_url",
]


def write_opportunities_csv(opps, path) -> Path:
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(OPP_CSV_COLUMNS)
        for o in opps:
            comp = o.marketplace_match
            w.writerow([
                o.retailer_product.name, o.retailer_product.source,
                f"{o.buy_price:.2f}", getattr(comp, "market", ""),
                f"{o.expected_sale_price:.2f}", f"{o.fees:.2f}",
                f"{o.estimated_net_profit:.2f}", f"{o.roi:.4f}",
                f"{o.demand_confidence:.3f}", f"{o.match_confidence:.3f}",
                f"{o.trend_strength:.3f}", f"{o.seasonal_strength:.3f}",
                f"{o.opportunity_score:.4f}",
                o.retailer_product.url, getattr(comp, "market_url", ""),
            ])
    return path
