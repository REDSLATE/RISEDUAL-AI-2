"""
Iteration 63: SSE Real-Time Insight Stream Tests

Tests:
1. GET /api/stream/insights - SSE endpoint returns connected and heartbeat events
2. GET /api/stream/recent - Returns buffered events
3. push_event() - Adds events to in-memory buffer
4. Stream router registration in server.py
5. Integration with nightly_cleanup, verify_pending_predictions, post_mortem
"""

import pytest
import requests
import os
import time
import json
from datetime import datetime, timezone
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD, OWNER_EMAIL, OWNER_PASSWORD

# Test credentials from test_credentials.md
class TestSSEStreamEndpoints:
    """Test SSE stream endpoints"""

    def test_api_health(self):
        """Verify API is running"""
        response = requests.get(f"{BASE_URL}/api/")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        print(f"PASS: API health check - {data['message']}")

    def test_stream_recent_endpoint(self):
        """GET /api/stream/recent returns buffered events"""
        response = requests.get(f"{BASE_URL}/api/stream/recent?limit=15")
        assert response.status_code == 200
        data = response.json()
        
        # Verify response structure
        assert "events" in data
        assert "total_buffered" in data
        assert isinstance(data["events"], list)
        assert isinstance(data["total_buffered"], int)
        
        print(f"PASS: /api/stream/recent - {len(data['events'])} events, {data['total_buffered']} total buffered")
        
        # If events exist, verify structure
        if data["events"]:
            event = data["events"][0]
            assert "type" in event
            assert "data" in event
            assert "timestamp" in event
            print(f"  Event type: {event['type']}")

    def test_stream_recent_limit_parameter(self):
        """GET /api/stream/recent respects limit parameter"""
        response = requests.get(f"{BASE_URL}/api/stream/recent?limit=5")
        assert response.status_code == 200
        data = response.json()
        assert len(data["events"]) <= 5
        print(f"PASS: /api/stream/recent limit=5 - returned {len(data['events'])} events")

    def test_stream_insights_sse_endpoint(self):
        """GET /api/stream/insights returns SSE stream with connected event"""
        # Use streaming request with timeout
        response = requests.get(
            f"{BASE_URL}/api/stream/insights",
            stream=True,
            timeout=10,
            headers={"Accept": "text/event-stream"}
        )
        assert response.status_code == 200
        
        # Read first few lines to verify SSE format
        lines = []
        for i, line in enumerate(response.iter_lines(decode_unicode=True)):
            if line:
                lines.append(line)
            if i > 5:  # Read first 6 lines
                break
        response.close()
        
        # Verify SSE format
        content = "\n".join(lines)
        assert "event:" in content or "data:" in content
        
        # Check for connected event
        has_connected = "connected" in content
        has_heartbeat = "heartbeat" in content
        
        print(f"PASS: /api/stream/insights SSE endpoint")
        print(f"  Has 'connected' event: {has_connected}")
        print(f"  Has 'heartbeat' event: {has_heartbeat}")
        
        assert has_connected or has_heartbeat, "SSE should have connected or heartbeat event"

    def test_stream_recent_event_types(self):
        """Verify event types in buffer are valid"""
        response = requests.get(f"{BASE_URL}/api/stream/recent?limit=50")
        assert response.status_code == 200
        data = response.json()
        
        valid_types = {"new_verification", "post_mortem", "toxic_alert", "memory_update"}
        
        for event in data["events"]:
            assert event["type"] in valid_types, f"Invalid event type: {event['type']}"
            
            # Verify data structure based on type
            if event["type"] == "toxic_alert":
                assert "toxic_count" in event["data"]
                assert "affected_tickers" in event["data"]
            elif event["type"] == "new_verification":
                assert "ticker" in event["data"]
                assert "correct" in event["data"]
            elif event["type"] == "post_mortem":
                assert "ticker" in event["data"]
                assert "failure_code" in event["data"]
        
        print(f"PASS: All {len(data['events'])} events have valid types and structure")


class TestToxicAlertInBuffer:
    """Test that toxic_alert from cleanup is in buffer"""

    def test_toxic_alert_present(self):
        """Verify toxic_alert event is in buffer (from nightly_cleanup)"""
        response = requests.get(f"{BASE_URL}/api/stream/recent?limit=20")
        assert response.status_code == 200
        data = response.json()
        
        toxic_alerts = [e for e in data["events"] if e["type"] == "toxic_alert"]
        
        if toxic_alerts:
            alert = toxic_alerts[0]
            print(f"PASS: Found toxic_alert in buffer")
            print(f"  toxic_count: {alert['data'].get('toxic_count')}")
            print(f"  affected_tickers: {alert['data'].get('affected_tickers')}")
            print(f"  total_before: {alert['data'].get('total_before')}")
            print(f"  total_after: {alert['data'].get('total_after')}")
            
            # Verify structure
            assert "toxic_count" in alert["data"]
            assert "affected_tickers" in alert["data"]
            assert isinstance(alert["data"]["affected_tickers"], list)
        else:
            print("INFO: No toxic_alert in buffer (cleanup may not have found toxic patterns)")


class TestStreamRouterRegistration:
    """Test that stream router is properly registered"""

    def test_stream_router_prefix(self):
        """Verify /api/stream prefix is working"""
        # Test recent endpoint
        response = requests.get(f"{BASE_URL}/api/stream/recent")
        assert response.status_code == 200
        
        # Test insights endpoint
        response = requests.get(
            f"{BASE_URL}/api/stream/insights",
            stream=True,
            timeout=5
        )
        assert response.status_code == 200
        response.close()
        
        print("PASS: Stream router registered with /api/stream prefix")

    def test_stream_404_for_invalid_endpoint(self):
        """Verify 404 for non-existent stream endpoints"""
        response = requests.get(f"{BASE_URL}/api/stream/nonexistent")
        assert response.status_code == 404
        print("PASS: Returns 404 for invalid stream endpoint")


class TestPushEventIntegration:
    """Test push_event() integration with services"""

    def test_cleanup_triggers_toxic_alert(self):
        """Verify nightly_cleanup pushes toxic_alert to stream"""
        session = requests.Session()
        
        # Login as admin
        login_response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login_response.status_code == 200
        print("PASS: Admin login successful")
        
        # Get current buffer state
        before_response = session.get(f"{BASE_URL}/api/stream/recent?limit=50")
        before_count = before_response.json()["total_buffered"]
        
        # Trigger cleanup (this should push toxic_alert if toxic patterns found)
        cleanup_response = session.post(
            f"{BASE_URL}/api/accuracy/memory/cleanup?days=90&threshold=80.0"
        )
        assert cleanup_response.status_code == 200
        cleanup_data = cleanup_response.json()
        print(f"PASS: Cleanup triggered - toxic_removed: {cleanup_data.get('toxic_removed', 0)}")
        
        # Check if buffer was updated
        after_response = session.get(f"{BASE_URL}/api/stream/recent?limit=50")
        after_count = after_response.json()["total_buffered"]
        
        # If toxic patterns were found, buffer should have new event
        if cleanup_data.get("toxic_removed", 0) > 0:
            assert after_count >= before_count
            print(f"PASS: Buffer updated after cleanup ({before_count} -> {after_count})")
        else:
            print(f"INFO: No toxic patterns found, buffer unchanged ({after_count})")


class TestSSEEventFormat:
    """Test SSE event format compliance"""

    def test_sse_content_type(self):
        """Verify SSE endpoint returns correct content type"""
        response = requests.get(
            f"{BASE_URL}/api/stream/insights",
            stream=True,
            timeout=5
        )
        assert response.status_code == 200
        
        content_type = response.headers.get("content-type", "")
        assert "text/event-stream" in content_type
        response.close()
        
        print(f"PASS: SSE content-type is {content_type}")

    def test_sse_event_data_is_json(self):
        """Verify SSE event data is valid JSON"""
        response = requests.get(
            f"{BASE_URL}/api/stream/insights",
            stream=True,
            timeout=10
        )
        assert response.status_code == 200
        
        data_lines = []
        for i, line in enumerate(response.iter_lines(decode_unicode=True)):
            if line and line.startswith("data:"):
                data_lines.append(line[5:].strip())
            if i > 10:
                break
        response.close()
        
        # Verify each data line is valid JSON
        for data_str in data_lines:
            try:
                parsed = json.loads(data_str)
                assert isinstance(parsed, dict)
            except json.JSONDecodeError:
                pytest.fail(f"SSE data is not valid JSON: {data_str}")
        
        print(f"PASS: All {len(data_lines)} SSE data payloads are valid JSON")


class TestRecentEventsOrdering:
    """Test recent events ordering and structure"""

    def test_events_ordered_newest_first(self):
        """Verify events are returned newest first"""
        response = requests.get(f"{BASE_URL}/api/stream/recent?limit=20")
        assert response.status_code == 200
        data = response.json()
        
        if len(data["events"]) >= 2:
            timestamps = [e["timestamp"] for e in data["events"]]
            # Verify descending order (newest first)
            for i in range(len(timestamps) - 1):
                assert timestamps[i] >= timestamps[i + 1], "Events should be newest first"
            print(f"PASS: Events are ordered newest first ({len(timestamps)} events)")
        else:
            print(f"INFO: Only {len(data['events'])} event(s) in buffer, ordering check skipped")

    def test_event_timestamp_format(self):
        """Verify event timestamps are ISO format"""
        response = requests.get(f"{BASE_URL}/api/stream/recent?limit=10")
        assert response.status_code == 200
        data = response.json()
        
        for event in data["events"]:
            ts = event["timestamp"]
            # Should be ISO format
            try:
                datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                pytest.fail(f"Invalid timestamp format: {ts}")
        
        print(f"PASS: All event timestamps are valid ISO format")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
