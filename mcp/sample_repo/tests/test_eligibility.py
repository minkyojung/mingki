from datetime import date

from loan.eligibility import age_in_months, is_eligible


def test_age_in_months():
    assert age_in_months(date(1960, 12, 15), date(2026, 12, 14)) == 791
    assert age_in_months(date(1960, 12, 15), date(2026, 12, 15)) == 792


def test_is_eligible():
    assert is_eligible(19, 600)
    assert not is_eligible(18, 900)
    assert not is_eligible(30, 599)
