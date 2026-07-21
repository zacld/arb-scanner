"""CSV + console output of comparison results."""

from __future__ import annotations

import csv
from pathlib import Path

from .models import Comparison

CSV_COLUMNS = [
    "product_name", "source", "source_price", "market", "market_price",
    "diff_gbp", "diff_pct", "net_profit_gbp", "n_listings", "matched_by",
    "source_url", "market_url",
]

NA_NET = "N/A (retail comparison only)"


def sort_comparisons(
    comparisons: list[Comparison],
    by: str = "net",
    fees_pct: float = 13.0,
    postage: float = 0.0,
) -> list[Comparison]:
    """Sort results best-first.

    ``net`` (default): rows with a computable net profit first, largest net
    first; retail-comparison rows (net N/A) after them, ordered by raw £ gap.
    ``abs``/``pct``: raw £ or % gap, largest first.
    """
    if by == "pct":
        key = lambda c: (0, c.diff_pct)  # noqa: E731
    elif by == "abs":
        key = lambda c: (0, c.diff_abs)  # noqa: E731
    else:
        def key(c: Comparison):
            net = c.net_profit(fees_pct, postage)
            return (1, net) if net is not None else (0, c.diff_abs)
    return sorted(comparisons, key=key, reverse=True)


def write_csv(
    comparisons: list[Comparison],
    path: str | Path,
    fees_pct: float = 13.0,
    postage: float = 0.0,
) -> Path:
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for c in comparisons:
            net = c.net_profit(fees_pct, postage)
            writer.writerow([
                c.product.name, c.product.source, f"{c.product.price:.2f}",
                c.market, f"{c.market_price:.2f}", f"{c.diff_abs:.2f}",
                f"{c.diff_pct:.1f}",
                f"{net:.2f}" if net is not None else NA_NET,
                c.n_listings, c.matched_by,
                c.product.url, c.market_url,
            ])
    return path


def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def format_table(
    comparisons: list[Comparison],
    name_width: int = 44,
    fees_pct: float = 13.0,
    postage: float = 0.0,
) -> str:
    market = comparisons[0].market if comparisons else "market"
    market_col = f"{market} £"
    mw = max(8, len(market_col))
    header = (
        f"{'Product':<{name_width}}  {'Argos £':>8}  {market_col:>{mw}}  "
        f"{'Diff £':>8}  {'Diff %':>7}  {'Net £':>8}  {'N':>3}  {'Match':<5}"
    )
    lines = [header, "-" * len(header)]
    any_na = False
    for c in comparisons:
        net = c.net_profit(fees_pct, postage)
        if net is None:
            any_na = True
            net_cell = f"{'N/A':>8}"
        else:
            net_cell = f"{net:>+8.2f}"
        lines.append(
            f"{_trunc(c.product.name, name_width):<{name_width}}  "
            f"{c.product.price:>8.2f}  {c.market_price:>{mw}.2f}  "
            f"{c.diff_abs:>+8.2f}  {c.diff_pct:>+6.1f}%  {net_cell}  "
            f"{c.n_listings:>3}  {c.matched_by:<5}"
        )
    if comparisons and not any_na:
        lines.append(f"(net = after {fees_pct:g}% selling fees + £{postage:.2f} postage)")
    if any_na:
        lines.append(
            "(N/A = retail comparison only — prices are asks, not resale value)"
        )
    return "\n".join(lines)
