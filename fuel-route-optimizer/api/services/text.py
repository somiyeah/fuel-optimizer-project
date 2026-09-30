"""Text normalisation for place names and highway designations.

Two jobs live here:

1. `normalize_city` makes "St. Louis", "SAINT LOUIS" and "st louis" compare
   equal, so station cities and user input match the offline gazetteer.
2. `parse_station_highways` / `parse_osrm_highways` pull Interstate and US
   route numbers out of free text, so a station listed at
   "I-44, EXIT 283 & US-69" can be checked against the highways OSRM says the
   route actually drives on ("I 44;US 69").

Only Interstates (I-xx) and US routes (US-xx) are used for matching. Their
numbers are unique nationwide, while state routes (SR-29, FM-1960) repeat from
state to state and are formatted inconsistently in both data sources.
"""

import re

_PUNCTUATION_RE = re.compile(r"[.'`’]")
_SEPARATOR_RE = re.compile(r"[-/,_]")
_SPACE_RE = re.compile(r"\s+")

# Leading abbreviations that appear both ways in US place names.
_LEADING_ABBREVIATIONS = {
    "ST": "SAINT",
    "STE": "SAINTE",
    "FT": "FORT",
    "MT": "MOUNT",
    "PT": "POINT",
}
_DIRECTION_ABBREVIATIONS = {"N": "NORTH", "S": "SOUTH", "E": "EAST", "W": "WEST"}


def normalize_city(value: str) -> str:
    """Canonical form of a city name used as a dictionary key.

    >>> normalize_city("St. Louis")
    'SAINT LOUIS'
    >>> normalize_city("Ft Worth")
    'FORT WORTH'
    """
    text = _PUNCTUATION_RE.sub("", value.upper())
    text = _SPACE_RE.sub(" ", _SEPARATOR_RE.sub(" ", text)).strip()
    if not text:
        return ""
    tokens = text.split(" ")
    first = tokens[0]
    if first in _LEADING_ABBREVIATIONS:
        tokens[0] = _LEADING_ABBREVIATIONS[first]
    elif first in _DIRECTION_ABBREVIATIONS and len(tokens) > 1:
        tokens[0] = _DIRECTION_ABBREVIATIONS[first]
    return " ".join(tokens)


def compact_city(value: str) -> str:
    """Normalised name without spaces, so "DE FOREST" matches "DEFOREST"."""
    return normalize_city(value).replace(" ", "")


# --------------------------------------------------------------------------- #
# Highways
# --------------------------------------------------------------------------- #

# "I-44", "I 44", "I-35E", "US-69", "US 69", "US HWY 151", "US-HWY 20".
# The look-behind stops matches inside words such as "BUS 71" or "HI 5".
_NATIONAL_ROUTE_RE = re.compile(
    r"(?<![A-Z0-9])(I|US)(?:[\s-]*(?:HWY|HIGHWAY|RTE|ROUTE))?[\s-]*(\d{1,3})(?:[A-Z])?(?![0-9])"
)
# The dataset occasionally types the Interstate "I" as the digit one: "1-40 EXIT 77".
_INTERSTATE_TYPO_RE = re.compile(r"(?<![A-Z0-9-])1-(\d{1,3})(?![0-9])")
# OSRM street names such as "Interstate 44" or "US Highway 69".
_LONG_FORM_RE = re.compile(r"\b(INTERSTATE|US HIGHWAY|US ROUTE|U\.S\. HIGHWAY|U\.S\. ROUTE)\s+(\d{1,3})\b")


def _canonical(kind: str, number: str) -> str:
    prefix = "I" if kind in {"I", "INTERSTATE"} else "US"
    return f"{prefix}-{int(number)}"


def parse_station_highways(address: str) -> tuple[str, ...]:
    """Interstate and US routes mentioned in a station address.

    >>> parse_station_highways("I-44, EXIT 283 & US-69")
    ('I-44', 'US-69')
    >>> parse_station_highways("SR-29/SR-55, EXIT 234")
    ()
    """
    text = (address or "").upper()
    found = {_canonical(kind, number) for kind, number in _NATIONAL_ROUTE_RE.findall(text)}
    found.update(f"I-{int(number)}" for number in _INTERSTATE_TYPO_RE.findall(text))
    return tuple(sorted(found))


def parse_osrm_highways(ref: str | None, name: str | None) -> frozenset[str]:
    """Interstate and US routes from an OSRM step's `ref` and `name` fields.

    OSRM refs look like "I 44;US 69"; names look like "Interstate 44".
    """
    found: set[str] = set()
    for part in (ref or "").upper().split(";"):
        found.update(_canonical(kind, number) for kind, number in _NATIONAL_ROUTE_RE.findall(part))
    upper_name = (name or "").upper()
    found.update(
        _canonical(kind.split(" ")[0].replace(".", ""), number) for kind, number in _LONG_FORM_RE.findall(upper_name)
    )
    return frozenset(found)


# --------------------------------------------------------------------------- #
# States
# --------------------------------------------------------------------------- #

US_STATES: dict[str, str] = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}
_STATE_NAME_TO_CODE = {name.upper(): code for code, name in US_STATES.items()}
_STATE_NAME_TO_CODE.update({"WASHINGTON DC": "DC", "WASHINGTON D C": "DC", "D C": "DC"})


def to_state_code(value: str) -> str | None:
    """'TX', 'tx', 'Texas' -> 'TX'. Returns None for anything else."""
    text = _SPACE_RE.sub(" ", _PUNCTUATION_RE.sub("", value.upper())).strip()
    if text in US_STATES:
        return text
    return _STATE_NAME_TO_CODE.get(text)
