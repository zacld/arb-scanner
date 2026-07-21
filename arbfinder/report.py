"""CSV + console output of comparison results."""

from __future__ import annotations

import csv
from pathlib import Path

from .models import Comparison, MergedRow

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


# --- merged (multi-comparator) report --------------------------------------

MERGED_CSV_COLUMNS = [
    "product_name", "argos_price",
    "pricerunner_price", "pricerunner_gap_gbp",
    "ebay_price", "ebay_diff_gbp", "net_profit_gbp", "ebay_n_listings",
    "argos_url", "pricerunner_url", "ebay_url",
]

_NEG_INF = float("-inf")


def sort_merged(
    rows: list[MergedRow],
    fees_pct: float = 13.0,
    postage: float = 0.0,
) -> list[MergedRow]:
    """Net profit descending; rows without an eBay match come after all rows
    that have one, ordered among themselves by PriceRunner gap."""
    def key(r: MergedRow):
        net = r.net_profit(fees_pct, postage)
        gap = r.pricerunner_gap if r.pricerunner_gap is not None else _NEG_INF
        if net is not None:
            return (1, net, gap)
        return (0, gap, _NEG_INF)
    return sorted(rows, key=key, reverse=True)


def _cell(value: float | None, width: int, signed: bool = False) -> str:
    if value is None:
        return f"{'—':>{width}}"
    return f"{value:>{'+' if signed else ''}{width}.2f}"


def format_merged_table(
    rows: list[MergedRow],
    name_width: int = 40,
    fees_pct: float = 13.0,
    postage: float = 0.0,
) -> str:
    header = (
        f"{'Product':<{name_width}}  {'Argos £':>8}  {'PRun £':>8}  {'PR gap':>8}  "
        f"{'eBay £':>8}  {'Net £':>8}"
    )
    lines = [header, "-" * len(header)]
    for r in rows:
        pr = r.pricerunner
        eb = r.ebay
        lines.append(
            f"{_trunc(r.product.name, name_width):<{name_width}}  "
            f"{r.product.price:>8.2f}  "
            f"{_cell(pr.market_price if pr else None, 8)}  "
            f"{_cell(r.pricerunner_gap, 8, signed=True)}  "
            f"{_cell(eb.market_price if eb else None, 8)}  "
            f"{_cell(r.net_profit(fees_pct, postage), 8, signed=True)}"
        )
    lines.append(
        f"(net = eBay resale minus {fees_pct:g}% fees + £{postage:.2f} postage; "
        f"PR gap = vs lowest retail ask, not profit; — = no match)"
    )
    return "\n".join(lines)


def write_merged_csv(
    rows: list[MergedRow],
    path: str | Path,
    fees_pct: float = 13.0,
    postage: float = 0.0,
) -> Path:
    path = Path(path)

    def fmt(value: float | None, signed_precision: str = ".2f") -> str:
        return "" if value is None else f"{value:{signed_precision}}"

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(MERGED_CSV_COLUMNS)
        for r in rows:
            pr, eb = r.pricerunner, r.ebay
            writer.writerow([
                r.product.name, f"{r.product.price:.2f}",
                fmt(pr.market_price if pr else None), fmt(r.pricerunner_gap),
                fmt(eb.market_price if eb else None),
                fmt(eb.diff_abs if eb else None),
                fmt(r.net_profit(fees_pct, postage)),
                eb.n_listings if eb else "",
                r.product.url,
                pr.market_url if pr else "",
                eb.market_url if eb else "",
            ])
    return path
