"""Discover how travel sites deliver flight results (run on GitHub Actions).

For each site: open the prefilled search URL in headless Chromium, record the
JSON responses the page fetches, and print a compact summary to the log so the
provider parsers can be written against real payloads.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta

from playwright.sync_api import sync_playwright

DEP = date.today() + timedelta(days=60)
D = DEP.isoformat()
YYMMDD = DEP.strftime("%y%m%d")

SITES = {
    "trip_com": f"https://uk.trip.com/flights/showfarefirst?dcity=lon&acity=sha&ddate={D}"
    "&triptype=ow&class=y&quantity=1&locale=en-GB&curr=GBP",
    "ctrip": f"https://flights.ctrip.com/online/list/oneway-lon-sha?depdate={D}&cabin=y_s&adult=1",
    "skyscanner": f"https://www.skyscanner.net/transport/flights/lhr/pvg/{YYMMDD}/?adultsv2=1"
    "&cabinclass=economy&rtn=0&currency=GBP",
    "kayak": f"https://www.kayak.co.uk/flights/LHR-PVG/{D}?sort=price_a",
    "qunar": "https://flight.qunar.com/",
}
ONLY = set(sys.argv[1:])


def price_paths(obj, path="", out=None, limit=12):
    """Find keys that look like prices, to locate the fare data in a payload."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}"
            if any(w in k.lower() for w in ("price", "fare", "amount")) and isinstance(
                v, (int, float, str)
            ):
                out.append(f"{p}={v}")
            price_paths(v, p, out, limit)
    elif isinstance(obj, list) and obj:
        price_paths(obj[0], path + "[0]", out, limit)
    return out


def probe(pw, name: str, url: str) -> None:
    print(f"\n===== {name} =====\n{url}", flush=True)
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(
        locale="en-GB",
        timezone_id="Europe/London",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
        viewport={"width": 1366, "height": 900},
    )
    page = ctx.new_page()
    seen = []

    def on_response(resp):
        ctype = resp.headers.get("content-type", "")
        if "json" not in ctype:
            return
        try:
            body = resp.body()
        except Exception:
            return
        seen.append((resp, body))

    page.on("response", on_response)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(25000)
    except Exception as exc:
        print(f"goto error: {exc}")
    print(f"final url: {page.url}\ntitle: {page.title()!r}")
    text = page.inner_text("body")[:600].replace("\n", " | ") if page.url else ""
    print(f"body text: {text}")
    big = sorted(seen, key=lambda rb: -len(rb[1]))
    print(f"json responses: {len(seen)}")
    for resp, body in big[:8]:
        req = resp.request
        print(f"\n-- {req.method} {resp.status} {len(body)}B {req.url[:300]}")
        if req.post_data:
            print(f"   post: {req.post_data[:1200]}")
        hdrs = {k: v for k, v in req.headers.items() if k.lower() not in ("cookie",)}
        print(f"   req headers: {json.dumps(hdrs)[:800]}")
        try:
            data = json.loads(body)
        except Exception:
            continue
        if isinstance(data, dict):
            print(f"   keys: {list(data)[:20]}")
        print(f"   price-like: {price_paths(data)}")
        print(f"   head: {body[:1500].decode('utf-8', 'replace')}")
    page.screenshot(path=f"probe-{name}.png")
    browser.close()


def probe_google() -> None:
    print("\n===== google_flights (fast-flights) =====", flush=True)
    try:
        import fast_flights as ff

        q = ff.create_query(
            flights=[ff.FlightQuery(date=D, from_airport="LHR", to_airport="PVG")],
            currency="GBP",
            language="en-GB",
        )
        t = time.time()
        res = ff.get_flights(q)
        print(f"ok: {len(res)} results in {time.time() - t:.1f}s")
        for f in list(res)[:3]:
            print(f"   {f.price} {f.airlines} stops={len(f.flights) - 1}")
    except Exception as exc:
        print(f"google error: {type(exc).__name__}: {str(exc)[:500]}")


if __name__ == "__main__":
    if not ONLY or "google" in ONLY:
        probe_google()
    with sync_playwright() as pw:
        for name, url in SITES.items():
            if ONLY and name not in ONLY:
                continue
            try:
                probe(pw, name, url)
            except Exception as exc:
                print(f"{name} failed: {exc}")
