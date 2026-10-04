"""Rule-based parsing of trip requests, so most searches need no LLM call.

Understands Chinese and English city names, IATA codes, absolute and relative
dates ("12月20日", "20 Dec", "下周五", "next Friday", "圣诞节"), round trips,
cabin, passengers, budget and stop limits. Anything it cannot pin down is
reported in ``missing`` so the caller can ask, or hand the text to the LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from flightsaver.airports import KNOWN_AIRPORTS, METROS

# Longer names first so "上海浦东" wins over "上海".
PLACES: dict[str, str] = {
    # UK
    "london heathrow": "LHR",
    "heathrow": "LHR",
    "希思罗": "LHR",
    "gatwick": "LGW",
    "盖特威克": "LGW",
    "stansted": "STN",
    "斯坦斯特德": "STN",
    "london": "LON",
    "伦敦": "LON",
    "manchester": "MAN",
    "曼彻斯特": "MAN",
    "曼城": "MAN",
    "edinburgh": "EDI",
    "爱丁堡": "EDI",
    "birmingham": "BHX",
    "伯明翰": "BHX",
    "glasgow": "GLA",
    "格拉斯哥": "GLA",
    "bristol": "BRS",
    "布里斯托": "BRS",
    "布里斯托尔": "BRS",
    "newcastle": "NCL",
    "纽卡斯尔": "NCL",
    "纽卡": "NCL",
    # China
    "beijing daxing": "PKX",
    "大兴": "PKX",
    "beijing capital": "PEK",
    "首都机场": "PEK",
    "beijing": "BEIJING",
    "北京": "BEIJING",
    "pudong": "PVG",
    "浦东": "PVG",
    "hongqiao": "SHA",
    "虹桥": "SHA",
    "shanghai": "SHANGHAI",
    "上海": "SHANGHAI",
    "魔都": "SHANGHAI",
    "guangzhou": "CAN",
    "广州": "CAN",
    "shenzhen": "SZX",
    "深圳": "SZX",
    "chengdu": "CHENGDU",
    "成都": "CHENGDU",
    "天府": "TFU",
    "双流": "CTU",
    "hangzhou": "HGH",
    "杭州": "HGH",
    "xiamen": "XMN",
    "厦门": "XMN",
    "chongqing": "CKG",
    "重庆": "CKG",
    "wuhan": "WUH",
    "武汉": "WUH",
    "xi'an": "XIY",
    "xian": "XIY",
    "西安": "XIY",
    "nanjing": "NKG",
    "南京": "NKG",
    "qingdao": "TAO",
    "青岛": "TAO",
    "kunming": "KMG",
    "昆明": "KMG",
    "changsha": "CSX",
    "长沙": "CSX",
    "tianjin": "TSN",
    "天津": "TSN",
    "hong kong": "HKG",
    "hongkong": "HKG",
    "香港": "HKG",
}

# Fixed-date and lunar holidays used as anchors ("春节前", "around Christmas").
HOLIDAYS: dict[str, dict[int, date] | tuple[int, int]] = {
    "圣诞": (12, 25),
    "christmas": (12, 25),
    "xmas": (12, 25),
    "元旦": (1, 1),
    "new year": (1, 1),
    "国庆": (10, 1),
    "劳动节": (5, 1),
    "五一": (5, 1),
    "春节": {
        2026: date(2026, 2, 17),
        2027: date(2027, 2, 6),
        2028: date(2028, 1, 26),
        2029: date(2029, 2, 13),
        2030: date(2030, 2, 3),
    },
    "chinese new year": {
        2026: date(2026, 2, 17),
        2027: date(2027, 2, 6),
        2028: date(2028, 1, 26),
        2029: date(2029, 2, 13),
        2030: date(2030, 2, 3),
    },
    "中秋": {2026: date(2026, 9, 25), 2027: date(2027, 9, 15), 2028: date(2028, 10, 3)},
}

MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}
WEEKDAYS_CN = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
WEEKDAYS_EN = {
    d: i
    for i, d in enumerate(
        ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    )
}
CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

ROUND_TRIP_WORDS = (
    "往返",
    "来回",
    "round trip",
    "round-trip",
    "return trip",
    "回来",
    "返程",
    "回程",
    "return",
)


@dataclass
class ParsedRequest:
    origin: str | None = None
    destination: str | None = None
    depart: date | None = None
    # Flexible departure: any day from ``depart`` to ``depart_until`` (inclusive).
    depart_until: date | None = None
    return_date: date | None = None
    adults: int = 1
    cabin: str = "economy"
    currency: str = "GBP"
    max_stops: int | None = None
    budget: float | None = None
    sort: str = "best"  # best | cheapest | fastest
    wants_round_trip: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def missing(self) -> list[str]:
        out = []
        if not self.origin:
            out.append("origin")
        if not self.destination:
            out.append("destination")
        if not self.depart:
            out.append("depart")
        if self.wants_round_trip and not self.return_date:
            out.append("return_date")
        return out

    def tool_input(self) -> dict:
        """Arguments in the shape of the search_flights tool."""
        args = {
            "origin": self.origin,
            "destination": self.destination,
            "depart_date": self.depart.isoformat() if self.depart else None,
            "adults": self.adults,
            "cabin": self.cabin,
            "currency": self.currency,
        }
        if self.return_date:
            args["return_date"] = self.return_date.isoformat()
        if self.max_stops is not None:
            args["max_stops"] = self.max_stops
        if self.budget is not None:
            args["budget"] = self.budget
        args["sort"] = self.sort
        return args


def _future(month: int, day: int, today: date) -> date:
    """The next occurrence of month/day on or after today."""
    d = date(today.year, month, day)
    return d if d >= today else date(today.year + 1, month, day)


def _holiday(name: str, today: date) -> date | None:
    spec = HOLIDAYS[name]
    if isinstance(spec, tuple):
        return _future(*spec, today)
    for year in sorted(spec):
        if spec[year] >= today:
            return spec[year]
    return None


def _places(text: str) -> list[tuple[int, str]]:
    """(position, code) for each place mentioned, in order of appearance."""
    low = text.lower()
    taken = [False] * len(low)
    found: list[tuple[int, str]] = []
    for name in sorted(PLACES, key=len, reverse=True):
        # English names must be whole words ("bristol", not "bristolian").
        pattern = rf"(?<![a-z]){re.escape(name)}(?![a-z])" if name.isascii() else re.escape(name)
        for m in re.finditer(pattern, low):
            if not any(taken[m.start() : m.end()]):
                taken[m.start() : m.end()] = [True] * (m.end() - m.start())
                found.append((m.start(), PLACES[name]))
    for m in re.finditer(r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])", text):
        code = m.group(1)
        if (code in KNOWN_AIRPORTS or code in METROS) and not any(taken[m.start() : m.end()]):
            found.append((m.start(), code))
    return sorted(found)


def _dates(text: str, today: date) -> list[tuple[int, date, str]]:
    """(position, date, kind) for each date mention; kind is 'exact' or 'approx'."""
    low = text.lower()
    out: list[tuple[int, date, str]] = []

    def add(pos: int, d: date | None, kind: str = "exact") -> None:
        if d and not any(p == pos for p, _, _ in out):
            out.append((pos, d, kind))

    for m in re.finditer(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", low):
        add(m.start(), _safe(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in re.finditer(r"(?<![\d/.-])(\d{1,2})月(\d{1,2})[日号]?", low):
        add(m.start(), _safe_future(int(m.group(1)), int(m.group(2)), today))
    for m in re.finditer(r"(?<![\d/.-])(\d{1,2})[/.](\d{1,2})(?![\d/.])", low):
        a, b = int(m.group(1)), int(m.group(2))
        month, day = (b, a) if a > 12 else (a, b)  # 20/12 -> 20 Dec; 12/20 -> 20 Dec
        add(m.start(), _safe_future(month, day, today))
    mon = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    for m in re.finditer(rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+{mon}", low):
        add(m.start(), _safe_future(MONTHS[m.group(2)], int(m.group(1)), today))
    for m in re.finditer(rf"{mon}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?!\d)", low):
        add(m.start(), _safe_future(MONTHS[m.group(1)], int(m.group(2)), today))

    parts = {"early": 5, "beginning of": 5, "mid": 15, "middle of": 15, "late": 25, "end of": 25}
    for m in re.finditer(rf"(early|beginning of|mid|middle of|late|end of)[\s-]*{mon}", low):
        add(m.start(), _safe_future(MONTHS[m.group(2)], parts[m.group(1)], today), "approx")
    cn_parts = {"上旬": 5, "初": 5, "中旬": 15, "中": 15, "下旬": 25, "底": 25, "末": 25}
    for m in re.finditer(r"(?<!\d)(\d{1,2})月(上旬|中旬|下旬|初|中|底|末)", low):
        add(m.start(), _safe_future(int(m.group(1)), cn_parts[m.group(2)], today), "approx")

    for word, delta in (("今天", 0), ("today", 0), ("明天", 1), ("tomorrow", 1), ("后天", 2)):
        for m in re.finditer(word, low):
            add(m.start(), today + timedelta(days=delta))
    for m in re.finditer(r"(下下|下个?|这个?|本)?(?:周|星期|礼拜)([一二三四五六日天])", text):
        target = WEEKDAYS_CN[m.group(2)]
        prefix = m.group(1) or ""
        base = today - timedelta(days=today.weekday())  # this Monday
        if prefix.startswith("下下"):
            base += timedelta(weeks=2)
        elif prefix.startswith("下"):
            base += timedelta(weeks=1)
        d = base + timedelta(days=target)
        if not prefix and d < today:
            d += timedelta(weeks=1)
        add(m.start(), d)
    for m in re.finditer(
        r"(next|this)?\s*(monday|tuesday|wednesday|thursday|friday|"
        r"saturday|sunday)",
        low,
    ):
        target = WEEKDAYS_EN[m.group(2)]
        monday = today - timedelta(days=today.weekday())
        if m.group(1) == "next":
            d = monday + timedelta(weeks=1, days=target)  # in next calendar week
        else:
            d = monday + timedelta(days=target)
            if d <= today:
                d += timedelta(weeks=1)
        add(m.start(), d)
    for name in HOLIDAYS:
        for m in re.finditer(re.escape(name), low):
            d = _holiday(name, today)
            after = low[m.end() : m.end() + 10]
            if d and re.match(r"节?前后|\s*around", after):
                pass
            elif d and re.match(r"节?前|\s*before", after):
                d -= timedelta(days=3)
            elif d and re.match(r"节?后|\s*after", after):
                d += timedelta(days=3)
            add(m.start(), d, "approx")
    out.sort()
    return out


def _safe(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _safe_future(m: int, d: int, today: date) -> date | None:
    try:
        return _future(m, d, today)
    except ValueError:
        return None


ANY_CN, ANY_UK = "ANY_CN", "ANY_UK"
_ANY = re.compile(
    r"(?P<cn>(国内|中国|大陆|内地|china)\s*(的)?\s*(任何|任意|所有|各个|随便|哪个|any)?\s*(一个)?\s*"
    r"(城市|地方|机场|city|cities|airport|airports|anywhere)"
    r"|(任何|任意|所有|随便|哪个)\s*(中国|国内)?\s*(城市|地方|机场)"
    r"|anywhere in china|any (?:city|airport) in china)"
    r"|(?P<uk>(英国)\s*(的)?\s*(任何|任意|所有|各个|随便|哪个)?\s*(城市|地方|机场)"
    r"|anywhere in (?:the )?uk|any (?:city|airport) in (?:the )?uk)"
)
_CN_MONTHS = {
    "十二": 12,
    "十一": 11,
    "十": 10,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_RANGE_LINK = re.compile(r"(到|至|~|～|-|–|—|until|till|to|through|between|and|之间)")
_RETURN = re.compile(r"(回来|返回|返程|回程|往返|来回|return|back|round)")


def normalise(text: str) -> str:
    """Write Chinese month numerals as digits: 十月底 -> 10月底, 十一月 -> 11月."""
    return re.sub(
        r"(?<![\d一二三四五六七八九十])(十二|十一|十|[一二三四五六七八九])月",
        lambda m: f"{_CN_MONTHS[m.group(1)]}月",
        text,
    )


def parse(text: str, today: date | None = None) -> ParsedRequest:
    today = today or date.today()
    text = normalise(text)
    low = text.lower()
    req = ParsedRequest()

    places = _places(text)
    origins, dests, plain = [], [], []
    for pos, code in places:
        before = low[max(0, pos - 6) : pos]
        if re.search(r"(从|由|from)\s*$", before):
            origins.append(code)
        elif re.search(r"(去|到|飞往|飞|回|抵达|前往|to|into)\s*$", before):
            dests.append(code)
        else:
            plain.append(code)
    # Unmarked places fill origin first, then destination, in order of mention.
    for code in plain:
        if not origins:
            origins.append(code)
        elif not dests and code not in origins:
            dests.append(code)
    req.origin = origins[0] if origins else None
    req.destination = next((c for c in dests if c != req.origin), None)

    # "国内任何城市" / "anywhere in China": every gateway is searched.
    m = _ANY.search(low)
    if m:
        anywhere = ANY_CN if m.group("cn") else ANY_UK
        if re.search(r"(从|由|from)\s*$", low[max(0, m.start() - 4) : m.start()]):
            req.origin, req.destination = anywhere, req.destination or req.origin
        elif not req.destination:
            req.destination = anywhere

    dates = _dates(text, today)
    if dates:
        pos, d, kind = dates[0]
        req.depart = d
        if kind == "approx":
            req.notes.append(f"approximate date: searched {d.isoformat()}")
    if len(dates) > 1 and req.depart:
        (p1, _, _), (p2, d2, _) = dates[0], dates[1]
        between = low[p1 : p2 + 1]
        if d2 > req.depart and _RANGE_LINK.search(between) and not _RETURN.search(low):
            # "十月底到11月中": a window of departure dates, not a return date.
            req.depart_until = d2
            req.notes = [n for n in req.notes if not n.startswith("approximate")]
    if not req.depart_until:
        for _pos, d, _ in dates[1:]:
            if req.depart and d > req.depart:
                req.return_date = d
                break

    req.wants_round_trip = req.return_date is not None or any(w in low for w in ROUND_TRIP_WORDS)
    if any(w in low for w in ("单程", "one way", "one-way")):
        req.wants_round_trip = False
        req.return_date = None
    elif req.wants_round_trip and not req.return_date and req.depart:
        m = re.search(
            r"(\d+|[一两二三四五六七八九十])\s*(?:个)?\s*(天|晚|days|nights|周|星期|"
            r"weeks?)",
            low,
        )
        if m:
            k = m.group(1)
            k = int(k) if k.isdigit() else (10 if k == "十" else CN_NUM[k])
            n = k * (7 if m.group(2) in ("周", "星期", "week", "weeks") else 1)
            req.return_date = req.depart + timedelta(days=n)

    if re.search(r"商务舱|商务|business", low):
        req.cabin = "business"
    elif re.search(r"头等舱|头等|first class", low):
        req.cabin = "first"
    elif re.search(r"超级经济|超经|premium", low):
        req.cabin = "premium-economy"

    m = re.search(r"(\d)\s*(?:个)?(?:人|位|adults?|people|passengers?)", low) or re.search(
        r"([一两二三四五六七八九])\s*(?:个)?(?:人|位)", text
    )
    if m:
        n = m.group(1)
        req.adults = int(n) if n.isdigit() else CN_NUM[n]
    elif re.search(r"我们俩|两口子|情侣|couple", low):
        req.adults = 2

    if re.search(r"直飞|不转机|direct|non-?stop", low):
        req.max_stops = 0
    else:
        m = re.search(
            r"(?:最多|至多|max(?:imum)?|at most|up to)\s*(?:转)?\s*(\d|一|两)\s*"
            r"(?:次|个)?\s*(?:转机|中转|stops?|转)?",
            low,
        )
        if m:
            n = m.group(1)
            req.max_stops = int(n) if n.isdigit() else CN_NUM[n]

    if re.search(r"人民币|rmb|cny|¥|元", low):
        req.currency = "CNY"
    m = (
        re.search(r"(?:预算|budget|under|below|以内|不超过|少于|<)\s*[£¥$]?\s*(\d{2,6})", low)
        or re.search(r"[£¥]\s*(\d{2,6})", low)
        or re.search(
            r"(\d{2,6})\s*(?:镑|英镑|pounds?|gbp|元|块|rmb|cny)\s*(?:以内|以下|之内)?", low
        )
    )
    if m:
        req.budget = float(m.group(1))
    if re.search(r"最快|最短|时间最少|fastest|quickest|shortest", low):
        req.sort = "fastest"
    elif re.search(r"最便宜|最低价|最省钱|cheapest|lowest price", low):
        req.sort = "cheapest"
    return req
