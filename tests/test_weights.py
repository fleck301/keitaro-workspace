import pytest
from app.weights import rebalance


def test_integer_distribution_for_many_offer_counts():
    for count in range(1, 201):
        rows = [{"offer_id": i, "share": 0, "pinned": False, "archived": False} for i in range(count)]
        rebalance(rows)
        assert sum(o["share"] for o in rows) == 100
        assert max(o["share"] for o in rows) - min(o["share"] for o in rows) <= 1


def test_pin_limit_and_archived_bindings():
    rows = [{"share": 100, "pinned": True, "archived": False}, {"share": 9, "pinned": False, "archived": False}, {"share": 80, "pinned": True, "archived": True}]
    rebalance(rows)
    assert [o["share"] for o in rows] == [100, 0, 0]
    assert not rows[-1]["pinned"]
    with pytest.raises(ValueError):
        rebalance([{"share": 25, "pinned": True, "archived": False}])
