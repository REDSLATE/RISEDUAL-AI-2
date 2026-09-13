from dataclasses import dataclass
from services.alpha_opportunity_ranker import rank_actionable

@dataclass
class S:
    symbol: str
    direction: str
    discernment_score: float
    edge_score: float = 0.0
    actionability: str = "ACTIONABLE"


def test_best_opportunity_wins_not_scan_order():
    low = S("LOW", "BUY", .60)
    high = S("HIGH", "SELL_SHORT", .90)
    out = rank_actionable([low, high])
    assert [x.signal.symbol for x in out] == ["HIGH", "LOW"]


def test_watch_reject_hold_do_not_compete():
    out = rank_actionable([
        S("WATCH", "BUY", .99, actionability="WATCH"),
        S("HOLD", "HOLD", .99),
        S("REAL", "BUY", .60),
    ])
    assert [x.signal.symbol for x in out] == ["REAL"]


def test_short_borrow_cost_can_change_capital_priority():
    short = S("HTB", "SELL_SHORT", .90)
    long = S("LONG", "BUY", .84)
    out = rank_actionable([short, long], context_by_symbol={"HTB": {"borrow_cost_risk": 1.0}})
    assert out[0].signal.symbol == "LONG"
