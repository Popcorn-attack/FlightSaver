"""IATA airline codes to English names, so offers from different sources match."""

from __future__ import annotations

NAMES: dict[str, str] = {
    # UK <-> China direct
    "BA": "British Airways",
    "VS": "Virgin Atlantic",
    "CA": "Air China",
    "MU": "China Eastern",
    "CZ": "China Southern",
    "HU": "Hainan Airlines",
    "3U": "Sichuan Airlines",
    "GS": "Tianjin Airlines",
    "FM": "Shanghai Airlines",
    "HO": "Juneyao Air",
    "MF": "Xiamen Airlines",
    "ZH": "Shenzhen Airlines",
    "CX": "Cathay Pacific",
    # common one-stop carriers
    "AY": "Finnair",
    "KL": "KLM",
    "AF": "Air France",
    "LH": "Lufthansa",
    "LX": "Swiss",
    "OS": "Austrian",
    "SK": "SAS",
    "TK": "Turkish Airlines",
    "EK": "Emirates",
    "QR": "Qatar Airways",
    "EY": "Etihad",
    "SQ": "Singapore Airlines",
    "KE": "Korean Air",
    "OZ": "Asiana",
    "JL": "Japan Airlines",
    "NH": "ANA",
    "LO": "LOT Polish",
    "AZ": "ITA Airways",
    "IB": "Iberia",
    "EI": "Aer Lingus",
    "SU": "Aeroflot",
    "U2": "easyJet",
    "FR": "Ryanair",
    "W6": "Wizz Air",
    "VY": "Vueling",
}


def name(code: str, fallback: str = "") -> str:
    return NAMES.get(code.upper(), fallback or code.upper())
