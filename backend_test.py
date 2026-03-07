#!/usr/bin/env python3

import requests
import json
import sys
import time
from typing import Dict, List, Optional

# Backend URL from frontend .env
BACKEND_URL = "https://algo-trader-ai-1.preview.emergentagent.com/api"

class TradealgoAPITester:
    def __init__(self):
        self.base_url = BACKEND_URL
        self.session = requests.Session()
        self.results = {}
        
    def test_endpoint(self, method: str, endpoint: str, data: Dict = None, expected_status: int = 200) -> Dict:
        """Test a single API endpoint"""
        url = f"{self.base_url}{endpoint}"
        try:
            print(f"Testing {method} {url}")
            
            if method.upper() == "GET":
                response = self.session.get(url, timeout=30)
            elif method.upper() == "POST":
                response = self.session.post(url, json=data, timeout=30)
            else:
                return {"success": False, "error": f"Unsupported method: {method}"}
            
            print(f"  Status Code: {response.status_code}")
            
            # Check if status code matches expected
            if response.status_code != expected_status:
                return {
                    "success": False, 
                    "status_code": response.status_code,
                    "error": f"Expected {expected_status}, got {response.status_code}",
                    "response_text": response.text[:500]
                }
            
            # Try to parse JSON response
            try:
                json_data = response.json()
                print(f"  Response preview: {str(json_data)[:200]}...")
                return {
                    "success": True,
                    "status_code": response.status_code,
                    "data": json_data,
                    "response_size": len(str(json_data))
                }
            except json.JSONDecodeError:
                return {
                    "success": False,
                    "status_code": response.status_code,
                    "error": "Invalid JSON response",
                    "response_text": response.text[:500]
                }
                
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"Request failed: {str(e)}"
            }
    
    def validate_stock_ticker_data(self, data) -> List[str]:
        """Validate stock ticker data structure"""
        errors = []
        if not isinstance(data, list):
            errors.append("Ticker data should be a list")
            return errors
        
        for i, ticker in enumerate(data):
            if not isinstance(ticker, dict):
                errors.append(f"Ticker {i} should be a dict")
                continue
            
            required_fields = ['symbol', 'price', 'change', 'changePercent']
            for field in required_fields:
                if field not in ticker:
                    errors.append(f"Ticker {i} missing field: {field}")
                elif field in ['price', 'change', 'changePercent'] and not isinstance(ticker[field], (int, float)):
                    errors.append(f"Ticker {i} field {field} should be numeric")
        
        return errors
    
    def validate_quote_data(self, data, symbol: str) -> List[str]:
        """Validate individual stock quote data"""
        errors = []
        if not isinstance(data, dict):
            errors.append("Quote data should be a dict")
            return errors
        
        required_fields = ['symbol', 'price', 'change', 'changePercent', 'volume', 'high', 'low']
        for field in required_fields:
            if field not in data:
                errors.append(f"Quote missing field: {field}")
            elif field != 'symbol' and not isinstance(data[field], (int, float)):
                errors.append(f"Quote field {field} should be numeric")
        
        if 'symbol' in data and data['symbol'].upper() != symbol.upper():
            errors.append(f"Quote symbol mismatch: expected {symbol}, got {data['symbol']}")
        
        return errors
    
    def validate_options_data(self, data, endpoint_type: str) -> List[str]:
        """Validate options data structure"""
        errors = []
        
        if endpoint_type == 'radar':
            if not isinstance(data, dict):
                errors.append("Options radar should be a dict")
                return errors
            
            required_sections = ['mostActivelyTraded', 'volatilityOpportunities']
            for section in required_sections:
                if section not in data:
                    errors.append(f"Options radar missing section: {section}")
                elif not isinstance(data[section], list):
                    errors.append(f"Options radar {section} should be a list")
        
        elif endpoint_type in ['momentum', 'fast_movers', 'unusual_volume']:
            if not isinstance(data, list):
                errors.append(f"Options {endpoint_type} should be a list")
        
        elif endpoint_type == 'flow':
            if not isinstance(data, dict):
                errors.append("Options flow should be a dict")
                return errors
            
            expected_sections = ['mostActivelyTraded', 'dteEdge', 'volatilityLow', 'volatilityHigh']
            for section in expected_sections:
                if section not in data:
                    errors.append(f"Options flow missing section: {section}")
                elif not isinstance(data[section], list):
                    errors.append(f"Options flow {section} should be a list")
        
        return errors
    
    def validate_crypto_data(self, data) -> List[str]:
        """Validate crypto data structure"""
        errors = []
        if not isinstance(data, list):
            errors.append("Crypto data should be a list")
            return errors
        
        for i, crypto in enumerate(data):
            if not isinstance(crypto, dict):
                errors.append(f"Crypto {i} should be a dict")
                continue
            
            required_fields = ['symbol', 'price', 'market']
            for field in required_fields:
                if field not in crypto:
                    errors.append(f"Crypto {i} missing field: {field}")
        
        return errors
    
    def validate_dark_pool_data(self, data) -> List[str]:
        """Validate dark pool data structure"""
        errors = []
        if not isinstance(data, list):
            errors.append("Dark pool data should be a list")
            return errors
        
        for i, pool in enumerate(data):
            if not isinstance(pool, dict):
                errors.append(f"Dark pool {i} should be a dict")
                continue
            
            required_fields = ['symbol', 'darkPoolVolume', 'totalVolume', 'darkPoolPercent']
            for field in required_fields:
                if field not in pool:
                    errors.append(f"Dark pool {i} missing field: {field}")
        
        return errors
    
    def validate_chat_response(self, data, session_id: str) -> List[str]:
        """Validate chat response structure"""
        errors = []
        if not isinstance(data, dict):
            errors.append("Chat response should be a dict")
            return errors
        
        required_fields = ['response', 'sessionId']
        for field in required_fields:
            if field not in data:
                errors.append(f"Chat response missing field: {field}")
        
        if 'sessionId' in data and data['sessionId'] != session_id:
            errors.append(f"Session ID mismatch: expected {session_id}, got {data['sessionId']}")
        
        if 'response' in data and not isinstance(data['response'], str):
            errors.append("Chat response should be a string")
        
        return errors
    
    def run_all_tests(self):
        """Run comprehensive API tests"""
        print("=" * 60)
        print("TRADEALGO BACKEND API COMPREHENSIVE TESTING")
        print("=" * 60)
        
        # Test 1: Stock Market APIs
        print("\n1. TESTING STOCK MARKET APIs")
        print("-" * 40)
        
        # Stock ticker test
        result = self.test_endpoint("GET", "/stocks/ticker")
        self.results['stocks_ticker'] = result
        if result['success']:
            validation_errors = self.validate_stock_ticker_data(result['data'])
            if validation_errors:
                result['validation_errors'] = validation_errors
                result['success'] = False
                print(f"  ❌ Validation errors: {', '.join(validation_errors)}")
            else:
                print(f"  ✅ Ticker data valid with {len(result['data'])} stocks")
        else:
            print(f"  ❌ Error: {result.get('error', 'Unknown error')}")
        
        # Stock quote test for AAPL
        result = self.test_endpoint("GET", "/stocks/quote/AAPL")
        self.results['stocks_quote_aapl'] = result
        if result['success']:
            validation_errors = self.validate_quote_data(result['data'], 'AAPL')
            if validation_errors:
                result['validation_errors'] = validation_errors
                result['success'] = False
                print(f"  ❌ AAPL quote validation errors: {', '.join(validation_errors)}")
            else:
                print(f"  ✅ AAPL quote valid: ${result['data']['price']}")
        else:
            print(f"  ❌ AAPL quote error: {result.get('error', 'Unknown error')}")
        
        # Test 2: Options APIs
        print("\n2. TESTING OPTIONS APIs")
        print("-" * 40)
        
        options_endpoints = [
            ('radar', 'Options Radar'),
            ('flow', 'Options Flow'),
            ('momentum', 'Options Momentum'),
            ('fast-movers', 'Fast Movers'),
            ('unusual-volume', 'Unusual Volume')
        ]
        
        for endpoint, name in options_endpoints:
            result = self.test_endpoint("GET", f"/options/{endpoint}")
            self.results[f'options_{endpoint.replace("-", "_")}'] = result
            
            if result['success']:
                validation_errors = self.validate_options_data(result['data'], endpoint.replace('-', '_'))
                if validation_errors:
                    result['validation_errors'] = validation_errors
                    result['success'] = False
                    print(f"  ❌ {name} validation errors: {', '.join(validation_errors)}")
                else:
                    print(f"  ✅ {name} data valid")
            else:
                print(f"  ❌ {name} error: {result.get('error', 'Unknown error')}")
        
        # Test 3: Crypto APIs
        print("\n3. TESTING CRYPTO APIs")
        print("-" * 40)
        
        # Crypto prices test
        result = self.test_endpoint("GET", "/crypto/prices")
        self.results['crypto_prices'] = result
        if result['success']:
            validation_errors = self.validate_crypto_data(result['data'])
            if validation_errors:
                result['validation_errors'] = validation_errors
                result['success'] = False
                print(f"  ❌ Crypto prices validation errors: {', '.join(validation_errors)}")
            else:
                print(f"  ✅ Crypto prices valid with {len(result['data'])} currencies")
        else:
            print(f"  ❌ Crypto prices error: {result.get('error', 'Unknown error')}")
        
        # Bitcoin specific test
        result = self.test_endpoint("GET", "/crypto/BTC")
        self.results['crypto_btc'] = result
        if result['success']:
            validation_errors = self.validate_crypto_data([result['data']])
            if validation_errors:
                result['validation_errors'] = validation_errors
                result['success'] = False
                print(f"  ❌ BTC price validation errors: {', '.join(validation_errors)}")
            else:
                print(f"  ✅ BTC price valid: ${result['data']['price']}")
        else:
            print(f"  ❌ BTC price error: {result.get('error', 'Unknown error')}")
        
        # Test 4: Dark Pool API
        print("\n4. TESTING DARK POOL API")
        print("-" * 40)
        
        result = self.test_endpoint("GET", "/dark-pool")
        self.results['dark_pool'] = result
        if result['success']:
            validation_errors = self.validate_dark_pool_data(result['data'])
            if validation_errors:
                result['validation_errors'] = validation_errors
                result['success'] = False
                print(f"  ❌ Dark pool validation errors: {', '.join(validation_errors)}")
            else:
                print(f"  ✅ Dark pool data valid with {len(result['data'])} entries")
        else:
            print(f"  ❌ Dark pool error: {result.get('error', 'Unknown error')}")
        
        # Test 5: AI Chat API
        print("\n5. TESTING AI CHAT API")
        print("-" * 40)
        
        chat_data = {
            "message": "What's the market outlook for AAPL?",
            "sessionId": f"test-session-{int(time.time())}"
        }
        
        result = self.test_endpoint("POST", "/chat", data=chat_data)
        self.results['ai_chat'] = result
        if result['success']:
            validation_errors = self.validate_chat_response(result['data'], chat_data['sessionId'])
            if validation_errors:
                result['validation_errors'] = validation_errors
                result['success'] = False
                print(f"  ❌ Chat validation errors: {', '.join(validation_errors)}")
            else:
                response_text = result['data']['response']
                print(f"  ✅ AI chat working. Response: {response_text[:100]}...")
        else:
            print(f"  ❌ AI chat error: {result.get('error', 'Unknown error')}")
        
        # Test chat history
        result = self.test_endpoint("GET", f"/chat/history/{chat_data['sessionId']}")
        self.results['chat_history'] = result
        if result['success'] and 'messages' in result['data']:
            print(f"  ✅ Chat history retrieved with {len(result['data']['messages'])} messages")
        else:
            print(f"  ❌ Chat history error: {result.get('error', 'Unknown error')}")
        
        # Generate summary report
        self.generate_summary()
    
    def generate_summary(self):
        """Generate test summary report"""
        print("\n" + "=" * 60)
        print("TEST SUMMARY REPORT")
        print("=" * 60)
        
        total_tests = len(self.results)
        passed_tests = sum(1 for r in self.results.values() if r.get('success', False))
        failed_tests = total_tests - passed_tests
        
        print(f"Total Tests: {total_tests}")
        print(f"Passed: {passed_tests}")
        print(f"Failed: {failed_tests}")
        print(f"Success Rate: {(passed_tests/total_tests)*100:.1f}%")
        
        print("\nDETAILED RESULTS:")
        print("-" * 40)
        
        for test_name, result in self.results.items():
            status = "✅ PASS" if result.get('success', False) else "❌ FAIL"
            print(f"{test_name:25} | {status}")
            
            if not result.get('success', False):
                if 'error' in result:
                    print(f"  Error: {result['error']}")
                if 'validation_errors' in result:
                    print(f"  Validation: {', '.join(result['validation_errors'])}")
        
        print("\nCRITICAL ISSUES:")
        print("-" * 40)
        
        critical_issues = []
        
        # Check for critical failures
        for test_name, result in self.results.items():
            if not result.get('success', False):
                if 'chat' in test_name and 'error' in result:
                    critical_issues.append(f"AI Chat API failed: {result['error']}")
                elif result.get('status_code') == 500:
                    critical_issues.append(f"{test_name} returning server errors")
                elif 'validation_errors' in result:
                    critical_issues.append(f"{test_name} has data structure issues")
        
        if critical_issues:
            for issue in critical_issues:
                print(f"⚠️  {issue}")
        else:
            print("✅ No critical issues found")
        
        return {
            'total_tests': total_tests,
            'passed_tests': passed_tests,
            'failed_tests': failed_tests,
            'success_rate': (passed_tests/total_tests)*100,
            'critical_issues': critical_issues,
            'detailed_results': self.results
        }

if __name__ == "__main__":
    tester = TradealgoAPITester()
    tester.run_all_tests()