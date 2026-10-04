"""Booking links for travel platforms and airline websites.

Every search returns a link per platform so the user can compare and buy there.
URL formats are the platforms' public search URLs; those we could not confirm
carry ``prefilled=False`` and open the platform's flight search page instead.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import urlencode

from flightsaver.airports import METROS, city_code, expand
from flightsaver.models import BookingLink, SearchQuery

# Skyscanner uses its own codes for multi-airport cities.
_SKYSCANNER_CITY = {
    "LON": "lond",
    "LONDON": "lond",
    "BJS": "bjsa",
    "BEIJING": "bjsa",
    "SHANGHAI": "csha",
}
_SKYSCANNER_CABIN = {
    "economy": "economy",
    "premium-economy": "premiumeconomy",
    "business": "business",
    "first": "first",
}
_KAYAK_CABIN = {
    "economy": "",
    "premium-economy": "premium",
    "business": "business",
    "first": "first",
}
_TRIP_CABIN = {"economy": "y", "premium-economy": "s", "business": "c", "first": "f"}
_CTRIP_CABIN = {"economy": "y_s", "premium-economy": "y_s", "business": "c_f", "first": "c_f"}


def _code(code: str) -> str:
    """Airport or city code usable by platforms that understand IATA city codes."""
    code = code.upper()
    return city_code(code) if code in METROS else code


def google_flights(q: SearchQuery) -> BookingLink:
    text = f"Flights from {_code(q.origin)} to {_code(q.destination)} on {q.depart.isoformat()}"
    if q.return_date:
        text += f" through {q.return_date.isoformat()}"
    if q.cabin != "economy":
        text += f" {q.cabin.replace('-', ' ')}"
    params = {"q": text, "hl": "en-GB", "curr": q.currency}
    return BookingLink(
        "google_flights",
        "Google Flights",
        "https://www.google.com/travel/flights?" + urlencode(params),
        "metasearch",
        True,
    )


def skyscanner(q: SearchQuery) -> BookingLink:
    def code(c: str) -> str:
        c = c.upper()
        return _SKYSCANNER_CITY.get(c) or expand(c)[0].lower()

    def d(x: date) -> str:
        return x.strftime("%y%m%d")

    path = f"{code(q.origin)}/{code(q.destination)}/{d(q.depart)}/"
    if q.return_date:
        path += f"{d(q.return_date)}/"
    params = {
        "adultsv2": q.adults,
        "cabinclass": _SKYSCANNER_CABIN[q.cabin],
        "rtn": int(q.round_trip),
        "currency": q.currency,
    }
    return BookingLink(
        "skyscanner",
        "Skyscanner",
        f"https://www.skyscanner.net/transport/flights/{path}?{urlencode(params)}",
        "metasearch",
        True,
    )


def kayak(q: SearchQuery) -> BookingLink:
    path = f"{_code(q.origin)}-{_code(q.destination)}/{q.depart.isoformat()}"
    if q.return_date:
        path += f"/{q.return_date.isoformat()}"
    if _KAYAK_CABIN[q.cabin]:
        path += f"/{_KAYAK_CABIN[q.cabin]}"
    if q.adults > 1:
        path += f"/{q.adults}adults"
    return BookingLink(
        "kayak", "KAYAK", f"https://www.kayak.co.uk/flights/{path}?sort=price_a", "metasearch", True
    )


def expedia(q: SearchQuery) -> BookingLink:
    o, dst = _code(q.origin), _code(q.destination)

    def leg(frm: str, to: str, day: date) -> str:
        return f"from:{frm},to:{to},departure:{day.strftime('%d/%m/%Y')}TANYT"

    params = {"trip": "roundtrip" if q.round_trip else "oneway", "leg1": leg(o, dst, q.depart)}
    if q.return_date:
        params["leg2"] = leg(dst, o, q.return_date)
    params["passengers"] = f"adults:{q.adults}"
    params["options"] = f"cabinclass:{q.cabin.replace('-', '')}"
    params["mode"] = "search"
    return BookingLink(
        "expedia",
        "Expedia UK",
        "https://www.expedia.co.uk/Flights-Search?" + urlencode(params, safe=":,/"),
        "ota-intl",
        True,
    )


def trip_com(q: SearchQuery) -> BookingLink:
    params = {
        "dcity": city_code(q.origin).lower(),
        "acity": city_code(q.destination).lower(),
        "ddate": q.depart.isoformat(),
    }
    if q.return_date:
        params["rdate"] = q.return_date.isoformat()
    params.update(
        {
            "triptype": "rt" if q.round_trip else "ow",
            "class": _TRIP_CABIN[q.cabin],
            "quantity": q.adults,
            "locale": "en-GB",
            "curr": q.currency,
        }
    )
    return BookingLink(
        "trip_com",
        "Trip.com",
        "https://uk.trip.com/flights/showfarefirst?" + urlencode(params),
        "ota-intl",
        True,
    )


def ctrip(q: SearchQuery) -> BookingLink:
    o, d = city_code(q.origin).lower(), city_code(q.destination).lower()
    kind = "round" if q.round_trip else "oneway"
    dates = q.depart.isoformat() + (f"_{q.return_date.isoformat()}" if q.return_date else "")
    params = {
        "depdate": dates,
        "cabin": _CTRIP_CABIN[q.cabin],
        "adult": q.adults,
        "child": 0,
        "infant": 0,
    }
    return BookingLink(
        "ctrip",
        "携程 Ctrip",
        f"https://flights.ctrip.com/online/list/{kind}-{o}-{d}?{urlencode(params)}",
        "ota-cn",
        True,
    )


# Platforms whose deep-link formats are not yet confirmed: link to flight search.
_UNPREFILLED_PLATFORMS: list[BookingLink] = [
    BookingLink("qunar", "去哪儿 Qunar", "https://flight.qunar.com/", "ota-cn", False),
    BookingLink("fliggy", "飞猪 Fliggy", "https://www.fliggy.com/jipiao/", "ota-cn", False),
    BookingLink("tongcheng", "同程旅行 LY.com", "https://www.ly.com/flights/", "ota-cn", False),
]

# Airlines flying UK <-> China, direct or one-stop via their hub. Keyed by the
# airline name as Google Flights reports it so results can link to the carrier.
AIRLINES: dict[str, tuple[str, str]] = {
    "British Airways": ("BA", "https://www.britishairways.com/"),
    "Virgin Atlantic": ("VS", "https://www.virginatlantic.com/"),
    "Air China": ("CA", "https://www.airchina.com.cn/"),
    "China Eastern": ("MU", "https://www.ceair.com/"),
    "China Southern": ("CZ", "https://www.csair.com/"),
    "Hainan Airlines": ("HU", "https://www.hainanairlines.com/"),
    "Sichuan Airlines": ("3U", "https://www.sichuanair.com/"),
    "Tianjin Airlines": ("GS", "https://www.tianjin-air.com/"),
    "Cathay Pacific": ("CX", "https://www.cathaypacific.com/"),
    "Finnair": ("AY", "https://www.finnair.com/"),
    "KLM": ("KL", "https://www.klm.co.uk/"),
    "Lufthansa": ("LH", "https://www.lufthansa.com/"),
    "Turkish Airlines": ("TK", "https://www.turkishairlines.com/"),
    "Emirates": ("EK", "https://www.emirates.com/uk/"),
    "Qatar Airways": ("QR", "https://www.qatarairways.com/"),
    "Etihad": ("EY", "https://www.etihad.com/"),
}


def airline_link(name: str) -> BookingLink | None:
    entry = AIRLINES.get(name)
    if entry is None:
        return None
    code, url = entry
    return BookingLink(f"airline_{code.lower()}", name, url, "airline", False)


def platform_links(q: SearchQuery) -> list[BookingLink]:
    """Links for every supported platform, Chinese and international."""
    return [
        google_flights(q),
        skyscanner(q),
        kayak(q),
        expedia(q),
        trip_com(q),
        ctrip(q),
        *_UNPREFILLED_PLATFORMS,
    ]


def airline_links(names: list[str] | None = None) -> list[BookingLink]:
    """Airline links, either for the given airline names or for all known carriers."""
    names = list(AIRLINES) if names is None else names
    seen, out = set(), []
    for name in names:
        link = airline_link(name)
        if link and link.platform not in seen:
            seen.add(link.platform)
            out.append(link)
    return out
