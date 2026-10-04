from datetime import date

import pytest

from flightsaver.nlp import parse

TODAY = date(2026, 10, 4)  # a Sunday


@pytest.mark.parametrize(
    "text, origin, dest, depart, ret",
    [
        ("12月20日从伦敦飞上海，1月5日回来，经济舱", "LON", "SHANGHAI", "2026-12-20", "2027-01-05"),
        ("曼彻斯特到北京，圣诞节前后最便宜的单程", "MAN", "BEIJING", "2026-12-25", None),
        ("春节前从爱丁堡回成都", "EDI", "CHENGDU", "2027-02-03", None),
        ("下周五伦敦飞上海", "LON", "SHANGHAI", "2026-10-09", None),
        ("LHR to PVG 20 Dec, back Jan 5", "LHR", "PVG", "2026-12-20", "2027-01-05"),
        ("伦敦去深圳 2026-11-18 往返 两周", "LON", "SZX", "2026-11-18", "2026-12-02"),
        ("next friday heathrow to hong kong", "LHR", "HKG", "2026-10-09", None),
        ("上海飞伦敦 12/20", "SHANGHAI", "LON", "2026-12-20", None),
        ("去上海，从伦敦出发，12月20日", "LON", "SHANGHAI", "2026-12-20", None),
        ("London to Guangzhou mid-January", "LON", "CAN", "2027-01-15", None),
        (
            "Edinburgh to Shanghai on 5 March returning 19 March",
            "EDI",
            "SHANGHAI",
            "2027-03-05",
            "2027-03-19",
        ),
        ("1月中旬伦敦回北京", "LON", "BEIJING", "2027-01-15", None),
    ],
)
def test_routes_and_dates(text, origin, dest, depart, ret):
    r = parse(text, TODAY)
    assert (r.origin, r.destination) == (origin, dest)
    assert r.depart.isoformat() == depart
    assert (r.return_date.isoformat() if r.return_date else None) == ret
    assert r.missing == []


def test_options():
    r = parse("从香港飞曼城 3月1号 两个人 商务舱 直飞 预算3000镑", TODAY)
    assert (r.origin, r.destination, r.adults, r.cabin, r.max_stops, r.budget) == (
        "HKG",
        "MAN",
        2,
        "business",
        0,
        3000.0,
    )
    r = parse("LON to PEK 1 Dec 2 adults premium economy max 1 stop under £700", TODAY)
    assert (r.adults, r.cabin, r.max_stops, r.budget) == (2, "premium-economy", 1, 700.0)
    assert parse("伦敦飞北京 12月1日 人民币", TODAY).currency == "CNY"


def test_missing_parts_are_reported():
    assert parse("我想去上海", TODAY).missing == ["origin", "depart"]
    assert parse("伦敦飞上海 往返 12月1日", TODAY).missing == ["return_date"]
    assert parse("hello", TODAY).missing == ["origin", "destination", "depart"]


def test_past_dates_roll_to_next_year():
    assert parse("伦敦飞上海 3月1日", TODAY).depart == date(2027, 3, 1)
    assert parse("伦敦飞上海 10月10日", TODAY).depart == date(2026, 10, 10)


def test_english_words_need_boundaries():
    # "Bristolian" is not Bristol; IATA codes only count when known.
    r = parse("ABC Bristolian to Shanghai 1 Dec", TODAY)
    assert r.origin is None and r.destination == "SHANGHAI"


def test_sort_preference():
    assert parse("伦敦飞上海 12月1日 最便宜的", TODAY).sort == "cheapest"
    assert parse("fastest London to Beijing 1 Dec", TODAY).sort == "fastest"
    assert parse("伦敦飞上海 12月1日", TODAY).sort == "best"
