import pytest

from flightsaver.airports import check_corridor, city_code, expand


def test_expand_metro_and_airport():
    assert expand("lon") == ("LHR", "LGW", "STN")
    assert expand("SHANGHAI") == ("PVG", "SHA")
    assert expand("PVG") == ("PVG",)
    with pytest.raises(ValueError):
        expand("JFK")


def test_city_code():
    assert city_code("LHR") == "LON"
    assert city_code("PKX") == "BJS"
    assert city_code("SHANGHAI") == "SHA"


def test_corridor():
    check_corridor("MAN", "PEK")
    check_corridor("CAN", "LON")
    with pytest.raises(ValueError):
        check_corridor("LHR", "MAN")
    with pytest.raises(ValueError):
        check_corridor("PEK", "PVG")
