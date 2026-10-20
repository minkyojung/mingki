from loan.ltv import within_ltv


def test_within_ltv_boundary():
    assert within_ltv(36, 100)
    assert not within_ltv(37, 100)
