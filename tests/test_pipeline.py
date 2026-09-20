"""Tests for the incremental fetch plan.

This is the decision that turns a 29-minute run into a one-minute one, so the
cases that matter are the ones where it could wrongly SKIP a page — a skipped
page is a row that stays quietly wrong, while a wrongly re-fetched one costs a
single request.
"""

from datetime import date

from coffee.pipeline import plan_fetch

JAN = date(2026, 1, 1)
JUN = date(2026, 6, 1)


def test_a_url_never_seen_is_fetched():
    to_fetch, _ = plan_fetch({"new": JAN}, {})
    assert to_fetch == {"new"}


def test_an_unchanged_url_is_skipped():
    to_fetch, _ = plan_fetch({"u": JAN}, {"u": JAN})
    assert to_fetch == set()


def test_a_newer_lastmod_is_fetched():
    to_fetch, _ = plan_fetch({"u": JUN}, {"u": JAN})
    assert to_fetch == {"u"}


def test_an_older_lastmod_upstream_is_not_fetched():
    """The site going backwards is odd, but it is not evidence of change."""
    to_fetch, _ = plan_fetch({"u": JAN}, {"u": JUN})
    assert to_fetch == set()


def test_unknown_upstream_date_is_fetched():
    """Cannot prove it is unchanged, so assume it changed."""
    to_fetch, _ = plan_fetch({"u": None}, {"u": JAN})
    assert to_fetch == {"u"}


def test_unknown_held_date_is_fetched():
    """Rows predating sitemap discovery have no recorded freshness."""
    to_fetch, _ = plan_fetch({"u": JAN}, {"u": None})
    assert to_fetch == {"u"}


def test_urls_no_longer_listed_are_reported_not_fetched():
    to_fetch, retired = plan_fetch({"live": JAN}, {"live": JAN, "gone": JAN})
    assert to_fetch == set()
    assert retired == {"gone"}


def test_retired_urls_are_never_silently_dropped():
    """A review that disappears upstream cannot be re-fetched, so the held copy
    is the only one. plan_fetch reports it; nothing deletes it."""
    _, retired = plan_fetch({}, {"gone": JAN})
    assert retired == {"gone"}


def test_a_realistic_mixture():
    discovered = {"unchanged": JAN, "changed": JUN, "new": JUN, "undated": None}
    known = {"unchanged": JAN, "changed": JAN, "gone": JAN, "undated": JAN}
    to_fetch, retired = plan_fetch(discovered, known)
    assert to_fetch == {"changed", "new", "undated"}
    assert retired == {"gone"}
