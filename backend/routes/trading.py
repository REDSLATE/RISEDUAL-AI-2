"""Trading & broker integration routes."""
from fastapi import APIRouter, HTTPException
from typing import Dict
import os
import logging

router = APIRouter(prefix="/api")


def _get_alpaca_credentials():
    return {
        'api_key': os.environ.get('ALPACA_API_KEY'),
        'api_secret': os.environ.get('ALPACA_API_SECRET'),
        'paper': os.environ.get('ALPACA_PAPER_TRADING', 'true').lower() == 'true'
    }


@router.post("/broker/connect")
async def connect_broker(broker_id: str, credentials: Dict):
    try:
        from services.broker_service import BrokerService
        client = BrokerService.get_broker_client(broker_id, credentials)
        account = client.get_account()
        if not account:
            raise HTTPException(status_code=400, detail="Failed to connect to broker")
        return {
            "status": "connected",
            "broker_id": broker_id,
            "account_id": account.get('account_number', 'N/A'),
            "message": "Successfully connected to broker"
        }
    except Exception as e:
        logging.error(f"Error connecting to broker: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trading/account/{broker_id}")
async def get_account_info(broker_id: str):
    try:
        from services.broker_service import BrokerService
        client = BrokerService.get_broker_client(broker_id, _get_alpaca_credentials())
        account = client.get_account()
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        return {
            "broker": broker_id,
            "account_id": account.get('account_number'),
            "cash": float(account.get('cash', 0)),
            "buying_power": float(account.get('buying_power', 0)),
            "portfolio_value": float(account.get('portfolio_value', 0)),
            "equity": float(account.get('equity', 0))
        }
    except Exception as e:
        logging.error(f"Error fetching account: {e}")
        raise HTTPException(status_code=500, detail="Error fetching account")


@router.get("/trading/positions/{broker_id}")
async def get_positions(broker_id: str):
    try:
        from services.broker_service import BrokerService
        client = BrokerService.get_broker_client(broker_id, _get_alpaca_credentials())
        return client.get_positions()
    except Exception as e:
        logging.error(f"Error fetching positions: {e}")
        raise HTTPException(status_code=500, detail="Error fetching positions")


@router.post("/trading/order/{broker_id}")
async def place_order(broker_id: str, order: Dict):
    try:
        from services.broker_service import BrokerService
        client = BrokerService.get_broker_client(broker_id, _get_alpaca_credentials())
        result = client.place_order(
            symbol=order.get('symbol'),
            qty=order.get('quantity'),
            side=order.get('side'),
            order_type=order.get('type', 'market'),
            time_in_force=order.get('time_in_force', 'day'),
            limit_price=order.get('limit_price'),
            stop_price=order.get('stop_price')
        )
        if not result:
            raise HTTPException(status_code=400, detail="Failed to place order")
        return result
    except Exception as e:
        logging.error(f"Error placing order: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trading/orders/{broker_id}")
async def get_orders(broker_id: str, status: str = 'all'):
    try:
        from services.broker_service import BrokerService
        client = BrokerService.get_broker_client(broker_id, _get_alpaca_credentials())
        return client.get_orders(status=status)
    except Exception as e:
        logging.error(f"Error fetching orders: {e}")
        raise HTTPException(status_code=500, detail="Error fetching orders")


@router.delete("/trading/order/{broker_id}/{order_id}")
async def cancel_order(broker_id: str, order_id: str):
    try:
        from services.broker_service import BrokerService
        client = BrokerService.get_broker_client(broker_id, _get_alpaca_credentials())
        success = client.cancel_order(order_id)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to cancel order")
        return {"status": "cancelled", "order_id": order_id}
    except Exception as e:
        logging.error(f"Error cancelling order: {e}")
        raise HTTPException(status_code=500, detail=str(e))
