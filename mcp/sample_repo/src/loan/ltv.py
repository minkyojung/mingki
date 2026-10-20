"""담보 LTV 계산."""

COLLATERAL_HAIRCUT = 0.9

LTV_LIMIT = 0.40


def ltv(loan_amount: int, collateral_value: int) -> float:
    return loan_amount / (collateral_value * COLLATERAL_HAIRCUT)


def within_ltv(loan_amount: int, collateral_value: int) -> bool:
    return ltv(loan_amount, collateral_value) <= LTV_LIMIT
