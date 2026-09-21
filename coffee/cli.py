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
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import requests

from coffee.clean import DEFAULT_BASELINE_DATE, clean_reviews
from coffee.config import DATA_DIR, openexchangerates_api_id
from coffee.cpi import DEFAULT_CPI_PATH, MONTH_COLUMNS, fetch_cpi
from coffee.enrich import load_cpi, load_exchange_rates
from coffee.exchange_rates import (
    DEFAULT_OUTPUT,
    fetch_rates,
    load_rates,
    load_review_dates,
    unfetched_dates,
)
from coffee.pipeline import (
    DEFAULT_CONCURRENCY,
    DEFAULT_OUTPUT_DIR,
    scrape_all_reviews,
)
from coffee.roasters import (
    format_resolution_report,
    format_violations,
    load_decisions,
    promote_reviewed,
    resolve,
    unpromoted_verdicts,
)
from coffee.storage import CsvReviewStore

__all__ = [
    "clean_reviews_command",
    "fetch_cpi_command",
    "refresh_data",
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
        help="Directory holding reviews.csv.",
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
        default=DATA_DIR / "raw" / "reviews.csv",
        help="Reviews file whose review months need rates. Defaults to the raw "
        "scrape, which is what keeps this independent of the cleaning step it "
        "feeds; the cleaned layer is also accepted.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Destination JSON file for exchange rates.",
    )
    parser.add_argument(
        "--refetch",
        action="store_true",
        help="re-request dates already held. Rates for a past date do not "
        "change, so this is only for repairing a corrupt file.",
    )
    args = parser.parse_args(argv)

    _configure_logging()
    app_id = openexchangerates_api_id()
    if not app_id:
        raise SystemExit("OPENEXCHANGERATES_API_ID is not set (add it to your .env).")

    try:
        dates = load_review_dates(args.input)
    except FileNotFoundError as exc:
        raise SystemExit(
            f"{args.input} does not exist. Run `uv run scrape-reviews` first, "
            "or pass --input to point at a different reviews file."
        ) from exc
    held = load_rates(args.output)
    pending = dates if args.refetch else unfetched_dates(dates, held)

    print(
        f"{len(dates)} review month(s); {len(dates) - len(pending)} already held, "
        f"{len(pending)} to fetch"
    )
    if not pending:
        print(f"Nothing to fetch; {args.output} is already current.")
        return

    rates = fetch_rates(dates, app_id, args.output, refetch=args.refetch)

    missing = [str(day) for day in dates if not rates.get(str(day))]
    if missing:
        print(
            f"{len(missing)} date(s) still have no rates and will be retried "
            f"next run, starting with {missing[0]}"
        )
    print(f"Wrote {sum(1 for r in rates.values() if r)} dated rates to {args.output}")


def resolve_roasters(argv: list[str] | None = None) -> None:
    """Cluster messy roaster-name spellings into canonical entities."""
    parser = argparse.ArgumentParser(description=resolve_roasters.__doc__)
    parser.add_argument("infile", type=Path, help="CSV containing the names")
    parser.add_argument("--column", default="roaster", help="column holding the names")
    parser.add_argument(
        "--location-column",
        default="roaster_location",
        help="column holding each roaster's location; '' disables the signal",
    )
    parser.add_argument("--outdir", type=Path, default=DATA_DIR / "roasters")
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

    print(
        format_resolution_report(
            crosswalk,
            review,
            decisions,
            decisions_path=decisions_path,
            review_path=review_path,
        )
    )
    if violations := format_violations(crosswalk):
        print(violations)


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
        default=DATA_DIR / "clean" / "reviews.parquet",
    )
    parser.add_argument(
        "--crosswalk",
        type=Path,
        default=DATA_DIR / "roasters" / "roaster_crosswalk.csv",
        help="roaster crosswalk; skipped if absent",
    )
    parser.add_argument(
        "--rates",
        type=Path,
        default=DATA_DIR / "external" / "openex_exchange_rates.json",
        help="historical exchange rates; price conversion is skipped if absent",
    )
    parser.add_argument(
        "--cpi",
        type=Path,
        default=DATA_DIR / "external" / "consumer_price_index.csv",
        help="BLS CPI-U table; inflation adjustment is skipped if absent",
    )
    parser.add_argument(
        "--baseline-date",
        default=DEFAULT_BASELINE_DATE,
        help="the month whose dollars adjusted prices are expressed in "
        f"(default {DEFAULT_BASELINE_DATE}). Must be a month the CPI table "
        "covers.",
    )
    args = parser.parse_args(argv)

    _configure_logging()
    raw = pd.read_csv(args.input)
    crosswalk = pd.read_csv(args.crosswalk) if args.crosswalk.exists() else None
    if crosswalk is None:
        logger.warning(
            "No crosswalk at %s; roaster spellings stay unresolved.", args.crosswalk
        )

    rates = load_exchange_rates(args.rates) if args.rates.exists() else None
    cpi = load_cpi(args.cpi) if args.cpi.exists() else None
    if rates is None or cpi is None:
        missing = [
            str(p) for p, v in ((args.rates, rates), (args.cpi, cpi)) if v is None
        ]
        logger.warning(
            "Missing %s; prices stay in their original currency.",
            " and ".join(missing),
        )

    try:
        cleaned = clean_reviews(
            raw,
            exchange_rates=rates,
            cpi=cpi,
            crosswalk=crosswalk,
            baseline_date=args.baseline_date,
        )
    except ValueError as exc:  # an unusable baseline is a user error
        raise SystemExit(str(exc)) from exc

    args.output.parent.mkdir(parents=True, exist_ok=True)
    cleaned.to_parquet(args.output, index=False)
    dropped = len(raw) - len(cleaned)
    print(
        f"{len(raw)} raw -> {len(cleaned)} cleaned ({dropped} dropped as agtron typos)"
    )
    if rates is not None and cpi is not None:
        print(f"prices in {args.baseline_date} dollars")
    print(f"wrote {args.output}")


def fetch_cpi_command(argv: list[str] | None = None) -> None:
    """Update the BLS consumer price index table."""
    parser = argparse.ArgumentParser(description=fetch_cpi_command.__doc__)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_CPI_PATH,
        help="CPI table to update in place.",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        help="rebuild a specific range instead of the recent window; the "
        "unkeyed API allows spans of up to ten years",
    )
    parser.add_argument("--end-year", type=int)
    args = parser.parse_args(argv)

    _configure_logging()
    if (args.start_year is None) != (args.end_year is None):
        raise SystemExit("--start-year and --end-year must be given together.")

    try:
        table = fetch_cpi(
            args.output, start_year=args.start_year, end_year=args.end_year
        )
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Could not update the CPI table: {exc}") from exc

    months = table[MONTH_COLUMNS].map(lambda v: str(v).strip() not in {"", "nan"})
    print(
        f"{int(months.to_numpy().sum())} monthly CPI values across "
        f"{len(table)} years -> {args.output}"
    )


def refresh_data(argv: list[str] | None = None) -> None:
    """Run the whole collection-and-cleaning pipeline in dependency order.

    The four steps have to run in this order and each reads what the previous
    one wrote, which is the kind of thing that is easy to get wrong by hand:

        scrape  ->  resolve roasters  ->  fetch rates  ->  clean

    Everything downstream of this is analysis, which belongs in a notebook.
    """
    parser = argparse.ArgumentParser(description=refresh_data.__doc__)
    parser.add_argument(
        "--full",
        action="store_true",
        help="re-fetch and re-parse every review, not only what changed",
    )
    parser.add_argument(
        "--baseline-date",
        default=DEFAULT_BASELINE_DATE,
        help=f"month whose dollars adjusted prices use (default "
        f"{DEFAULT_BASELINE_DATE})",
    )
    parser.add_argument(
        "--skip-scrape",
        action="store_true",
        help="rebuild from the reviews already held, making no review requests",
    )
    args = parser.parse_args(argv)

    raw = DEFAULT_OUTPUT_DIR / "reviews.csv"
    steps: list[tuple[str, Callable[[], None]]] = []
    if not args.skip_scrape:
        steps.append(
            ("scrape", lambda: scrape_reviews(["--full"] if args.full else []))
        )
    steps += [
        ("resolve roasters", lambda: resolve_roasters([str(raw)])),
        ("fetch exchange rates", lambda: fetch_exchange_rates([])),
        ("fetch CPI", lambda: fetch_cpi_command([])),
        (
            "clean",
            lambda: clean_reviews_command(["--baseline-date", args.baseline_date]),
        ),
    ]

    for number, (name, run) in enumerate(steps, start=1):
        print(f"\n=== {number}/{len(steps)}  {name} " + "=" * (40 - len(name)))
        run()

    print(f"\nDone. The cleaned layer is at {DATA_DIR / 'clean' / 'reviews.parquet'}.")
