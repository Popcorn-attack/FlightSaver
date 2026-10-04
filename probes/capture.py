"""Capture real search payloads as test fixtures (run on GitHub Actions).

Saves tests/fixtures/live/<name>.json.gz = {"page_url", "captured": [{"url", "post", "body"}]}
for the API responses that carry flight results, one-way and round-trip.
"""

from __future__ import annotations

import gzip
import json
import resource
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("tests/fixtures/live")
DEP = date.today() + timedelta(days=60)
RET = DEP + timedelta(days=14)
D, R = DEP.isoformat(), RET.isoformat()

# name -> (page url, substring identifying the result API, max wait seconds)
TARGETS = {
    "trip_com_ow": (
        f"https://uk.trip.com/flights/showfarefirst?dcity=lon&acity=sha&ddate={D}"
        "&triptype=ow&class=y&quantity=1&locale=en-GB&curr=GBP",
        "*",
        30,
    ),
    "trip_com_rt": (
        f"https://uk.trip.com/flights/showfarefirst?dcity=lon&acity=sha&ddate={D}&rdate={R}"
        "&triptype=rt&class=y&quantity=1&locale=en-GB&curr=GBP",
        "*",
        30,
    ),
    "ctrip_ow": (
        f"https://flights.ctrip.com/online/list/oneway-lon-sha?depdate={D}&cabin=y_s&adult=1",
        "/international/search/api/search/",
        30,
    ),
    "ctrip_rt": (
        f"https://flights.ctrip.com/online/list/round-lon-sha?depdate={D}_{R}&cabin=y_s&adult=1",
        "/international/search/api/search/",
        30,
    ),
    "kayak_ow": (
        f"https://www.kayak.co.uk/flights/LHR-PVG/{D}?sort=price_a",
        "/flights/poll",
        40,
    ),
    "kayak_rt": (
        f"https://www.kayak.co.uk/flights/LHR-PVG/{D}/{R}?sort=price_a",
        "/flights/poll",
        40,
    ),
    "google_ow": (
        f"https://www.google.com/travel/flights?q=Flights%20from%20LHR%20to%20PVG%20on%20{D}"
        "%20one%20way&hl=en-GB&curr=GBP",
        "*",
        20,
    ),
}
ONLY = set(sys.argv[1:])
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


def rss_mb() -> float:
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    kids = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return (own + kids) / 1024


def capture(browser, name: str, url: str, marker: str, wait: int) -> None:
    print(f"\n===== {name} =====\n{url}", flush=True)
    ctx = browser.new_context(locale="en-GB", timezone_id="Europe/London", user_agent=UA,
                              viewport={"width": 1366, "height": 900})
    # Skip heavy resources; results come from XHR JSON.
    ctx.route("**/*", lambda route: route.abort()
              if route.request.resource_type in ("image", "media", "font")
              else route.continue_())
    page = ctx.new_page()
    captured = []

    def on_response(resp):
        if marker == "*":
            if resp.request.resource_type not in ("xhr", "fetch", "eventsource"):
                return
        elif not marker or marker not in resp.url:
            return
        try:
            body = resp.json()
        except Exception:
            try:
                body = resp.text()
            except Exception:
                return
            if marker != "*":
                return
        captured.append({"url": resp.url, "status": resp.status,
                         "ctype": resp.headers.get("content-type", ""),
                         "post": resp.request.post_data, "body": body})
        size = len(json.dumps(body)) if not isinstance(body, str) else len(body)
        print(f"  captured {resp.status} {size}B {resp.headers.get('content-type', '')[:40]} "
              f"{resp.url[:150]} at {time.time() - t0:.1f}s", flush=True)

    page.on("response", on_response)
    t0 = time.time()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(wait * 1000)
    except Exception as exc:
        print(f"  goto error: {exc}")
    print(f"  final url: {page.url[:200]}\n  title: {page.title()!r}")
    data = {"page_url": url, "final_url": page.url, "captured": captured}
    if name.startswith("google"):
        data["html"] = page.content()
        print(f"  html {len(data['html'])} chars, has ds:1 = {'ds:1' in data['html']}")
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"  saved {path} ({path.stat().st_size // 1024} KB), {len(captured)} responses, "
          f"peak rss {rss_mb():.0f} MB")
    ctx.close()


if __name__ == "__main__":
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for name, (url, marker, wait) in TARGETS.items():
            if ONLY and name not in ONLY:
                continue
            try:
                capture(browser, name, url, marker, wait)
            except Exception as exc:
                print(f"{name} failed: {exc}")
        browser.close()
