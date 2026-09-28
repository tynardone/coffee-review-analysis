"""Tests for the shared HTTP rules: what is retried, what is not, and how.

The network is mocked with respx, and backoff waits are replaced with zero or
recorded, so nothing here sleeps or touches a real site.
"""

import asyncio

import httpx
import pytest
import respx

from coffee import http_client
from coffee.http_client import (
    BASE_DELAY,
    HEADERS,
    JITTER,
    MAX_DELAY,
    async_client,
    fetch,
    request_json,
    retry_delay,
    sync_client,
)

URL = "https://site.test/review/a/"
API = "https://api.test/data"


@pytest.fixture
def no_backoff(monkeypatch):
    """Record each backoff instead of waiting it out."""
    delays = []

    def record(attempt, retry_after):
        delays.append((attempt, retry_after))
        return 0.0

    monkeypatch.setattr(http_client, "retry_delay", record)
    return delays


def run_fetch(url=URL, retries=5):
    async def go():
        async with async_client() as client:
            return await fetch(url, client, asyncio.Semaphore(2), retries=retries)

    return asyncio.run(go())


# --------------------------------------------------------------------------
# fetch: the scrape's async GET
# --------------------------------------------------------------------------


@respx.mock
def test_fetch_returns_the_page_and_identifies_itself():
    route = respx.get(URL).mock(return_value=httpx.Response(200, text="<html/>"))
    assert run_fetch() == "<html/>"
    user_agent = route.calls[0].request.headers["user-agent"]
    assert user_agent == HEADERS["user-agent"]
    assert "Mozilla" not in user_agent


@respx.mock
def test_fetch_retries_a_transient_status(no_backoff):
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, text="ok")]
    )
    assert run_fetch() == "ok"
    assert route.call_count == 2


@respx.mock
def test_fetch_retries_a_connection_error(no_backoff):
    respx.get(URL).mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, text="ok")]
    )
    assert run_fetch() == "ok"


@respx.mock
def test_fetch_does_not_retry_a_permanent_error(no_backoff):
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    assert run_fetch() is None
    assert route.call_count == 1
    assert no_backoff == []


@respx.mock
def test_fetch_gives_up_after_its_retries(no_backoff):
    route = respx.get(URL).mock(return_value=httpx.Response(503))
    assert run_fetch(retries=3) is None
    assert route.call_count == 3


@respx.mock
def test_fetch_passes_retry_after_to_the_backoff(no_backoff):
    respx.get(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, text="ok"),
        ]
    )
    run_fetch()
    assert no_backoff == [(0, "7")]


class SlowSite:
    """A fake client that records concurrency and the order requests start."""

    def __init__(self, first_status=None):
        self.in_flight = self.peak = 0
        self.started: list[str] = []
        self.first_status = first_status or {}

    async def get(self, url):
        self.started.append(url)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        await asyncio.sleep(0.01)
        self.in_flight -= 1
        status = self.first_status.pop(url, 200)
        return httpx.Response(status, text=url)


def test_the_semaphore_caps_requests_in_flight():
    site = SlowSite()

    async def go():
        semaphore = asyncio.Semaphore(2)
        urls = [f"u{i}" for i in range(8)]
        return await asyncio.gather(*(fetch(u, site, semaphore) for u in urls))

    assert all(asyncio.run(go()))
    assert site.peak == 2


def test_the_semaphore_is_released_during_backoff(monkeypatch):
    """While one URL waits to retry, another gets its slot."""
    monkeypatch.setattr(http_client, "retry_delay", lambda attempt, after: 0.05)
    site = SlowSite(first_status={"a": 503})

    async def go():
        semaphore = asyncio.Semaphore(1)
        return await asyncio.gather(
            fetch("a", site, semaphore), fetch("b", site, semaphore)
        )

    assert asyncio.run(go()) == ["a", "b"]
    assert site.started == ["a", "b", "a"]  # b ran during a's backoff


# --------------------------------------------------------------------------
# request_json: the reference APIs' sync request
# --------------------------------------------------------------------------


def call_json(method="GET", sleeps=None, **kwargs):
    with sync_client() as client:
        return request_json(
            client,
            method,
            API,
            sleep=(sleeps.append if sleeps is not None else lambda s: None),
            **kwargs,
        )


@respx.mock
def test_request_json_retries_then_returns_the_body():
    route = respx.get(API).mock(
        side_effect=[httpx.Response(502), httpx.Response(200, json={"x": 1})]
    )
    sleeps: list[float] = []
    assert call_json(sleeps=sleeps) == {"x": 1}
    assert route.call_count == 2 and len(sleeps) == 1


@respx.mock
def test_request_json_sends_params_and_a_json_body():
    route = respx.post(API).mock(return_value=httpx.Response(200, json={}))
    call_json("POST", params={"k": "v"}, json={"seriesid": ["X"]})
    request = route.calls[0].request
    assert request.url.params["k"] == "v"
    assert request.content == b'{"seriesid":["X"]}'


@respx.mock
def test_request_json_raises_at_once_on_a_permanent_error():
    route = respx.get(API).mock(return_value=httpx.Response(401))
    with pytest.raises(httpx.HTTPStatusError):
        call_json()
    assert route.call_count == 1


@respx.mock
def test_request_json_raises_once_retries_run_out():
    route = respx.get(API).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        call_json(retries=3)
    assert route.call_count == 3


@respx.mock
def test_request_json_raises_a_persistent_connection_error():
    respx.get(API).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(httpx.ConnectError):
        call_json(retries=2)


# --------------------------------------------------------------------------
# retry_delay
# --------------------------------------------------------------------------


def test_a_numeric_retry_after_is_honored():
    assert retry_delay(0, "12") == 12.0


@pytest.mark.parametrize("attempt", [0, 1, 2, 10])
def test_backoff_grows_exponentially_and_is_capped(attempt):
    delay = retry_delay(attempt, None)
    floor = min(BASE_DELAY * 2**attempt, MAX_DELAY)
    assert floor <= delay <= floor + JITTER
