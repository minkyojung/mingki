from loan.income import estimate_lifetime_income, remaining_working_years


def test_remaining_working_years():
    assert remaining_working_years(40) == 25
    assert remaining_working_years(60) == 5
    assert remaining_working_years(70) == 0


def test_lifetime_income():
    assert estimate_lifetime_income(50_000_000, 55) == 500_000_000
