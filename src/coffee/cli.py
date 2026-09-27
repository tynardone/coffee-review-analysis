"""Command-line entry points for the pipeline steps.

Each function here is registered in ``pyproject.toml`` under
``[project.scripts]``, so the steps run as ``scrape-reviews``,
``fetch-exchange-rates`` and ``resolve-roasters`` once the package is
installed. Argument parsing and logging setup live here; the work itself lives
in the modules these import, so it stays importable from notebooks and tests.

Each command loads the settings once, through
:func:`~coffee.settings.get_settings`, and takes its defaults from them: file
paths, the log level, concurrency, the baseline month, the roaster thresholds.
Command-line flags override those for a single run. Nothing below this layer
reads the environment.
"""

import argparse
import asyncio
import inspect
import logging
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import requests
from pydantic import ValidationError

from coffee.clean import clean_reviews
from coffee.cpi import MONTH_COLUMNS, fetch_cpi
from coffee.exchange_rates import (
    fetch_rates,
    load_rates,
    load_review_dates,
    unfetched_dates,
)
from coffee.page_store import PageStore
from coffee.parse import parse_saved_reviews
from coffee.prices import load_cpi, load_exchange_rates
from coffee.review_store import CsvReviewStore
from coffee.roasters import (
    format_resolution_report,
    format_violations,
    load_decisions,
    promote_reviewed,
    resolve,
    unpromoted_verdicts,
)
from coffee.scrape import scrape_all_reviews
from coffee.settings import Settings, get_settings

__all__ = [
    "clean_reviews_command",
    "fetch_cpi_command",
    "refresh_data",
    "fetch_exchange_rates",
    "parse_reviews",
    "resolve_roasters",
    "scrape_reviews",
]

logger = logging.getLogger(__name__)


def _settings() -> Settings:
    """Load and validate the settings, or stop with a readable error.

    Input values are left out of the message: one of them could be the API key.
    """
    try:
        return get_settings()
    except ValidationError as exc:
        problems = "\n".join(
            f"  {'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_input=False)
        )
        raise SystemExit(
            "Invalid settings (config/settings.toml, .env or COFFEE_* "
            f"variables):\n{problems}"
        ) from exc


def _configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def _positive_int(value: str) -> int:
    """argparse type that rejects non-positive integers."""
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value!r}")
    return parsed


def scrape_reviews(argv: list[str] | None = None) -> None:
    """Download new and changed review pages into data/downloaded/."""
    settings = _settings()
    parser = argparse.ArgumentParser(description=scrape_reviews.__doc__)
    parser.add_argument(
        "--full",
        action="store_true",
        help="re-download every review, not only new and changed ones. Rarely "
        "needed: after a parser change, use parse-reviews --full instead.",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        help="fetch at most this many pages, for a quick trial run",
    )
    parser.add_argument(
        "-c",
        "--concurrency",
        type=_positive_int,
        default=settings.scrape.concurrency,
        help="maximum number of concurrent requests (default from settings: "
        "%(default)s)",
    )
    args = parser.parse_args(argv)

    _configure_logging(settings)
    pages = PageStore(settings.downloaded_dir)
    result = asyncio.run(
        scrape_all_reviews(pages, args.concurrency, full=args.full, limit=args.limit)
    )
    print(f"saved {result.saved} page(s) to {pages.directory}")
    if result.failed:
        print(f"{len(result.failed)} page(s) failed and will be retried next run")
    if result.retired:
        print(f"{result.retired} saved review(s) are no longer on the site; kept")


def parse_reviews(argv: list[str] | None = None) -> None:
    """Parse downloaded review pages into data/parsed/reviews.csv. No network."""
    settings = _settings()
    parser = argparse.ArgumentParser(description=parse_reviews.__doc__)
    parser.add_argument(
        "--full",
        action="store_true",
        help="re-parse every saved page, not only those not yet parsed. Use "
        "after changing the parser.",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        help="parse at most this many pages, for a quick check of a parser change",
    )
    args = parser.parse_args(argv)

    _configure_logging(settings)
    store = CsvReviewStore(settings.parsed_reviews)
    result = parse_saved_reviews(
        PageStore(settings.downloaded_dir), store, full=args.full, limit=args.limit
    )
    print(f"parsed {result.parsed} page(s); {store.csv_path} holds {result.total}")
    if result.failed:
        print(
            f"{len(result.failed)} page(s) failed to parse and kept their previous "
            f"row, starting with {result.failed[0]}"
        )


def fetch_exchange_rates(argv: list[str] | None = None) -> None:
    """Fetch historical exchange rates for the dates in a scraped reviews file."""
    settings = _settings()
    parser = argparse.ArgumentParser(description=fetch_exchange_rates.__doc__)
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=settings.parsed_reviews,
        help="Reviews file whose review months need rates. Defaults to the "
        "parsed reviews, which keeps this independent of the cleaning step it "
        "feeds; the cleaned reviews are also accepted.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=settings.exchange_rates,
        help="Destination JSON file for exchange rates.",
    )
    parser.add_argument(
        "--refetch",
        action="store_true",
        help="re-request dates already held. Rates for a past date do not "
        "change, so this is only for repairing a corrupt file.",
    )
    args = parser.parse_args(argv)

    _configure_logging(settings)
    if settings.openexchangerates_api_id is None:
        raise SystemExit(
            "COFFEE_OPENEXCHANGERATES_API_ID is not set; add it to .env "
            "(see .env.example)."
        )
    app_id = settings.openexchangerates_api_id.get_secret_value()

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
    settings = _settings()
    parser = argparse.ArgumentParser(description=resolve_roasters.__doc__)
    parser.add_argument("infile", type=Path, help="CSV containing the names")
    parser.add_argument("--column", default="roaster", help="column holding the names")
    parser.add_argument(
        "--location-column",
        default="roaster_location",
        help="column holding each roaster's location; '' disables the signal",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=settings.roasters_dir,
        help="where the crosswalk and the review queue are written",
    )
    parser.add_argument(
        "--decisions",
        type=Path,
        default=settings.roaster_decisions,
        help="CSV of adjudicated pairs, kept with the other seed files rather "
        "than under --outdir. Missing is fine; it is created as you record "
        "verdicts.",
    )
    parser.add_argument(
        "--auto",
        type=int,
        default=settings.roasters.auto_threshold,
        help="score >= this: merge automatically (raise if you see false "
        "merges; default from settings: %(default)s)",
    )
    parser.add_argument(
        "--review",
        type=int,
        default=settings.roasters.review_threshold,
        help="score in [review, auto): send to the review queue (lower it if "
        "true matches are being missed entirely; default from settings: "
        "%(default)s)",
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

    decisions_path = args.decisions
    review_path = args.outdir / settings.roaster_review_queue.name

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
    crosswalk_path = args.outdir / settings.roaster_crosswalk.name
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
    """Build the cleaned dataset from the parsed reviews."""
    settings = _settings()
    parser = argparse.ArgumentParser(description=clean_reviews_command.__doc__)
    parser.add_argument("-i", "--input", type=Path, default=settings.parsed_reviews)
    parser.add_argument("-o", "--output", type=Path, default=settings.cleaned_reviews)
    parser.add_argument(
        "--crosswalk",
        type=Path,
        default=settings.roaster_crosswalk,
        help="roaster crosswalk; skipped if absent",
    )
    parser.add_argument(
        "--rates",
        type=Path,
        default=settings.exchange_rates,
        help="historical exchange rates; price conversion is skipped if absent",
    )
    parser.add_argument(
        "--cpi",
        type=Path,
        default=settings.cpi,
        help="BLS CPI-U table; inflation adjustment is skipped if absent",
    )
    parser.add_argument(
        "--baseline-date",
        default=settings.prices.baseline_date.isoformat(),
        help="the month whose dollars adjusted prices are expressed in "
        "(default from settings: %(default)s). Must be a month the CPI table "
        "covers.",
    )
    args = parser.parse_args(argv)

    _configure_logging(settings)
    parsed = pd.read_csv(args.input)
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
            parsed,
            exchange_rates=rates,
            cpi=cpi,
            crosswalk=crosswalk,
            baseline_date=args.baseline_date,
        )
    except ValueError as exc:  # an unusable baseline is a user error
        raise SystemExit(str(exc)) from exc

    args.output.parent.mkdir(parents=True, exist_ok=True)
    cleaned.to_parquet(args.output, index=False)
    dropped = len(parsed) - len(cleaned)
    print(
        f"{len(parsed)} parsed -> {len(cleaned)} cleaned "
        f"({dropped} dropped as agtron typos)"
    )
    if rates is not None and cpi is not None:
        print(f"prices in {args.baseline_date} dollars")
    print(f"wrote {args.output}")


def fetch_cpi_command(argv: list[str] | None = None) -> None:
    """Update the BLS consumer price index table."""
    settings = _settings()
    parser = argparse.ArgumentParser(description=fetch_cpi_command.__doc__)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=settings.cpi,
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

    _configure_logging(settings)
    if (args.start_year is None) != (args.end_year is None):
        raise SystemExit("--start-year and --end-year must be given together.")

    try:
        table = fetch_cpi(
            args.output, start_year=args.start_year, end_year=args.end_year
        )
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Could not update the CPI table: {exc}") from exc

    months = table[list(MONTH_COLUMNS)].map(lambda v: str(v).strip() not in {"", "nan"})
    print(
        f"{int(months.to_numpy().sum())} monthly CPI values across "
        f"{len(table)} years -> {args.output}"
    )


def refresh_data(argv: list[str] | None = None) -> None:
    """Run the whole collection-and-cleaning pipeline in dependency order.

    The six steps have to run in this order, since later steps read what
    earlier ones wrote, which is easy to get wrong by hand:

        scrape -> parse -> resolve roasters -> fetch rates -> fetch CPI -> clean

    Everything downstream of this is analysis, which belongs in a notebook.
    """
    settings = _settings()
    parser = argparse.ArgumentParser(
        # Raw, so the step diagram keeps its own line in --help.
        description=inspect.cleandoc(refresh_data.__doc__ or ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="re-download and re-parse every review, not only what changed. "
        "Exchange rates and CPI stay incremental.",
    )
    parser.add_argument(
        "--baseline-date",
        default=settings.prices.baseline_date.isoformat(),
        help="month whose dollars adjusted prices use (default from settings: "
        "%(default)s)",
    )
    parser.add_argument(
        "--skip-scrape",
        action="store_true",
        help="make no review requests; parse and rebuild from saved pages",
    )
    args = parser.parse_args(argv)

    parsed = settings.parsed_reviews
    full = ["--full"] if args.full else []
    steps: list[tuple[str, Callable[[], None]]] = []
    if not args.skip_scrape:
        steps.append(("scrape", lambda: scrape_reviews(full)))
    steps += [
        ("parse", lambda: parse_reviews(full)),
        ("resolve roasters", lambda: resolve_roasters([str(parsed)])),
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

    print(f"\nDone. The cleaned dataset is at {settings.cleaned_reviews}.")
