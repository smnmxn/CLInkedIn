"""Resolve LinkedIn industry names to the numeric codes the search filter wants.

WHY a bundled static table instead of a live typeahead lookup: LinkedIn's
filter-value typeahead moved to GraphQL behind a version-pinned `queryId` hash
(the same brittle scheme `linkedin_api`'s search uses). That hash can't be
discovered without scraping the live web client and rotates without notice, so a
typeahead-backed resolver would break unpredictably and cost an extra request per
search. The industry taxonomy itself is effectively static, so we map names to
codes offline — zero extra requests, nothing to break on a hash rotation.

The codes are LinkedIn's classic v1 industry enumeration. Confirmed against the
live `search_people` `industries` filter: e.g. 4 (Computer Software) and 43
(Financial Services) return disjoint, valid 1st-degree result sets. Industries
not in this table can still be filtered by passing a raw numeric code.
"""

from __future__ import annotations


class IndustryError(Exception):
    pass


# name -> LinkedIn industry code (classic v1 enumeration)
INDUSTRIES: dict[str, str] = {
    "Defense & Space": "1",
    "Computer Hardware": "3",
    "Computer Software": "4",
    "Computer Networking": "5",
    "Internet": "6",
    "Semiconductors": "7",
    "Telecommunications": "8",
    "Law Practice": "9",
    "Legal Services": "10",
    "Management Consulting": "11",
    "Biotechnology": "12",
    "Medical Practice": "13",
    "Hospital & Health Care": "14",
    "Pharmaceuticals": "15",
    "Veterinary": "16",
    "Medical Devices": "17",
    "Cosmetics": "18",
    "Apparel & Fashion": "19",
    "Sporting Goods": "20",
    "Tobacco": "21",
    "Supermarkets": "22",
    "Food Production": "23",
    "Consumer Electronics": "24",
    "Consumer Goods": "25",
    "Furniture": "26",
    "Retail": "27",
    "Entertainment": "28",
    "Gambling & Casinos": "29",
    "Leisure, Travel & Tourism": "30",
    "Hospitality": "31",
    "Restaurants": "32",
    "Sports": "33",
    "Food & Beverages": "34",
    "Motion Pictures and Film": "35",
    "Broadcast Media": "36",
    "Museums and Institutions": "37",
    "Fine Art": "38",
    "Performing Arts": "39",
    "Recreational Facilities and Services": "40",
    "Banking": "41",
    "Insurance": "42",
    "Financial Services": "43",
    "Real Estate": "44",
    "Investment Banking": "45",
    "Investment Management": "46",
    "Accounting": "47",
    "Construction": "48",
    "Building Materials": "49",
    "Architecture & Planning": "50",
    "Civil Engineering": "51",
    "Aviation & Aerospace": "52",
    "Automotive": "53",
    "Chemicals": "54",
    "Machinery": "55",
    "Mining & Metals": "56",
    "Oil & Energy": "57",
    "Shipbuilding": "58",
    "Utilities": "59",
    "Textiles": "60",
    "Paper & Forest Products": "61",
    "Railroad Manufacture": "62",
    "Farming": "63",
    "Ranching": "64",
    "Dairy": "65",
    "Fishery": "66",
    "Primary/Secondary Education": "67",
    "Higher Education": "68",
    "Education Management": "69",
    "Research": "70",
    "Military": "71",
    "Legislative Office": "72",
    "Judiciary": "73",
    "International Affairs": "74",
    "Government Administration": "75",
    "Executive Office": "76",
    "Law Enforcement": "77",
    "Public Safety": "78",
    "Public Policy": "79",
    "Marketing and Advertising": "80",
    "Newspapers": "81",
    "Publishing": "82",
    "Printing": "83",
    "Information Services": "84",
    "Libraries": "85",
    "Environmental Services": "86",
    "Package/Freight Delivery": "87",
    "Individual & Family Services": "88",
    "Religious Institutions": "89",
    "Civic & Social Organization": "90",
    "Consumer Services": "91",
    "Transportation/Trucking/Railroad": "92",
    "Warehousing": "93",
    "Airlines/Aviation": "94",
    "Maritime": "95",
    "Information Technology and Services": "96",
    "Market Research": "97",
    "Public Relations and Communications": "98",
    "Design": "99",
    "Nonprofit Organization Management": "100",
    "Fund-Raising": "101",
    "Program Development": "102",
    "Writing and Editing": "103",
    "Staffing and Recruiting": "104",
    "Professional Training & Coaching": "105",
    "Venture Capital & Private Equity": "106",
    "Political Organization": "107",
    "Translation and Localization": "108",
    "Computer Games": "109",
    "Events Services": "110",
    "Arts and Crafts": "111",
    "Electrical/Electronic Manufacturing": "112",
    "Online Media": "113",
    "Nanotechnology": "114",
    "Music": "115",
    "Logistics and Supply Chain": "116",
    "Plastics": "117",
    "Computer & Network Security": "118",
    "Wireless": "119",
    "Alternative Dispute Resolution": "120",
    "Security and Investigations": "121",
    "Facilities Services": "122",
    "Outsourcing/Offshoring": "123",
    "Health, Wellness and Fitness": "124",
    "Alternative Medicine": "125",
    "Media Production": "126",
    "Animation": "127",
    "Commercial Real Estate": "128",
    "Capital Markets": "129",
    "Think Tanks": "130",
    "Philanthropy": "131",
    "E-Learning": "132",
    "Wholesale": "133",
    "Import and Export": "134",
    "Mechanical or Industrial Engineering": "135",
    "Photography": "136",
    "Human Resources": "137",
    "Business Supplies and Equipment": "138",
    "Mental Health Care": "139",
    "Graphic Design": "140",
    "International Trade and Development": "141",
    "Wine and Spirits": "142",
    "Luxury Goods & Jewelry": "143",
    "Renewables & Environment": "144",
    "Glass, Ceramics & Concrete": "145",
    "Packaging and Containers": "146",
    "Industrial Automation": "147",
    "Government Relations": "148",
}

# Modern LinkedIn UI labels (v2 taxonomy) that differ from the classic names in
# INDUSTRIES but map to the same still-valid code. Add entries here as they're
# confirmed against the live filter; keys are matched case-insensitively for
# exact lookup only (they're intentionally excluded from substring suggestions).
ALIASES: dict[str, str] = {
    # UI shows "Non-profit Organizations"; classic name is
    # "Nonprofit Organization Management" (code 100, verified against the
    # live 1st-degree search filter).
    "Non-profit Organizations": "100",
    # UI shows "Hospitals and Health Care"; classic name is
    # "Hospital & Health Care" (code 14).
    "Hospitals and Health Care": "14",
}

# lower-cased name -> code, for case-insensitive exact lookup (classic + aliases)
_BY_LOWER = {name.lower(): code for name, code in INDUSTRIES.items()}
_BY_LOWER.update({name.lower(): code for name, code in ALIASES.items()})


def resolve_industry(value: str) -> str:
    """Resolve an industry name (or raw numeric code) to a LinkedIn industry code.

    Accepts a numeric code verbatim (so industries absent from the bundled table
    remain reachable), an exact case-insensitive name, or an unambiguous
    case-insensitive substring of exactly one industry name.
    """
    v = value.strip()
    if not v:
        raise IndustryError("Empty industry value.")
    if v.isdigit():
        return v

    lower = v.lower()
    if lower in _BY_LOWER:
        return _BY_LOWER[lower]

    matches = [name for name in INDUSTRIES if lower in name.lower()]
    if len(matches) == 1:
        return INDUSTRIES[matches[0]]
    if len(matches) > 1:
        shown = ", ".join(sorted(matches)[:8])
        more = "" if len(matches) <= 8 else f" (+{len(matches) - 8} more)"
        raise IndustryError(
            f"Ambiguous industry {value!r} matches: {shown}{more}. "
            "Use a more specific name or a numeric code."
        )
    raise IndustryError(
        f"Unknown industry {value!r}. Pass an exact LinkedIn industry name "
        "(e.g. 'Financial Services') or a numeric code (e.g. 43)."
    )
