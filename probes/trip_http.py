"""Can Trip.com's list API be called without a browser? (run on GitHub Actions)

1. Load the search page in Chromium and record the FlightListSearchSSE request.
2. Replay it over plain HTTP (browser-like TLS via primp) with fewer and fewer
   of the browser's headers/cookies, and report which variants return results.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import primp
from playwright.sync_api import sync_playwright

D = (date.today() + timedelta(days=60)).isoformat()
PAGE = (f"https://uk.trip.com/flights/showfarefirst?dcity=lon&acity=sha&ddate={D}"
        "&triptype=ow&class=y&quantity=1&locale=en-GB&curr=GBP")


def count(text: str) -> int:
    n = 0
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                n = max(n, len(json.loads(line[5:]).get("itineraryList") or []))
            except ValueError:
                pass
    return n


def main() -> None:
    rec = {}
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        ctx = b.new_context(locale="en-GB")
        page = ctx.new_page()

        def on_request(req):
            if "FlightListSearchSSE" in req.url and not rec:
                rec.update(url=req.url, headers=req.all_headers(), body=req.post_data)

        page.on("request", on_request)
        page.goto(PAGE, wait_until="domcontentloaded")
        page.wait_for_timeout(15000)
        rec["cookies"] = {c["name"]: c["value"] for c in ctx.cookies()}
        b.close()
    if not rec.get("url"):
        print("no SSE request seen")
        return
    h = {k: v for k, v in rec["headers"].items() if not k.startswith(":")}
    print("url:", rec["url"])
    print("header names:", sorted(h))
    print("cookie names:", sorted(rec["cookies"]))
    variants = {
        "full (headers+cookies)": (h, rec["cookies"]),
        "no token header": ({k: v for k, v in h.items() if k != "token"}, rec["cookies"]),
        "no cookies": ({k: v for k, v in h.items() if k != "cookie"}, {}),
        "no token, no cookies": (
            {k: v for k, v in h.items() if k not in ("token", "cookie")}, {}),
        "minimal headers": ({"content-type": "application/json", "accept": "text/event-stream",
                             "origin": "https://uk.trip.com", "referer": PAGE}, {}),
    }
    for name, (headers, cookies) in variants.items():
        try:
            c = primp.Client(impersonate="chrome_145", cookie_store=True, timeout=40)
            if cookies:
                c.set_cookies("https://uk.trip.com", cookies)
            r = c.post(rec["url"], headers={k: v for k, v in headers.items() if k != "cookie"},
                       content=rec["body"].encode())
            print(f"{name:28s} status={r.status_code} len={len(r.text)} itineraries={count(r.text)}"
                  f" head={r.text[:120]!r}")
        except Exception as exc:
            print(f"{name:28s} error {type(exc).__name__}: {str(exc)[:200]}")
    # Fresh session: get cookies from the HTML page over HTTP only, then call the API.
    try:
        c = primp.Client(impersonate="chrome_145", cookie_store=True, timeout=40)
        c.get(PAGE)
        r = c.post(rec["url"], headers={k: v for k, v in h.items()
                                         if k not in ("token", "cookie")},
                   content=rec["body"].encode())
        print(f"{'fresh http session':28s} status={r.status_code} len={len(r.text)} "
              f"itineraries={count(r.text)}")
    except Exception as exc:
        print(f"fresh http session error {type(exc).__name__}: {str(exc)[:200]}")


if __name__ == "__main__":
    main()
