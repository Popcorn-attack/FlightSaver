"""UK and China airports, with metro codes that expand to individual airports.

The first release targets international UK <-> China routes, so the tables only
cover airports with long-haul service on that corridor (direct or via a hub).
"""

from __future__ import annotations

UK_AIRPORTS: dict[str, str] = {
    "LHR": "London Heathrow",
    "LGW": "London Gatwick",
    "STN": "London Stansted",
    "MAN": "Manchester",
    "EDI": "Edinburgh",
    "BHX": "Birmingham",
    "GLA": "Glasgow",
    "BRS": "Bristol",
    "NCL": "Newcastle",
}

CHINA_AIRPORTS: dict[str, str] = {
    "PEK": "Beijing Capital",
    "PKX": "Beijing Daxing",
    "PVG": "Shanghai Pudong",
    "SHA": "Shanghai Hongqiao",
    "CAN": "Guangzhou Baiyun",
    "SZX": "Shenzhen Bao'an",
    "CTU": "Chengdu Shuangliu",
    "TFU": "Chengdu Tianfu",
    "HGH": "Hangzhou Xiaoshan",
    "XMN": "Xiamen Gaoqi",
    "CKG": "Chongqing Jiangbei",
    "WUH": "Wuhan Tianhe",
    "XIY": "Xi'an Xianyang",
    "NKG": "Nanjing Lukou",
    "TAO": "Qingdao Jiaodong",
    "KMG": "Kunming Changshui",
    "CSX": "Changsha Huanghua",
    "TSN": "Tianjin Binhai",
    "HKG": "Hong Kong",
}

# Metro codes and city names that expand to several airports. SHA is both the
# Shanghai city code and Hongqiao airport, so it stays an airport here and the
# whole city is "SHANGHAI".
METROS: dict[str, tuple[str, ...]] = {
    "LON": ("LHR", "LGW", "STN"),
    "LONDON": ("LHR", "LGW", "STN"),
    "BJS": ("PEK", "PKX"),
    "BEIJING": ("PEK", "PKX"),
    "SHANGHAI": ("PVG", "SHA"),
    "CHENGDU": ("CTU", "TFU"),
}

# Names used in natural-language search links (Google Flights "q=").
METRO_NAMES: dict[str, str] = {
    "LON": "London",
    "LONDON": "London",
    "BJS": "Beijing",
    "BEIJING": "Beijing",
    "SHANGHAI": "Shanghai",
    "CHENGDU": "Chengdu",
}

# City codes used by OTAs that search by city rather than airport.
AIRPORT_TO_CITY: dict[str, str] = {
    "LHR": "LON",
    "LGW": "LON",
    "STN": "LON",
    "MAN": "MAN",
    "EDI": "EDI",
    "BHX": "BHX",
    "GLA": "GLA",
    "BRS": "BRS",
    "NCL": "NCL",
    "PEK": "BJS",
    "PKX": "BJS",
    "PVG": "SHA",
    "SHA": "SHA",
    "CTU": "CTU",
    "TFU": "CTU",
    "CAN": "CAN",
    "SZX": "SZX",
    "HGH": "HGH",
    "XMN": "XMN",
    "CKG": "CKG",
    "WUH": "WUH",
    "XIY": "SIA",
    "NKG": "NKG",
    "TAO": "TAO",
    "KMG": "KMG",
    "CSX": "CSX",
    "TSN": "TSN",
    "HKG": "HKG",
}

KNOWN_AIRPORTS = {**UK_AIRPORTS, **CHINA_AIRPORTS}


def expand(code: str) -> tuple[str, ...]:
    """Expand a metro code into airports; airport codes pass through."""
    code = code.strip().upper()
    if code in METROS:
        return METROS[code]
    if code in KNOWN_AIRPORTS:
        return (code,)
    raise ValueError(f"unknown airport or metro code: {code!r}")


def city_code(code: str) -> str:
    """City code for OTA links (e.g. LHR -> LON, PVG -> SHA)."""
    code = code.strip().upper()
    if code in METROS:
        return city_code(METROS[code][0])
    return AIRPORT_TO_CITY.get(code, code)


def region(code: str) -> str:
    """'UK' or 'CN' for a known airport or metro code."""
    first = expand(code)[0]
    return "UK" if first in UK_AIRPORTS else "CN"


def check_corridor(origin: str, destination: str) -> None:
    """Reject routes outside the UK <-> China scope of this release."""
    if {region(origin), region(destination)} != {"UK", "CN"}:
        raise ValueError(
            "only UK <-> China international routes are supported for now "
            f"(got {origin} -> {destination})"
        )
