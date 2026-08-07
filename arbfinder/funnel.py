"""Two-stage opportunity funnel: cheap providers filter wide, expensive ones
only touch the survivors.

The whole efficiency story lives here, once, provider-agnostic:

    Stage 1 (cheap, e.g. eBay's free API): price the WHOLE swept pool, apply the
            profit gates → a short list of survivors.
    Stage 2 (expensive, e.g. Amazon via browser, or a paid Keepa key): price ONLY
            the survivors. The higher-net venue becomes the lead number; the other
            is attached for comparison.

So a wide sweep costs ~O(pool) free API calls and only ~O(survivors) expensive
calls — never a browser hit on a product that was never going to clear the gate.
Swapping the rich stage for a paid provider (Keepa) is a drop-in; nothing here
changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .opportunity import build_opportunity, rank_opportunities
from .pipeline import compare_products


@dataclass
class FunnelResult:
    opportunities: list = field(default_factory=list)  # enriched, gated, ranked
    staged: list = field(default_factory=list)         # all cheap-stage opps, ungated
    n_priced: int = 0                                   # products priced in stage 1


def _price_pass(products, provider, fees, postage, min_score):
    """Price a pool on one provider → {product_url: (comparison, opportunity)}."""
    out: dict[str, tuple] = {}
    for comp in compare_products(products, provider, min_score):
        opp = build_opportunity(comp, fees, postage)
        if opp is not None:
            out[comp.product.url] = (comp, opp)
    return out


def find_opportunities(products, cheap_provider, rich_provider=None, *,
                       fees: float, postage: float, min_net=None, min_roi=None,
                       min_match=None, enrich_cap: int = 20, min_score: float = 85.0,
                       progress=None) -> FunnelResult:
    """Run the funnel. ``cheap_provider`` prices everything and the gates decide
    survivors; ``rich_provider`` (optional) re-prices just those survivors."""
    progress = progress or (lambda *a, **k: None)

    # -- Stage 1: cheap, wide -------------------------------------------------
    progress("collecting_prices",
             f"pricing {len(products)} on {getattr(cheap_provider, 'market_name', 'the cheap venue')}")
    cheap = _price_pass(products, cheap_provider, fees, postage, min_score)
    staged = rank_opportunities([o for (_c, o) in cheap.values()])
    survivors = rank_opportunities([o for (_c, o) in cheap.values()],
                                   min_net=min_net, min_roi=min_roi, min_match=min_match)
    survivors = survivors[:enrich_cap]  # bound how many we pay to enrich

    if rich_provider is None or not survivors:
        return FunnelResult(opportunities=survivors, staged=staged, n_priced=len(cheap))

    # -- Stage 2: expensive, narrow (only the survivors) ----------------------
    progress("collecting_prices",
             f"confirming top {len(survivors)} on {getattr(rich_provider, 'market_name', 'the rich venue')}")
    rich = _price_pass([o.retailer_product for o in survivors],
                       rich_provider, fees, postage, min_score)

    enriched = []
    for o in survivors:
        url = o.retailer_product.url
        pair = rich.get(url)
        if pair is None:
            enriched.append(o)  # rich venue had no credible match — keep cheap one
            continue
        _rich_comp, rich_opp = pair
        # The better flip leads; the other venue is attached for comparison.
        if rich_opp.estimated_net_profit >= o.estimated_net_profit:
            lead, other_comp = rich_opp, cheap[url][0]
        else:
            lead, other_comp = o, rich[url][0]
        lead.secondary_market = other_comp.market
        lead.secondary_price = other_comp.market_price
        lead.secondary_url = other_comp.market_url
        enriched.append(lead)

    return FunnelResult(opportunities=rank_opportunities(enriched),
                        staged=staged, n_priced=len(cheap))
