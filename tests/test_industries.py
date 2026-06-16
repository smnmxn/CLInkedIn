import pytest

from clinkedin.industries import IndustryError, resolve_industry


# ---------- resolve_industry ----------


def test_resolve_exact_name():
    assert resolve_industry("Financial Services") == "43"


def test_resolve_case_insensitive():
    assert resolve_industry("financial services") == "43"
    assert resolve_industry("COMPUTER SOFTWARE") == "4"


def test_resolve_strips_whitespace():
    assert resolve_industry("  Banking  ") == "41"


def test_numeric_code_passes_through():
    # codes absent from the table must still be reachable
    assert resolve_industry("9999") == "9999"
    assert resolve_industry("43") == "43"


def test_modern_ui_alias_resolves():
    # LinkedIn UI label differs from the classic table name but maps to code 100
    assert resolve_industry("Non-profit Organizations") == "100"
    assert resolve_industry("non-profit organizations") == "100"
    # classic name still resolves to the same code
    assert resolve_industry("Nonprofit Organization Management") == "100"
    # healthcare UI label vs classic name, same code 14
    assert resolve_industry("Hospitals and Health Care") == "14"
    assert resolve_industry("Hospital & Health Care") == "14"


def test_unambiguous_substring_resolves():
    # only one industry contains "biotech"
    assert resolve_industry("biotech") == "12"


def test_ambiguous_substring_raises():
    # "education" is a substring of several industries and not an exact name
    with pytest.raises(IndustryError, match="Ambiguous"):
        resolve_industry("education")


def test_unknown_raises():
    with pytest.raises(IndustryError, match="Unknown industry"):
        resolve_industry("Underwater Basket Weaving")


def test_empty_raises():
    with pytest.raises(IndustryError, match="Empty"):
        resolve_industry("   ")
