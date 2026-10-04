from datetime import date
from urllib.parse import parse_qs, urlparse

from flightsaver.models import SearchQuery
from flightsaver.providers.links import airline_links, platform_links


def _links(q):
    return {link.platform: link for link in platform_links(q)}


def test_round_trip_links(query):
    links = _links(query)
    assert links["skyscanner"].url.startswith(
        "https://www.skyscanner.net/transport/flights/lhr/pvg/261220/270105/?"
    )
    assert links["kayak"].url == (
        "https://www.kayak.co.uk/flights/LHR-PVG/2026-12-20/2027-01-05?sort=price_a"
    )
    trip = parse_qs(urlparse(links["trip_com"].url).query)
    assert trip["dcity"] == ["lon"] and trip["acity"] == ["sha"]
    assert trip["triptype"] == ["rt"] and trip["rdate"] == ["2027-01-05"]
    assert links["ctrip"].url.startswith(
        "https://flights.ctrip.com/online/list/round-lon-sha?depdate=2026-12-20_2027-01-05"
    )
    assert "leg2=from:PVG,to:LHR,departure:05/01/2027TANYT" in links["expedia"].url
    assert "through+2027-01-05" in links["google_flights"].url


def test_one_way_metro_business():
    q = SearchQuery("LON", "BEIJING", date(2026, 12, 20), adults=2, cabin="business")
    links = _links(q)
    assert "/lond/bjsa/261220/?" in links["skyscanner"].url
    assert links["kayak"].url.endswith("LON-BJS/2026-12-20/business/2adults?sort=price_a")
    assert "oneway-lon-bjs" in links["ctrip"].url
    assert "rtn=0" in links["skyscanner"].url


def test_platform_coverage(query):
    kinds = {link.kind for link in platform_links(query)}
    assert kinds == {"metasearch", "ota-intl", "ota-cn"}


def test_airline_links_filter():
    links = airline_links(["Air China", "Unknown Air", "Air China"])
    assert [link.name for link in links] == ["Air China"]
    assert len(airline_links()) >= 10
