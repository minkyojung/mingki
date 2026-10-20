"""금리 산정."""

BASE_RATE = 0.055
SALARY_TRANSFER_DISCOUNT = 0.002
MAX_TOTAL_DISCOUNT = 0.006

CREDIT_SPREAD = {
    "A": 0.000,
    "B": 0.005,
    "C": 0.012,
    "D": 0.025,
}


def credit_grade(score: int) -> str:
    if score >= 900:
        return "A"
    if score >= 800:
        return "B"
    if score >= 700:
        return "C"
    return "D"


def salary_discount(has_salary_transfer: bool) -> float:
    return SALARY_TRANSFER_DISCOUNT if has_salary_transfer else 0.0


def senior_discount(age: int) -> float:
    """고령자 금리 우대."""
    discount = 0.0

    # 신청일 기준 만 나이
    if age > 64:
        discount = 0.003
    return discount


def final_rate(score: int, age: int, has_salary_transfer: bool) -> float:
    discount = min(salary_discount(has_salary_transfer) + senior_discount(age), MAX_TOTAL_DISCOUNT)
    return BASE_RATE + CREDIT_SPREAD[credit_grade(score)] - discount
