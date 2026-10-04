"""Headless-browser capture for sites whose result APIs need browser-generated tokens.

Opens the site's own search page in Chromium and records the JSON its result API
returns, so we never have to reproduce signing or anti-bot tokens. Needs the
``browser`` extra (``uv sync --extra browser`` + ``playwright install chromium``).
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from flightsaver.providers.base import ProviderError

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
# Each Chromium instance needs ~200-300 MB; small hosts should keep this at 1.
_slots = threading.BoundedSemaphore(int(os.environ.get("FLIGHTSAVER_BROWSER_CONCURRENCY", "2")))


@dataclass
class Captured:
    url: str
    status: int
    post: str | None
    body: object
    at: float  # seconds after navigation started


def available() -> bool:
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
) -> list[Captured]:
    """Load ``url`` and collect JSON responses whose URL satisfies ``match``.

    Stops as soon as ``done(captured)`` is true, or after ``timeout`` seconds.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ProviderError("playwright not installed (uv sync --extra browser)") from exc

    captured: list[Captured] = []
    with _slots, sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, args=["--disable-dev-shm-usage", "--no-zygote", "--disable-gpu"]
        )
        try:
            ctx = browser.new_context(
                locale=locale,
                timezone_id="Europe/London",
                user_agent=USER_AGENT,
                viewport={"width": 1366, "height": 900},
            )
            # Results come from XHR JSON; skip heavy assets.
            ctx.route(
                "**/*",
                lambda route: (
                    route.abort()
                    if route.request.resource_type in ("image", "media", "font")
                    else route.continue_()
                ),
            )
            page = ctx.new_page()
            start = time.monotonic()

            def on_response(resp):
                if not match(resp.url):
                    return
                try:
                    body = resp.json()
                except Exception:
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
