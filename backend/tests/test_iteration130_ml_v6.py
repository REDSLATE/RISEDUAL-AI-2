"""
Iteration 130: ML Phase 3 v6 Backend Tests
Tests for:
- GET /api/ml/gate-status - v6 typed response with nested tiers
- GET /api/ml/stats - v6 response with data_progress, pattern_detection_counts, etc.
- GET /api/signal/{ticker} - graceful no_model status
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestMLGateStatus:
    """Tests for GET /api/ml/gate-status v6 response"""
    
    def test_gate_status_returns_200(self):
        """Gate status endpoint should return 200"""
        response = requests.get(f"{BASE_URL}/api/ml/gate-status")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("PASS: /api/ml/gate-status returns 200")
    
    def test_gate_status_has_highest_tier(self):
        """Response should have highest_tier field"""
        response = requests.get(f"{BASE_URL}/api/ml/gate-status")
        data = response.json()
        assert "highest_tier" in data, "Missing highest_tier field"
        assert data["highest_tier"] in ["locked", "tier1", "tier2", "tier3"], f"Invalid highest_tier: {data['highest_tier']}"
        print(f"PASS: highest_tier = {data['highest_tier']}")
    
    def test_gate_status_has_nested_tiers(self):
        """Response should have nested tiers structure with unlocked/reason"""
        response = requests.get(f"{BASE_URL}/api/ml/gate-status")
        data = response.json()
        
        assert "tiers" in data, "Missing tiers field"
        tiers = data["tiers"]
        
        # Check tier1_alerts
        assert "tier1_alerts" in tiers, "Missing tier1_alerts in tiers"
        assert "unlocked" in tiers["tier1_alerts"], "tier1_alerts missing unlocked"
        assert "reason" in tiers["tier1_alerts"], "tier1_alerts missing reason"
        assert isinstance(tiers["tier1_alerts"]["unlocked"], bool), "tier1_alerts.unlocked should be bool"
        
        # Check tier2_paper
        assert "tier2_paper" in tiers, "Missing tier2_paper in tiers"
        assert "unlocked" in tiers["tier2_paper"], "tier2_paper missing unlocked"
        assert "reason" in tiers["tier2_paper"], "tier2_paper missing reason"
        
        # Check tier3_live
        assert "tier3_live" in tiers, "Missing tier3_live in tiers"
        assert "unlocked" in tiers["tier3_live"], "tier3_live missing unlocked"
        assert "reason" in tiers["tier3_live"], "tier3_live missing reason"
        
        print(f"PASS: Nested tiers structure correct - tier1={tiers['tier1_alerts']['unlocked']}, tier2={tiers['tier2_paper']['unlocked']}, tier3={tiers['tier3_live']['unlocked']}")
    
    def test_gate_status_has_current_stats(self):
        """Response should have current_stats with accuracy, n_predictions, ece, sharpe, max_drawdown, live_days"""
        response = requests.get(f"{BASE_URL}/api/ml/gate-status")
        data = response.json()
        
        assert "current_stats" in data, "Missing current_stats field"
        stats = data["current_stats"]
        
        required_fields = ["accuracy", "n_predictions", "ece", "sharpe", "max_drawdown", "live_days"]
        for field in required_fields:
            assert field in stats, f"current_stats missing {field}"
        
        # Type checks
        assert isinstance(stats["accuracy"], (int, float)), "accuracy should be numeric"
        assert isinstance(stats["n_predictions"], int), "n_predictions should be int"
        assert isinstance(stats["ece"], (int, float)), "ece should be numeric"
        assert isinstance(stats["sharpe"], (int, float)), "sharpe should be numeric"
        assert isinstance(stats["max_drawdown"], (int, float)), "max_drawdown should be numeric"
        assert isinstance(stats["live_days"], int), "live_days should be int"
        
        print(f"PASS: current_stats has all required fields - accuracy={stats['accuracy']}, n_predictions={stats['n_predictions']}")
    
    def test_gate_status_has_thresholds(self):
        """Response should have thresholds for tier1, tier2, tier3"""
        response = requests.get(f"{BASE_URL}/api/ml/gate-status")
        data = response.json()
        
        assert "thresholds" in data, "Missing thresholds field"
        thresholds = data["thresholds"]
        
        # Tier 1 thresholds
        assert "tier1" in thresholds, "Missing tier1 thresholds"
        assert "min_accuracy" in thresholds["tier1"], "tier1 missing min_accuracy"
        assert "min_predictions" in thresholds["tier1"], "tier1 missing min_predictions"
        assert "max_ece" in thresholds["tier1"], "tier1 missing max_ece"
        
        # Tier 2 thresholds
        assert "tier2" in thresholds, "Missing tier2 thresholds"
        assert "min_accuracy" in thresholds["tier2"], "tier2 missing min_accuracy"
        assert "min_sharpe" in thresholds["tier2"], "tier2 missing min_sharpe"
        assert "max_drawdown" in thresholds["tier2"], "tier2 missing max_drawdown"
        
        # Tier 3 thresholds
        assert "tier3" in thresholds, "Missing tier3 thresholds"
        assert "min_live_days" in thresholds["tier3"], "tier3 missing min_live_days"
        
        print(f"PASS: thresholds structure correct - tier1.min_accuracy={thresholds['tier1']['min_accuracy']}")
    
    def test_gate_status_has_next_milestone(self):
        """Response should have next_milestone with milestone, description, predictions_needed"""
        response = requests.get(f"{BASE_URL}/api/ml/gate-status")
        data = response.json()
        
        assert "next_milestone" in data, "Missing next_milestone field"
        milestone = data["next_milestone"]
        
        assert "milestone" in milestone, "next_milestone missing milestone"
        assert "description" in milestone, "next_milestone missing description"
        assert "predictions_needed" in milestone, "next_milestone missing predictions_needed"
        
        print(f"PASS: next_milestone = {milestone['milestone']}")
    
    def test_gate_status_has_model_available(self):
        """Response should have model_available boolean"""
        response = requests.get(f"{BASE_URL}/api/ml/gate-status")
        data = response.json()
        
        assert "model_available" in data, "Missing model_available field"
        assert isinstance(data["model_available"], bool), "model_available should be bool"
        
        print(f"PASS: model_available = {data['model_available']}")


class TestMLStats:
    """Tests for GET /api/ml/stats v6 response"""
    
    def test_stats_returns_200(self):
        """Stats endpoint should return 200"""
        response = requests.get(f"{BASE_URL}/api/ml/stats")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("PASS: /api/ml/stats returns 200")
    
    def test_stats_has_data_progress(self):
        """Response should have data_progress with total_snapshots, labeled, pending_labels, schema_v2_snapshots, outcome_distribution"""
        response = requests.get(f"{BASE_URL}/api/ml/stats")
        data = response.json()
        
        assert "data_progress" in data, "Missing data_progress field"
        dp = data["data_progress"]
        
        required_fields = ["total_snapshots", "labeled", "pending_labels", "schema_v2_snapshots", "outcome_distribution"]
        for field in required_fields:
            assert field in dp, f"data_progress missing {field}"
        
        assert isinstance(dp["total_snapshots"], int), "total_snapshots should be int"
        assert isinstance(dp["labeled"], int), "labeled should be int"
        assert isinstance(dp["pending_labels"], int), "pending_labels should be int"
        assert isinstance(dp["schema_v2_snapshots"], int), "schema_v2_snapshots should be int"
        assert isinstance(dp["outcome_distribution"], dict), "outcome_distribution should be dict"
        
        print(f"PASS: data_progress - total={dp['total_snapshots']}, labeled={dp['labeled']}, pending={dp['pending_labels']}")
    
    def test_stats_has_pattern_detection_counts(self):
        """Response should have pattern_detection_counts dict"""
        response = requests.get(f"{BASE_URL}/api/ml/stats")
        data = response.json()
        
        assert "pattern_detection_counts" in data, "Missing pattern_detection_counts field"
        patterns = data["pattern_detection_counts"]
        
        assert isinstance(patterns, dict), "pattern_detection_counts should be dict"
        
        # Check for expected pattern keys (without pattern_ prefix)
        expected_patterns = ["double_bottom", "bullish_engulfing", "bearish_engulfing", "bull_flag", 
                           "rsi_divergence", "macd_crossover", "volume_surge", "head_and_shoulders"]
        for pattern in expected_patterns:
            assert pattern in patterns, f"Missing pattern: {pattern}"
            assert isinstance(patterns[pattern], int), f"{pattern} count should be int"
        
        print(f"PASS: pattern_detection_counts has all 8 patterns")
    
    def test_stats_has_paper_trading(self):
        """Response should have paper_trading with open_trades, total_trades, total_pnl_usd"""
        response = requests.get(f"{BASE_URL}/api/ml/stats")
        data = response.json()
        
        assert "paper_trading" in data, "Missing paper_trading field"
        pt = data["paper_trading"]
        
        assert "open_trades" in pt, "paper_trading missing open_trades"
        assert "total_trades" in pt, "paper_trading missing total_trades"
        assert "total_pnl_usd" in pt, "paper_trading missing total_pnl_usd"
        
        assert isinstance(pt["open_trades"], int), "open_trades should be int"
        assert isinstance(pt["total_trades"], int), "total_trades should be int"
        assert isinstance(pt["total_pnl_usd"], (int, float)), "total_pnl_usd should be numeric"
        
        print(f"PASS: paper_trading - open={pt['open_trades']}, total={pt['total_trades']}, pnl=${pt['total_pnl_usd']}")
    
    def test_stats_has_live_execution(self):
        """Response should have live_execution with total_orders"""
        response = requests.get(f"{BASE_URL}/api/ml/stats")
        data = response.json()
        
        assert "live_execution" in data, "Missing live_execution field"
        le = data["live_execution"]
        
        assert "total_orders" in le, "live_execution missing total_orders"
        assert isinstance(le["total_orders"], int), "total_orders should be int"
        
        print(f"PASS: live_execution.total_orders = {le['total_orders']}")
    
    def test_stats_has_calibration(self):
        """Response should have calibration dict (may be empty if no model)"""
        response = requests.get(f"{BASE_URL}/api/ml/stats")
        data = response.json()
        
        assert "calibration" in data, "Missing calibration field"
        assert isinstance(data["calibration"], dict), "calibration should be dict"
        
        # If calibration has data, check structure
        cal = data["calibration"]
        if cal:
            expected_fields = ["accuracy", "brier_score", "ece", "n_predictions", "model_version"]
            for field in expected_fields:
                assert field in cal, f"calibration missing {field}"
        
        print(f"PASS: calibration present (has_data={bool(cal)})")
    
    def test_stats_has_milestones(self):
        """Response should have milestones dict with boolean flags"""
        response = requests.get(f"{BASE_URL}/api/ml/stats")
        data = response.json()
        
        assert "milestones" in data, "Missing milestones field"
        milestones = data["milestones"]
        
        expected_milestones = ["first_train_ready", "first_backtest_ready", "tier1_prediction_count"]
        for m in expected_milestones:
            assert m in milestones, f"milestones missing {m}"
            assert isinstance(milestones[m], bool), f"{m} should be bool"
        
        print(f"PASS: milestones - train_ready={milestones['first_train_ready']}, backtest_ready={milestones['first_backtest_ready']}")


class TestSignalEndpoint:
    """Tests for GET /api/signal/{ticker} graceful no_model handling"""
    
    def test_signal_aapl_returns_no_model_gracefully(self):
        """Signal endpoint should return no_model status gracefully when no trained model exists"""
        response = requests.get(f"{BASE_URL}/api/signal/AAPL")
        
        # Should return 200 with no_model status, not 500
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "status" in data, "Missing status field"
        assert data["status"] == "no_model", f"Expected status='no_model', got {data['status']}"
        assert "message" in data, "Missing message field"
        
        print(f"PASS: /api/signal/AAPL returns no_model status gracefully - message: {data['message'][:50]}...")
    
    def test_signal_tsla_returns_no_model_gracefully(self):
        """Signal endpoint for TSLA should also return no_model gracefully"""
        response = requests.get(f"{BASE_URL}/api/signal/TSLA")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert data.get("status") == "no_model", f"Expected no_model status"
        
        print("PASS: /api/signal/TSLA returns no_model status gracefully")


class TestBackendHealth:
    """Tests to verify backend is running correctly"""
    
    def test_backend_health(self):
        """Backend health check via stocks/ticker endpoint"""
        response = requests.get(f"{BASE_URL}/api/stocks/ticker")
        assert response.status_code == 200, f"Health check failed: {response.status_code}"
        print("PASS: Backend health check OK")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
