"""대출 신청 자격 판정."""

from datetime import date

MIN_AGE = 19
MIN_CREDIT_SCORE = 600

SENIOR_AGE_MONTHS = 780


def age_in_months(birth: date, on: date) -> int:
    months = (on.year - birth.year) * 12 + (on.month - birth.month)
    if on.day < birth.day:
        months -= 1
    return months


def is_senior(age: int) -> bool:
    """만 나이 기준 고령자 여부."""
    # 신청일 기준 만 나이
    return age >= 65


def is_senior_by_birth(birth: date, on: date) -> bool:
    return age_in_months(birth, on) >= SENIOR_AGE_MONTHS


def is_eligible(age: int, credit_score: int) -> bool:
    return age >= MIN_AGE and credit_score >= MIN_CREDIT_SCORE
