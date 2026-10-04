"""Quick mode: parse the request locally, search, and summarise from a template.

No LLM call, so it costs nothing in API credits. The chat page uses this by
default and only falls back to Claude when the user asks for it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

from flightsaver import explore, nlp
from flightsaver.airports import region
from flightsaver.web.tools import package, parse_args

LABELS = {
    "zh": {
        "origin": "出发城市",
        "destination": "目的地",
        "depart": "出发日期",
        "return_date": "回程日期",
    },
    "en": {
        "origin": "departure city",
        "destination": "destination",
        "depart": "departure date",
        "return_date": "return date",
    },
}
CABINS = {
    "economy": "经济舱",
    "premium-economy": "超级经济舱",
    "business": "商务舱",
    "first": "头等舱",
}


def is_chinese(text: str) -> bool:
    return bool(re.search(r"[一-鿿]", text))


PLACE_NAMES = {
    nlp.ANY_CN: ("中国任意城市", "anywhere in China"),
    nlp.ANY_UK: ("英国任意城市", "anywhere in the UK"),
}


def place(code: str | None, zh: bool) -> str:
    names = PLACE_NAMES.get(code or "")
    return (names[0] if zh else names[1]) if names else str(code)


def describe(parsed: nlp.ParsedRequest, zh: bool) -> str:
    """One line restating how the request was understood."""
    when = f"{parsed.depart}"
    if parsed.depart_until:
        when += f" 至 {parsed.depart_until} 之间出发" if zh else f" to {parsed.depart_until}"
    trip = f"{place(parsed.origin, zh)} → {place(parsed.destination, zh)} · {when}"
    if parsed.return_date:
        trip += f" / {parsed.return_date}"
    cabin = CABINS[parsed.cabin] if zh else parsed.cabin
    bits = [trip, cabin, f"{parsed.adults} 人" if zh else f"{parsed.adults} adult(s)"]
    if parsed.max_stops == 0:
        bits.append("直飞" if zh else "direct only")
    elif parsed.max_stops is not None:
        bits.append(f"最多转 {parsed.max_stops} 次" if zh else f"max {parsed.max_stops} stop(s)")
    if parsed.budget:
        bits.append(
            f"预算 {parsed.budget:.0f} {parsed.currency}"
            if zh
            else f"budget {parsed.budget:.0f} {parsed.currency}"
        )
    return " · ".join(bits)


def summarise(data: dict, zh: bool) -> str:
    offers = data["offers"]
    cur = data["query"]["currency"]
    if not offers:
        return (
            "暂时没有抓到实时报价，可以直接点下方的平台链接查看。"
            if zh
            else "No live fares right now; use the platform links below."
        )
    best = max(offers, key=lambda o: o.get("score", 0))
    cheapest = min(offers, key=lambda o: o["price"])
    fastest = min(offers, key=lambda o: o.get("total_minutes") or o["flying_minutes"])
    rt = ""
    if data["query"]["round_trip_prices"]:
        rt = "（往返总价）" if zh else " (round-trip total)"

    def line(o: dict) -> str:
        total = _hm(o.get("total_minutes") or o["flying_minutes"])
        names = ("、" if zh else ", ").join(o["airlines"])
        if zh:
            stops = "直飞" if o["stops"] == 0 else f"转 {o['stops']} 次"
            wait = sum(x["minutes"] for x in o.get("layovers", []))
            extra = f"，其中中转 {_hm(wait)}" if wait else ""
            return f"{o['price']:.0f} {cur}，{names}，{stops}，总时长 {total}{extra}"
        stops = "direct" if o["stops"] == 0 else f"{o['stops']} stop(s)"
        return f"{o['price']:.0f} {cur}, {names}, {stops}, {total} door to door"

    if zh:
        text = f"找到 {data['total_offers_found']} 个报价{rt}。\n- 综合推荐：{line(best)}"
        if cheapest is not best:
            text += f"\n- 最便宜：{line(cheapest)}"
        if fastest is not best and fastest is not cheapest:
            text += f"\n- 最快：{line(fastest)}"
        return text
    text = f"Found {data['total_offers_found']} fares{rt}.\n- Best overall: {line(best)}"
    if cheapest is not best:
        text += f"\n- Cheapest: {line(cheapest)}"
    if fastest is not best and fastest is not cheapest:
        text += f"\n- Fastest: {line(fastest)}"
    return text


def _hm(minutes: int) -> str:
    return f"{minutes // 60}h{minutes % 60:02d}"


def merge_context(parsed: nlp.ParsedRequest, context: dict | None) -> None:
    """Fill route/dates left out of a follow-up ("12月20日") from the previous request."""
    if not context:
        return
    # Only a fragment ("12月20日", "改成曼彻斯特") continues the previous request; a message
    # that names a route or a date of its own is a new request and must not inherit.
    given = sum(x is not None for x in (parsed.origin, parsed.destination, parsed.depart))
    if given > 1:
        return
    if not parsed.origin and not parsed.destination:
        parsed.origin, parsed.destination = context.get("origin"), context.get("destination")
    elif not parsed.origin and context.get("origin") != parsed.destination:
        parsed.origin = context.get("origin")
    elif not parsed.destination and context.get("destination") != parsed.origin:
        parsed.destination = context.get("destination")
    for key, attr in (("depart_date", "depart"), ("return_date", "return_date")):
        if getattr(parsed, attr) is None and context.get(key):
            try:
                setattr(parsed, attr, date.fromisoformat(context[key]))
            except ValueError:
                pass
    if context.get("return_date") and parsed.return_date:
        parsed.wants_round_trip = True


def quick_search(
    text: str,
    search_fn: Callable[..., dict],
    engine: str = "rules",
    today: date | None = None,
    context: dict | None = None,
    explore_fn: Callable[..., dict] | None = None,
) -> dict:
    """Returns {"understood", "missing", "message", "parsed", "data"?}; never calls an LLM.

    Date windows and "any city" requests go to ``explore_fn`` (flexible search).
    """
    explore_fn = explore_fn or run_explore
    zh = is_chinese(text)
    if context and not re.search(r"[A-Za-z\u4e00-\u9fff]", text):
        zh = context.get("lang") == "zh"  # "2026-12-20" alone: keep the conversation's language
    parsed = nlp.parse(text, today)
    merge_context(parsed, context)
    missing = parsed.missing
    if missing:
        names = [LABELS["zh" if zh else "en"][m] for m in missing]
        message = (
            f"还需要：{'、'.join(names)}。可以补充后再发一次，或者点「让 AI 理解」。"
            if zh
            else f"Missing: {', '.join(names)}. Add it and send again, or ask the AI."
        )
        return {
            "understood": None,
            "missing": missing,
            "message": message,
            "parsed": _plain(parsed, zh),
        }
    try:
        if explore.is_flexible(parsed):
            data = explore_fn(parsed, engine)
            message = summarise_explore(data, zh)
        else:
            query, budget = parse_args(parsed.tool_input())
            data = search_fn(query, budget, engine, parsed.sort)
            message = summarise(data, zh)
    except ValueError as exc:
        return {
            "understood": describe(parsed, zh),
            "missing": [],
            "message": str(exc),
            "parsed": _plain(parsed, zh),
        }
    if parsed.notes:
        message += (
            " 日期是按大概时间推算的，可以写具体日期再搜。"
            if zh
            else " The date was approximated; give an exact date to refine."
        )
    return {
        "understood": describe(parsed, zh),
        "missing": [],
        "message": message,
        "parsed": _plain(parsed, zh),
        "data": data,
    }


def _plain(parsed: nlp.ParsedRequest, zh: bool) -> dict:
    """Route and dates only, echoed back by the page as context for the next message."""
    return {
        "lang": "zh" if zh else "en",
        "origin": parsed.origin,
        "destination": parsed.destination,
        "depart_date": parsed.depart.isoformat() if parsed.depart else None,
        "return_date": parsed.return_date.isoformat() if parsed.return_date else None,
    }


def run_explore(parsed: nlp.ParsedRequest, engine: str = "rules") -> dict:
    """Flexible search, packaged like a normal result plus an ``explore`` summary."""
    for code in (parsed.origin, parsed.destination):
        if code not in explore.GATEWAYS:
            region(code)  # raises ValueError for unknown codes
    ends = {
        region(c) if c not in explore.GATEWAYS else ("CN" if c == nlp.ANY_CN else "UK")
        for c in (parsed.origin, parsed.destination)
    }
    if ends != {"UK", "CN"}:
        raise ValueError("only UK <-> China international routes are supported for now")
    result, focus, summary = explore.explore(parsed)
    data = package(focus, result, parsed.budget, engine, sort=parsed.sort)
    data["explore"] = summary
    return data


def summarise_explore(data: dict, zh: bool) -> str:
    ex = data["explore"]
    routes = ex["routes"]
    cur = data["query"]["currency"]
    a, b = ex["window"]
    window = a if a == b else (f"{a} 至 {b}" if zh else f"{a} to {b}")
    lines = []
    if zh:
        lines.append(
            f"灵活搜索：{place(ex['origin'], True)} → {place(ex['destination'], True)}，"
            f"{window}，共查了 {len(routes)} 条航线。"
        )
    else:
        lines.append(
            f"Flexible search: {place(ex['origin'], False)} → "
            f"{place(ex['destination'], False)}, {window}, {len(routes)} routes."
        )
    known = [r for r in routes if r["has_direct"] is not None]
    if ex["direct_only"] and known and not any(r["has_direct"] for r in known):
        names = "、".join(r["destination"] for r in known)
        lines.append(
            f"- 没有直飞：{place(ex['origin'], True)} 到 {names} 都没有直飞航班"
            "（Trip.com 航线数据）。下面列的是转机最少的选择。"
            if zh
            else "- No nonstop service on any of these routes "
            f"({', '.join(r['destination'] for r in known)}); showing the fewest-stop options."
        )
    elif data.get("stops_relaxed"):
        lines.append(
            "- 没有找到符合直飞要求的航班，下面列的是转机最少的选择。"
            if zh
            else "- No flights matched the stop limit; showing the fewest-stop options."
        )
    priced = sorted((r for r in routes if r["cheapest_price"]), key=lambda r: r["cheapest_price"])
    for r in priced[:5]:
        direct = (
            ("有直飞" if r["has_direct"] else "无直飞")
            if zh
            else ("nonstop exists" if r["has_direct"] else "no nonstop")
        )
        if zh:
            lines.append(
                f"- {r['origin']} → {r['destination']}：区间最低 {r['cheapest_price']} {cur}"
                f"（{r['cheapest_date']} 出发，{direct}）"
            )
        else:
            lines.append(
                f"- {r['origin']} → {r['destination']}: from {r['cheapest_price']} {cur} "
                f"on {r['cheapest_date']} ({direct})"
            )
    failed = [r for r in routes if r["error"]]
    if failed:
        lines.append(
            ("- 未能查询：" if zh else "- Could not check: ")
            + ", ".join(f"{r['destination']}" for r in failed)
        )
    f = ex["focus"]
    if data["offers"]:
        lines.append(
            (f"下面是 {f['origin']} → {f['destination']} {f['date']} 等日期的具体航班。")
            if zh
            else f"Flights below include {f['origin']} → {f['destination']} on {f['date']}."
        )
    return "\n".join(lines)
