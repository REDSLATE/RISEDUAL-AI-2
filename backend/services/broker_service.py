import os
import logging
from typing import Optional, List, Dict
import requests
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class AlpacaTradingService:
    """Alpaca is the easiest broker to integrate - commission-free API-first trading"""
    
    def __init__(self, api_key: str, api_secret: str, paper: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = 'https://paper-api.alpaca.markets' if paper else 'https://api.alpaca.markets'
        self.headers = {
            'APCA-API-KEY-ID': api_key,
            'APCA-API-SECRET-KEY': api_secret
        }
    
    def get_account(self) -> Optional[Dict]:
        """Get account information"""
        try:
            response = requests.get(f'{self.base_url}/v2/account', headers=self.headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching account: {str(e)}")
            return None
    
    def get_positions(self) -> List[Dict]:
        """Get all open positions"""
        try:
            response = requests.get(f'{self.base_url}/v2/positions', headers=self.headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching positions: {str(e)}")
            return []
    
    def place_order(self, symbol: str, qty: int, side: str, order_type: str = 'market',
                   time_in_force: str = 'day', limit_price: Optional[float] = None,
                   stop_price: Optional[float] = None) -> Optional[Dict]:
        """Place a trading order"""
        try:
            order_data = {
                'symbol': symbol.upper(),
                'qty': qty,
                'side': side.lower(),
                'type': order_type.lower(),
                'time_in_force': time_in_force.lower()
            }
            
            if limit_price:
                order_data['limit_price'] = limit_price
            if stop_price:
                order_data['stop_price'] = stop_price
            
            response = requests.post(
                f'{self.base_url}/v2/orders',
                headers=self.headers,
                json=order_data
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error placing order: {str(e)}")
            return None
    
    def get_order(self, order_id: str) -> Optional[Dict]:
        """Get order status"""
        try:
            response = requests.get(
                f'{self.base_url}/v2/orders/{order_id}',
                headers=self.headers
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching order: {str(e)}")
            return None
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        try:
            response = requests.delete(
                f'{self.base_url}/v2/orders/{order_id}',
                headers=self.headers
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Error cancelling order: {str(e)}")
            return False
    
    def get_orders(self, status: str = 'all', limit: int = 50) -> List[Dict]:
        """Get all orders"""
        try:
            params = {'status': status, 'limit': limit}
            response = requests.get(
                f'{self.base_url}/v2/orders',
                headers=self.headers,
                params=params
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching orders: {str(e)}")
            return []

class BrokerService:
    """Main broker service that handles multiple brokers"""
    
    @staticmethod
    def get_broker_client(broker_id: str, credentials: Dict):
        """Factory method to get appropriate broker client"""
        if broker_id == 'alpaca':
            return AlpacaTradingService(
                api_key=credentials.get('api_key'),
                api_secret=credentials.get('api_secret'),
                paper=credentials.get('paper', True)
            )
        # Add more brokers here:
        # elif broker_id == 'td_ameritrade':
        #     return TDAmeritradeTradingService(...)
        # elif broker_id == 'interactive_brokers':
        #     return InteractiveBrokersTradingService(...)
        else:
            raise ValueError(f"Unsupported broker: {broker_id}")