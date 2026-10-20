from loan.rate import credit_grade, final_rate


def test_credit_grade():
    assert credit_grade(950) == "A"
    assert credit_grade(650) == "D"


def test_discount_is_capped():
    assert final_rate(950, 30, True) == 0.055 - 0.002
