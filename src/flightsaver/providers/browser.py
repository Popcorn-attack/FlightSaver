"""Headless-browser capture for sites whose result APIs need browser-generated tokens.

Opens the site's own search page in Chromium and records the JSON its result API
returns, so we never have to reproduce signing or anti-bot tokens. Needs the
``browser`` extra (``uv sync --extra browser`` + ``playwright install chromium``).
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from flightsaver.providers.base import ProviderError

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
# Each Chromium instance needs ~200-300 MB; small hosts should keep this at 1.
_slots = threading.BoundedSemaphore(int(os.environ.get("FLIGHTSAVER_BROWSER_CONCURRENCY", "2")))


SKIP_TYPES = ("image", "media", "font")
LEAN_SKIP_TYPES = (*SKIP_TYPES, "stylesheet", "manifest", "texttrack", "other")
# Analytics/ads that cost memory and never carry results.
TRACKERS = (
    "googletagmanager",
    "google-analytics",
    "doubleclick",
    "googlesyndication",
    "facebook",
    "hotjar",
    "clarity.ms",
    "criteo",
    "bing.com",
    "tiktok",
    "snapchat",
    "pinterest",
    "adservice",
    "optimizely",
    "newrelic",
    "nr-data",
    "sentry",
    "datadoghq",
    "cookielaw",
    "onetrust",
    "quantserve",
    "taboola",
    "outbrain",
)


def _lean() -> bool:
    """Low-memory mode for small hosts (FLIGHTSAVER_BROWSER_LEAN=1, e.g. 512 MB)."""
    return os.environ.get("FLIGHTSAVER_BROWSER_LEAN", "0") == "1"


def _launch_args() -> list[str]:
    args = ["--disable-dev-shm-usage", "--no-zygote", "--disable-gpu"]
    if _lean():
        args += [
            "--renderer-process-limit=1",
            "--disable-site-isolation-trials",
            "--disable-features=site-per-process,IsolateOrigins,Translate,MediaRouter",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-component-update",
            "--js-flags=--max-old-space-size=192",
        ]
    return args


@dataclass
class Captured:
    url: str
    status: int
    post: str | None
    body: object
    at: float  # seconds after navigation started


def parse_sse(text: str) -> list:
    """JSON payloads of a server-sent-events body (``data: {...}`` lines)."""
    events = []
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                events.append(json.loads(line[5:]))
            except ValueError:
                continue
    return events


def _decode(resp):
    """JSON body, the last JSON event of an event stream, or else the raw text."""
    if "event-stream" in resp.headers.get("content-type", ""):
        try:
            events = parse_sse(resp.text())
        except Exception:
            return None
        return events[-1] if events else None
    try:
        return resp.json()
    except Exception:
        pass
    try:
        # e.g. Google's ")]}'"-prefixed batch responses; providers decode these.
        return resp.text()
    except Exception:
        return None


def available() -> bool:
    """Playwright is installed and not disabled (FLIGHTSAVER_BROWSER=0 on small hosts)."""
    if os.environ.get("FLIGHTSAVER_BROWSER", "1") == "0":
        return False
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def capture_json(
    url: str,
    match: Callable[[str], bool],
    done: Callable[[list[Captured]], bool],
    timeout: float = 30.0,
    locale: str = "en-GB",
    allowed_hosts: tuple[str, ...] = (),
) -> list[Captured]:
    """Load ``url`` and collect JSON responses whose URL satisfies ``match``.

    Stops as soon as ``done(captured)`` is true, or after ``timeout`` seconds. On a
    small host a page can run out of memory; it is retried once, as the crash
    frees what the first attempt held. In lean mode only ``allowed_hosts`` (the
    site's own domains) are loaded.
    """
    try:
        return _capture_once(url, match, done, timeout, locale, allowed_hosts)
    except Exception as exc:
        if "crash" not in str(exc).lower():
            raise
    return _capture_once(url, match, done, timeout, locale, allowed_hosts)


def _host_allowed(request_url: str, allowed: tuple[str, ...]) -> bool:
    host = urlsplit(request_url).hostname or ""
    return not allowed or any(host == a or host.endswith("." + a) for a in allowed)


def _capture_once(url, match, done, timeout, locale, allowed_hosts) -> list[Captured]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ProviderError("playwright not installed (uv sync --extra browser)") from exc

    captured: list[Captured] = []
    with _slots, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=_launch_args())
        try:
            ctx = browser.new_context(
                locale=locale,
                timezone_id="Europe/London",
                user_agent=USER_AGENT,
                viewport={"width": 1366, "height": 900},
            )
            # Results come from XHR JSON; skip heavy assets (and more in lean mode).
            skip_types = LEAN_SKIP_TYPES if _lean() else SKIP_TYPES
            ctx.route(
                "**/*",
                lambda route: (
                    route.abort()
                    if route.request.resource_type in skip_types
                    or (_lean() and any(t in route.request.url for t in TRACKERS))
                    or (_lean() and not _host_allowed(route.request.url, allowed_hosts))
                    else route.continue_()
                ),
            )
            page = ctx.new_page()
            start = time.monotonic()

            def on_response(resp):
                if not match(resp.url):
                    return
                body = _decode(resp)
                if body is None:
                    return
                captured.append(
                    Captured(
                        resp.url,
                        resp.status,
                        resp.request.post_data,
                        body,
                        time.monotonic() - start,
                    )
                )

            page.on("response", on_response)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            except Exception as exc:
                raise ProviderError(f"could not load {url}: {exc}") from exc
            while time.monotonic() - start < timeout and not done(captured):
                page.wait_for_timeout(500)
            if "captcha" in page.url:
                raise ProviderError("blocked by a captcha")
        finally:
            browser.close()
    return captured
