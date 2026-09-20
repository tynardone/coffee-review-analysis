"""Command-line entry points for the pipeline steps.

Each function here is registered in ``pyproject.toml`` under
``[project.scripts]``, so the steps run as ``scrape-reviews``,
``fetch-exchange-rates`` and ``resolve-roasters`` once the package is
installed. Argument parsing and logging setup live here; the work itself lives
in the modules these import, so it stays importable from notebooks and tests.
"""

import argparse
import asyncio
import logging
from pathlib import Path

import pandas as pd

from coffee.config import OpenExConfig
from coffee.exchange_rates import (
    DEFAULT_OUTPUT,
    fetch_rates,
    load_review_dates,
    save_rates,
)
from coffee.pipeline import DEFAULT_OUTPUT_DIR, scrape_all_reviews
from coffee.roaster_resolution import load_decisions, promote_reviewed, resolve

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 10


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def _positive_int(value: str) -> int:
    """argparse type that rejects non-positive integers."""
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value!r}")
    return parsed


def scrape_reviews(argv: list[str] | None = None) -> None:
    """Scrape every review to a dated CSV + JSON."""
    parser = argparse.ArgumentParser(description=scrape_reviews.__doc__)
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for the dated reviews CSV and JSON.",
    )
    parser.add_argument(
        "-c",
        "--concurrency",
        type=_positive_int,
        default=DEFAULT_CONCURRENCY,
        help="Maximum number of concurrent review requests.",
    )
    args = parser.parse_args(argv)

    _configure_logging()
    asyncio.run(scrape_all_reviews(args.output_dir, args.concurrency))


def fetch_exchange_rates(argv: list[str] | None = None) -> None:
    """Fetch historical exchange rates for the dates in a scraped reviews file."""
    parser = argparse.ArgumentParser(description=fetch_exchange_rates.__doc__)
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        required=True,
        help="Scraped reviews file (.csv or .json).",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Destination JSON file for exchange rates.",
    )
    args = parser.parse_args(argv)

    _configure_logging()
    app_id = OpenExConfig.OPENEXCHANGERATES_API_ID
    if not app_id:
        raise SystemExit("OPENEXCHANGERATES_API_ID is not set (add it to your .env).")

    dates = load_review_dates(args.input)
    logger.info("Fetching rates for %d unique dates", len(dates))
    rates = fetch_rates(dates, app_id)

    failures = sum(1 for rate in rates.values() if not rate)
    if failures:
        logger.warning("%d/%d dates returned no rates", failures, len(dates))

    save_rates(rates, args.output)
    logger.info("Wrote exchange rates to %s", args.output)


def resolve_roasters(argv: list[str] | None = None) -> None:
    """Cluster messy roaster-name spellings into canonical entities."""
    parser = argparse.ArgumentParser(description=resolve_roasters.__doc__)
    parser.add_argument("infile", type=Path, help="CSV containing the names")
    parser.add_argument("--column", default="roaster", help="column holding the names")
    parser.add_argument(
        "--location-column",
        default="roaster location",
        help="column holding each roaster's location; '' disables the signal",
    )
    parser.add_argument(
        "--outdir", type=Path, default=OpenExConfig.DATA_DIR / "processed"
    )
    parser.add_argument(
        "--decisions",
        type=Path,
        default=None,
        help="CSV of adjudicated pairs (default: <outdir>/roaster_decisions.csv). "
        "Missing is fine; it is created as you record verdicts.",
    )
    parser.add_argument(
        "--auto",
        type=int,
        default=92,
        help="score >= this: merge automatically (raise if you see false merges)",
    )
    parser.add_argument(
        "--review",
        type=int,
        default=82,
        help="score in [review, auto): send to the review queue "
        "(lower it if true matches are being missed entirely)",
    )
    parser.add_argument(
        "--location-review",
        type=int,
        default=None,
        help="also queue pairs scoring below --review when their locations match "
        "exactly (e.g. 70). Recovers true matches the name score alone misses, "
        "at the cost of more pairs to adjudicate.",
    )
    parser.add_argument(
        "--accept-reviewed",
        action="store_true",
        help="first fold any verdicts you filed in the review queue into the "
        "decisions file, then re-resolve with them applied",
    )
    parser.add_argument(
        "--decided-by",
        default="manual",
        help="recorded against decisions promoted by --accept-reviewed",
    )
    args = parser.parse_args(argv)

    frame = pd.read_csv(args.infile)
    names = frame[args.column].dropna().astype(str).tolist()

    locations = None
    if args.location_column and args.location_column in frame.columns:
        locations = (
            frame.dropna(subset=[args.column, args.location_column])
            .groupby(args.column)[args.location_column]
            .apply(lambda values: set(values.astype(str)))
            .to_dict()
        )
    elif args.location_column:
        logger.warning(
            "No %r column in %s; resolving on names alone.",
            args.location_column,
            args.infile,
        )

    decisions_path = args.decisions or args.outdir / "roaster_decisions.csv"
    if args.accept_reviewed:
        added = promote_reviewed(
            args.outdir / "roaster_review_queue.csv", decisions_path, args.decided_by
        )
        print(f"recorded {added} new decision(s) in {decisions_path}")
    decisions = load_decisions(decisions_path)

    crosswalk, review = resolve(
        names,
        locations=locations,
        decisions=decisions,
        auto_threshold=args.auto,
        review_threshold=args.review,
        location_review_threshold=args.location_review,
    )

    args.outdir.mkdir(parents=True, exist_ok=True)
    crosswalk_path = args.outdir / "roaster_crosswalk.csv"
    review_path = args.outdir / "roaster_review_queue.csv"
    crosswalk.to_csv(crosswalk_path, index=False)
    review.to_csv(review_path, index=False)

    # Read these in order: did it merge anything, how much review is left, and
    # -- the one that matters -- did single-linkage chain clusters together?
    n_raw = crosswalk.raw_name.nunique()
    n_canonical = crosswalk.canonical_name.nunique()
    print(
        f"{n_raw} distinct spellings -> {n_canonical} roasters "
        f"({n_raw - n_canonical} merged)"
    )
    print(
        f"{len(decisions)} decisions applied from {decisions_path}"
        if decisions
        else f"no decisions yet ({decisions_path} not found)"
    )
    print(f"{len(review)} pairs queued for review -> {review_path}")
    print(
        f"{int(crosswalk.chain_risk.sum())} rows in chain-risk clusters"
        f"{'  <-- INSPECT THESE' if crosswalk.chain_risk.any() else ''}"
    )
