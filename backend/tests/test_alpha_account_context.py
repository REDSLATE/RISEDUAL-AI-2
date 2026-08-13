import pytest

from services.alpha_broker_account_context import AccountContextService
from services.alpha_account_decision_overlay import evaluate_account_fit


class FakeBroker:
    async def get_account(self):
        return {"equity": 1000, "cash": 300, "buying_power": 300}

    async def list_positions(self):
        return [{
            "symbol": "NVDA", "qty": 1, "avg_entry_price": 100,
            "market_value": 120, "unrealized_pl": 20,
        }]

    async def list_open_orders(self):
        return []


@pytest.mark.asyncio
async def test_reads_broker_and_normalizes():
    ctx = await AccountContextService(FakeBroker(), broker_name="fake").get()
    assert ctx.equity == 1000
    assert ctx.buying_power == 300
    assert ctx.positions[0].symbol == "NVDA"


@pytest.mark.asyncio
async def test_existing_position_reduces_add():
    ctx = await AccountContextService(FakeBroker()).get()
    fit = evaluate_account_fit(
        context=ctx, symbol="NVDA", side="BUY", desired_notional=50,
    )
    assert fit.verdict in {"PASS", "REDUCE"}
    assert fit.size_multiplier <= 1.0
    assert "EXISTING_POSITION" in fit.reasons
