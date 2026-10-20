"""한도 재산정 배치."""


def recalc_limits(accounts: list[dict]) -> list[str]:
    """DSR 한도를 넘는 계좌 ID 목록."""
    over = []
    for acc in accounts:
        income = acc["annual_income"]
        if income <= 0:
            continue
        dsr = acc["annual_payment"] / income
        if dsr > 0.4:
            over.append(acc["account_id"])
    return over
