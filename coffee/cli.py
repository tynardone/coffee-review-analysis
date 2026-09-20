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

from coffee.clean import (
    DEFAULT_BASELINE_DATE,
    clean_reviews,
    load_cpi,
    load_exchange_rates,
)
from coffee.config import DATA_DIR, openexchangerates_api_id
from coffee.exchange_rates import (
    DEFAULT_OUTPUT,
    fetch_rates,
    load_review_dates,
    save_rates,
)
from coffee.pipeline import (
    DEFAULT_CONCURRENCY,
    DEFAULT_OUTPUT_DIR,
    scrape_all_reviews,
)
from coffee.roaster_resolution import (
    load_decisions,
    promote_reviewed,
    resolve,
    unpromoted_verdicts,
)
from coffee.storage import CsvReviewStore

__all__ = [
    "clean_reviews_command",
    "fetch_exchange_rates",
    "resolve_roasters",
    "scrape_reviews",
]

logger = logging.getLogger(__name__)


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
    """Update the review corpus, fetching only what changed since last run."""
    parser = argparse.ArgumentParser(description=scrape_reviews.__doc__)
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory holding reviews.csv and reviews.json.",
    )
    parser.add_argument(
        "-c",
        "--concurrency",
        type=_positive_int,
        default=DEFAULT_CONCURRENCY,
        help="Maximum number of concurrent review requests.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="re-fetch every review instead of only what changed. Needed after "
        "a parser change, since an incremental run re-parses only the pages it "
        "re-fetches.",
    )
    args = parser.parse_args(argv)

    _configure_logging()
    store = CsvReviewStore(args.output_dir)
    asyncio.run(scrape_all_reviews(store, args.concurrency, full=args.full))


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
    app_id = openexchangerates_api_id()
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
    parser.add_argument("--outdir", type=Path, default=DATA_DIR / "processed")
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
    review_path = args.outdir / "roaster_review_queue.csv"

    if args.accept_reviewed:
        try:
            added = promote_reviewed(review_path, decisions_path, args.decided_by)
        except ValueError as exc:  # unreadable verdicts: a user error, not a bug
            raise SystemExit(str(exc)) from exc
        print(f"recorded {added} new decision(s) in {decisions_path}")
    else:
        # This run regenerates the queue. If the existing one still holds
        # answers that were never recorded, regenerating would discard them,
        # so stop and name the flag that records them first.
        pending = unpromoted_verdicts(review_path)
        if pending:
            raise SystemExit(
                f"{review_path} has {pending} verdict(s) that are not in "
                f"{decisions_path} yet, and this run would overwrite them.\n"
                "Re-run with --accept-reviewed to record them first, or delete "
                "the queue if you meant to discard them."
            )

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
    crosswalk.to_csv(crosswalk_path, index=False)
    review.to_csv(review_path, index=False)

    # Reported in order: what was merged, how much review remains, and whether
    # single-linkage chained any clusters together.
    n_raw = crosswalk.raw_name.nunique()
    n_canonical = crosswalk.canonical_name.nunique()
    print(
        f"{n_raw} distinct spellings -> {n_canonical} roasters "
        f"({n_raw - n_canonical} merged)"
    )
    # An absent decisions file and an empty one are reported differently,
    # since they call for different action.
    if decisions:
        print(f"{len(decisions)} decisions applied from {decisions_path}")
    elif decisions_path.exists():
        print(f"0 decisions in {decisions_path} (nothing adjudicated yet)")
    else:
        print(f"no decisions file yet; it will be created at {decisions_path}")
    print(f"{len(review)} pairs queued for review -> {review_path}")
    print(
        f"{int(crosswalk.chain_risk.sum())} rows in chain-risk clusters"
        f"{'  <-- INSPECT THESE' if crosswalk.chain_risk.any() else ''}"
    )

    # A recorded split that the clustering defeated transitively. Reported
    # prominently, since a decision that was accepted and then not applied is
    # harder to notice than one that was rejected.
    if "violates_decision" in crosswalk and crosswalk.violates_decision.any():
        offenders = crosswalk[crosswalk.violates_decision]
        print(
            f"\n!! {offenders.cluster_id.nunique()} cluster(s) VIOLATE a split "
            "decision -- these names were kept together despite your verdict:"
        )
        for _, group in offenders.groupby("cluster_id"):
            print("    " + "  |  ".join(sorted(group.raw_name)))
        print(
            "   A split can be defeated through a third name that resembles "
            "both\n   (often a collaboration, e.g. 'A & B Coffee'). Record a "
            "split against\n   that bridging name too."
        )


def clean_reviews_command(argv: list[str] | None = None) -> None:
    """Build the cleaned layer from the raw scrape."""
    parser = argparse.ArgumentParser(description=clean_reviews_command.__doc__)
    parser.add_argument(
        "-i", "--input", type=Path, default=DATA_DIR / "raw" / "reviews.csv"
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DATA_DIR / "clean" / "reviews.csv",
    )
    parser.add_argument(
        "--rates",
        type=Path,
        default=DATA_DIR / "external" / "openex_exchange_rates.json",
    )
    parser.add_argument(
        "--cpi", type=Path, default=DATA_DIR / "external" / "consumer_price_index.csv"
    )
    parser.add_argument(
        "--crosswalk",
        type=Path,
        default=DATA_DIR / "processed" / "roaster_crosswalk.csv",
        help="roaster crosswalk; skipped if absent",
    )
    parser.add_argument(
        "--baseline-date",
        default=DEFAULT_BASELINE_DATE,
        help="month whose dollars adjusted prices are expressed in",
    )
    args = parser.parse_args(argv)

    _configure_logging()
    raw = pd.read_csv(args.input)
    crosswalk = pd.read_csv(args.crosswalk) if args.crosswalk.exists() else None
    if crosswalk is None:
        logger.warning(
            "No crosswalk at %s; roaster spellings stay unresolved.", args.crosswalk
        )

    cleaned = clean_reviews(
        raw,
        exchange_rates=load_exchange_rates(args.rates),
        cpi=load_cpi(args.cpi),
        crosswalk=crosswalk,
        baseline_date=args.baseline_date,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    cleaned.to_csv(args.output, index=False)
    dropped = len(raw) - len(cleaned)
    print(
        f"{len(raw)} raw -> {len(cleaned)} cleaned ({dropped} dropped as agtron typos)"
    )
    print(f"prices in {args.baseline_date} dollars -> {args.output}")
