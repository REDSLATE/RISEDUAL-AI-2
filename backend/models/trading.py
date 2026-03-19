from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
import uuid

class BrokerConnection(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str  # In production, this would be from authenticated user
    broker_id: str  # 'td_ameritrade', 'alpaca', etc.
    broker_name: str
    access_token: str
    refresh_token: Optional[str] = None
    token_expires_at: Optional[datetime] = None
    account_id: Optional[str] = None
    is_active: bool = True
    connected_at: datetime = Field(default_factory=datetime.utcnow)
    last_used: datetime = Field(default_factory=datetime.utcnow)

class TradeOrder(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    broker_connection_id: str
    symbol: str
    order_type: str  # 'market', 'limit', 'stop', 'stop_limit'
    side: str  # 'buy', 'sell'
    quantity: int
    price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: str = 'day'  # 'day', 'gtc', 'ioc', 'fok'
    status: str = 'pending'  # 'pending', 'submitted', 'filled', 'cancelled', 'rejected'
    broker_order_id: Optional[str] = None
    filled_quantity: int = 0
    average_fill_price: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Position(BaseModel):
    symbol: str
    quantity: int
    average_entry_price: float
    current_price: float
    market_value: float
    profit_loss: float
    profit_loss_percent: float
    side: str  # 'long', 'short'

class AccountBalance(BaseModel):
    broker_name: str
    account_id: str
    cash: float
    buying_power: float
    portfolio_value: float
    total_profit_loss: float
    updated_at: datetime = Field(default_factory=datetime.utcnow)