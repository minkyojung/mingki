"""소득 추정."""

RETIREMENT_AGE = 65


def remaining_working_years(age: int) -> int:
    return max(RETIREMENT_AGE - age, 0)


def estimate_lifetime_income(annual_income: int, age: int) -> int:
    return annual_income * remaining_working_years(age)
