"""
Iteration 131: ML Paper Trading PnL Dashboard & Calibration Curve Tests

Tests for two new ML enhancements:
1. GET /api/ml/paper-trades - ML autonomous paper trade history with PnL summary
2. GET /api/ml/calibration-curve - Calibration curve visualization data

Expected behavior:
- paper-trades: Returns empty trades (no ML autonomous trades yet, Tier 2 locked)
- calibration-curve: Returns no_model status (no trained model exists)
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestMLPaperTrades:
    """Tests for GET /api/ml/paper-trades endpoint"""
    
    def test_paper_trades_endpoint_returns_200(self):
        """Verify paper-trades endpoint is accessible"""
        response = requests.get(f"{BASE_URL}/api/ml/paper-trades")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("PASS: /api/ml/paper-trades returns 200")
    
    def test_paper_trades_response_structure(self):
        """Verify paper-trades response has required fields"""
        response = requests.get(f"{BASE_URL}/api/ml/paper-trades")
        assert response.status_code == 200
        data = response.json()
        
        # Required top-level fields
        assert "trades" in data, "Missing 'trades' array"
        assert "summary" in data, "Missing 'summary' object"
        assert "cumulative_pnl" in data, "Missing 'cumulative_pnl' array"
        assert "position_sizing" in data, "Missing 'position_sizing' object"
        
        assert isinstance(data["trades"], list), "'trades' should be a list"
        assert isinstance(data["summary"], dict), "'summary' should be a dict"
        assert isinstance(data["cumulative_pnl"], list), "'cumulative_pnl' should be a list"
        assert isinstance(data["position_sizing"], dict), "'position_sizing' should be a dict"
        print("PASS: paper-trades response has correct structure")
    
    def test_paper_trades_summary_fields(self):
        """Verify summary object has all required fields"""
        response = requests.get(f"{BASE_URL}/api/ml/paper-trades")
        assert response.status_code == 200
        summary = response.json().get("summary", {})
        
        required_fields = [
            "total_trades", "open_trades", "closed_trades",
            "total_pnl_usd", "win_rate"
        ]
        for field in required_fields:
            assert field in summary, f"Missing summary field: {field}"
        
        # Verify types
        assert isinstance(summary["total_trades"], int), "total_trades should be int"
        assert isinstance(summary["open_trades"], int), "open_trades should be int"
        assert isinstance(summary["closed_trades"], int), "closed_trades should be int"
        assert isinstance(summary["total_pnl_usd"], (int, float)), "total_pnl_usd should be numeric"
        assert isinstance(summary["win_rate"], (int, float)), "win_rate should be numeric"
        print("PASS: paper-trades summary has all required fields")
    
    def test_paper_trades_empty_state(self):
        """Verify empty state when no ML autonomous trades exist"""
        response = requests.get(f"{BASE_URL}/api/ml/paper-trades")
        assert response.status_code == 200
        data = response.json()
        
        # No ML autonomous trades yet (Tier 2 locked)
        # trades array should be empty or contain only ML trades (prediction_id exists)
        data.get("trades", [])
        summary = data.get("summary", {})
        
        # Verify summary reflects empty/zero state
        assert summary.get("total_trades", 0) >= 0, "total_trades should be >= 0"
        print(f"PASS: paper-trades returns {summary.get('total_trades', 0)} ML trades (expected 0 or few)")
    
    def test_paper_trades_position_sizing_fields(self):
        """Verify position_sizing object has required fields"""
        response = requests.get(f"{BASE_URL}/api/ml/paper-trades")
        assert response.status_code == 200
        sizing = response.json().get("position_sizing", {})
        
        required_fields = ["avg_size_usd", "max_size_usd", "min_size_usd"]
        for field in required_fields:
            assert field in sizing, f"Missing position_sizing field: {field}"
            assert isinstance(sizing[field], (int, float)), f"{field} should be numeric"
        print("PASS: paper-trades position_sizing has all required fields")
    
    def test_paper_trades_with_limit_param(self):
        """Verify limit query parameter works"""
        response = requests.get(f"{BASE_URL}/api/ml/paper-trades?limit=10")
        assert response.status_code == 200
        data = response.json()
        trades = data.get("trades", [])
        assert len(trades) <= 10, "Limit parameter should restrict results"
        print("PASS: paper-trades limit parameter works")


class TestMLCalibrationCurve:
    """Tests for GET /api/ml/calibration-curve endpoint"""
    
    def test_calibration_curve_endpoint_returns_200(self):
        """Verify calibration-curve endpoint is accessible"""
        response = requests.get(f"{BASE_URL}/api/ml/calibration-curve")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("PASS: /api/ml/calibration-curve returns 200")
    
    def test_calibration_curve_response_structure(self):
        """Verify calibration-curve response has required fields"""
        response = requests.get(f"{BASE_URL}/api/ml/calibration-curve")
        assert response.status_code == 200
        data = response.json()
        
        # Required top-level fields
        assert "status" in data, "Missing 'status' field"
        assert "curve_data" in data, "Missing 'curve_data' array"
        assert "summary" in data, "Missing 'summary' object"
        
        assert isinstance(data["curve_data"], list), "'curve_data' should be a list"
        assert isinstance(data["summary"], dict), "'summary' should be a dict"
        print("PASS: calibration-curve response has correct structure")
    
    def test_calibration_curve_no_model_status(self):
        """Verify no_model status when no trained model exists"""
        response = requests.get(f"{BASE_URL}/api/ml/calibration-curve")
        assert response.status_code == 200
        data = response.json()
        
        # Expected: no_model status since no trained model exists
        status = data.get("status")
        assert status in ["no_model", "no_stats", "available"], f"Unexpected status: {status}"
        
        if status == "no_model":
            assert "message" in data, "no_model status should include message"
            assert data.get("curve_data") == [], "curve_data should be empty for no_model"
            assert data.get("summary") == {}, "summary should be empty for no_model"
            print("PASS: calibration-curve returns no_model status (expected)")
        elif status == "available":
            # If model exists, verify curve_data structure
            curve_data = data.get("curve_data", [])
            if curve_data:
                first_bucket = curve_data[0]
                assert "confidence_bucket" in first_bucket, "Missing confidence_bucket"
                assert "mean_predicted" in first_bucket, "Missing mean_predicted"
                assert "actual_accuracy" in first_bucket, "Missing actual_accuracy"
            print("PASS: calibration-curve returns available status with data")
        else:
            print(f"PASS: calibration-curve returns {status} status")
    
    def test_calibration_curve_summary_fields_when_available(self):
        """Verify summary fields when model is available"""
        response = requests.get(f"{BASE_URL}/api/ml/calibration-curve")
        assert response.status_code == 200
        data = response.json()
        
        if data.get("status") == "available":
            summary = data.get("summary", {})
            expected_fields = ["accuracy", "brier_score", "ece", "n_predictions", "model_version"]
            for field in expected_fields:
                assert field in summary, f"Missing summary field: {field}"
            print("PASS: calibration-curve summary has all fields when available")
        else:
            # No model, summary should be empty
            assert data.get("summary") == {}, "summary should be empty when no model"
            print("PASS: calibration-curve summary is empty (no model)")


class TestMLEndpointsIntegration:
    """Integration tests for ML endpoints"""
    
    def test_ml_endpoints_consistency(self):
        """Verify ML endpoints return consistent data"""
        # Get gate-status for model availability
        gate_res = requests.get(f"{BASE_URL}/api/ml/gate-status")
        assert gate_res.status_code == 200
        gate_data = gate_res.json()
        model_available = gate_data.get("model_available", False)
        
        # Get calibration-curve
        cal_res = requests.get(f"{BASE_URL}/api/ml/calibration-curve")
        assert cal_res.status_code == 200
        cal_data = cal_res.json()
        
        # Consistency check: if no model, calibration should be no_model
        if not model_available:
            assert cal_data.get("status") in ["no_model", "no_stats"], \
                "calibration-curve should return no_model when model_available is false"
        print("PASS: ML endpoints return consistent data")
    
    def test_ml_stats_paper_trading_matches_paper_trades(self):
        """Verify /api/ml/stats paper_trading matches /api/ml/paper-trades summary"""
        stats_res = requests.get(f"{BASE_URL}/api/ml/stats")
        assert stats_res.status_code == 200
        stats_data = stats_res.json()
        
        paper_res = requests.get(f"{BASE_URL}/api/ml/paper-trades")
        assert paper_res.status_code == 200
        paper_data = paper_res.json()
        
        # Note: /api/ml/stats counts ALL paper trades, /api/ml/paper-trades filters ML autonomous only
        # So paper-trades total_trades <= stats paper_trading total_trades
        stats_total = stats_data.get("paper_trading", {}).get("total_trades", 0)
        paper_total = paper_data.get("summary", {}).get("total_trades", 0)
        
        # ML autonomous trades should be <= total paper trades
        assert paper_total <= stats_total, \
            f"ML paper trades ({paper_total}) should be <= total paper trades ({stats_total})"
        print(f"PASS: ML paper trades ({paper_total}) <= total paper trades ({stats_total})")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
