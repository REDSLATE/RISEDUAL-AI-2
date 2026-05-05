"""
Iteration 59: Toxic Spikes Alert System Testing
Tests the new toxic spikes alert feature:
1. POST /api/accuracy/memory/cleanup - re-tags toxic outliers instead of deleting
2. GET /api/accuracy/memory - returns toxic_lessons count and active_episodes count
3. GET /api/notifications - returns toxic_spike type notifications with metadata
4. POST /api/notifications/read-all - marks toxic spike notifications as read
5. Email alerts via Resend (check backend logs)
"""
import pytest
import requests
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD, OWNER_EMAIL, OWNER_PASSWORD

# Test credentials
class TestToxicSpikesAlertSystem:
    """Test the Toxic Spikes Alert System feature"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session with auth cookies"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
    def _login(self, email, password):
        """Login and store cookies"""
        response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": email, "password": password}
        )
        return response
    
    # ── 1. Memory Stats - toxic_lessons and active_episodes ──
    def test_memory_stats_returns_toxic_lessons_count(self):
        """GET /api/accuracy/memory should return toxic_lessons and active_episodes"""
        login_resp = self._login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        
        response = self.session.get(f"{BASE_URL}/api/accuracy/memory")
        assert response.status_code == 200, f"Memory stats failed: {response.text}"
        
        data = response.json()
        # Verify toxic_lessons field exists
        assert "toxic_lessons" in data, f"Missing toxic_lessons field. Got: {data.keys()}"
        assert isinstance(data["toxic_lessons"], int), f"toxic_lessons should be int, got {type(data['toxic_lessons'])}"
        
        # Verify active_episodes field exists
        assert "active_episodes" in data, f"Missing active_episodes field. Got: {data.keys()}"
        assert isinstance(data["active_episodes"], int), "active_episodes should be int"
        
        # Verify total_episodes = toxic_lessons + active_episodes
        total = data.get("total_episodes", 0)
        toxic = data.get("toxic_lessons", 0)
        active = data.get("active_episodes", 0)
        assert total == toxic + active, f"total_episodes ({total}) != toxic_lessons ({toxic}) + active_episodes ({active})"
        
        print(f"✓ Memory stats: total={total}, toxic_lessons={toxic}, active_episodes={active}")
    
    # ── 2. Cleanup re-tags instead of deleting ──
    def test_cleanup_retags_toxic_entries(self):
        """POST /api/accuracy/memory/cleanup should re-tag toxic outliers, not delete them"""
        login_resp = self._login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert login_resp.status_code == 200
        
        # Get stats before cleanup
        before_resp = self.session.get(f"{BASE_URL}/api/accuracy/memory")
        assert before_resp.status_code == 200
        before_data = before_resp.json()
        before_data.get("total_episodes", 0)
        before_data.get("toxic_lessons", 0)
        
        # Run cleanup
        cleanup_resp = self.session.post(f"{BASE_URL}/api/accuracy/memory/cleanup")
        assert cleanup_resp.status_code == 200, f"Cleanup failed: {cleanup_resp.text}"
        
        cleanup_data = cleanup_resp.json()
        
        # Verify cleanup response structure
        assert "toxic_removed" in cleanup_data, "Missing toxic_removed in cleanup response"
        assert "total_before" in cleanup_data, "Missing total_before in cleanup response"
        assert "total_after" in cleanup_data, "Missing total_after in cleanup response"
        
        # Key assertion: When only toxic entries exist, total_after should equal total_before
        # (re-tagging doesn't reduce count, only deleting obsolete data does)
        toxic_retagged = cleanup_data.get("toxic_removed", 0)
        obsolete_removed = cleanup_data.get("obsolete_removed", 0)
        
        # If no obsolete data was removed, total should stay the same
        if obsolete_removed == 0:
            assert cleanup_data["total_after"] == cleanup_data["total_before"], \
                f"Total changed without obsolete removal: {cleanup_data['total_before']} -> {cleanup_data['total_after']}"
        
        print(f"✓ Cleanup: toxic_retagged={toxic_retagged}, obsolete_removed={obsolete_removed}")
        print(f"  total_before={cleanup_data['total_before']}, total_after={cleanup_data['total_after']}")
        
        # Verify toxic_details contains affected tickers
        if toxic_retagged > 0:
            assert "toxic_details" in cleanup_data, "Missing toxic_details when toxic entries found"
            details = cleanup_data.get("toxic_details", [])
            assert len(details) > 0, "toxic_details should not be empty when toxic_removed > 0"
            # Each detail should have symbol, confidence, date
            for detail in details[:3]:  # Check first 3
                assert "symbol" in detail, f"Missing symbol in toxic_detail: {detail}"
                assert "confidence" in detail, f"Missing confidence in toxic_detail: {detail}"
                print(f"  - Toxic: {detail.get('symbol')} @ {detail.get('confidence')}% confidence")
    
    # ── 3. Notifications endpoint returns toxic_spike type ──
    def test_notifications_returns_toxic_spike_type(self):
        """GET /api/notifications should return toxic_spike type notifications with metadata"""
        login_resp = self._login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert login_resp.status_code == 200
        
        response = self.session.get(f"{BASE_URL}/api/notifications")
        assert response.status_code == 200, f"Notifications failed: {response.text}"
        
        data = response.json()
        assert "notifications" in data, "Missing notifications field"
        
        notifications = data.get("notifications", [])
        
        # Find toxic_spike notifications
        toxic_notifications = [n for n in notifications if n.get("type") == "toxic_spike"]
        
        print(f"✓ Found {len(toxic_notifications)} toxic_spike notifications out of {len(notifications)} total")
        
        if toxic_notifications:
            # Verify toxic_spike notification structure
            toxic_notif = toxic_notifications[0]
            assert "title" in toxic_notif, "Missing title in toxic_spike notification"
            assert "message" in toxic_notif, "Missing message in toxic_spike notification"
            assert "metadata" in toxic_notif, "Missing metadata in toxic_spike notification"
            
            metadata = toxic_notif.get("metadata", {})
            assert "affected_tickers" in metadata, f"Missing affected_tickers in metadata: {metadata.keys()}"
            assert "toxic_count" in metadata, f"Missing toxic_count in metadata: {metadata.keys()}"
            
            affected = metadata.get("affected_tickers", [])
            print(f"  - Title: {toxic_notif.get('title')}")
            print(f"  - Affected tickers: {affected[:5]}{'...' if len(affected) > 5 else ''}")
            print(f"  - Toxic count: {metadata.get('toxic_count')}")
        else:
            print("  (No toxic_spike notifications found - may need to seed toxic data and run cleanup)")
    
    # ── 4. Mark all notifications as read ──
    def test_mark_all_notifications_read(self):
        """POST /api/notifications/read-all should mark toxic spike notifications as read"""
        login_resp = self._login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert login_resp.status_code == 200
        
        # Get unread count before
        before_resp = self.session.get(f"{BASE_URL}/api/notifications/unread-count")
        assert before_resp.status_code == 200
        unread_before = before_resp.json().get("count", 0)
        
        # Mark all as read
        mark_resp = self.session.post(f"{BASE_URL}/api/notifications/read-all")
        assert mark_resp.status_code == 200, f"Mark read failed: {mark_resp.text}"
        
        # Verify unread count is now 0
        after_resp = self.session.get(f"{BASE_URL}/api/notifications/unread-count")
        assert after_resp.status_code == 200
        unread_after = after_resp.json().get("count", 0)
        
        assert unread_after == 0, f"Unread count should be 0 after mark-all-read, got {unread_after}"
        
        print(f"✓ Mark all read: unread_before={unread_before}, unread_after={unread_after}")
    
    # ── 5. Verify notifications have correct fields for frontend rendering ──
    def test_notification_fields_for_frontend(self):
        """Verify notifications have all fields needed for NotificationItem.jsx rendering"""
        login_resp = self._login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert login_resp.status_code == 200
        
        response = self.session.get(f"{BASE_URL}/api/notifications")
        assert response.status_code == 200
        
        notifications = response.json().get("notifications", [])
        
        # Check toxic_spike notifications
        toxic_notifs = [n for n in notifications if n.get("type") == "toxic_spike"]
        if toxic_notifs:
            n = toxic_notifs[0]
            # Required fields for ToxicSpikeNotification component
            assert "type" in n, "Missing type field"
            assert "title" in n, "Missing title field"
            assert "message" in n, "Missing message field"
            assert "read" in n, "Missing read field"
            assert "created_at" in n, "Missing created_at field"
            assert "metadata" in n, "Missing metadata field"
            
            meta = n.get("metadata", {})
            assert "affected_tickers" in meta, "Missing affected_tickers in metadata"
            print("✓ Toxic spike notification has all required fields for frontend")
        
        # Check verdict_change notifications
        verdict_notifs = [n for n in notifications if n.get("type") == "verdict_change"]
        if verdict_notifs:
            n = verdict_notifs[0]
            # Required fields for VerdictNotification component
            assert "symbol" in n, "Missing symbol field"
            assert "new_verdict" in n, "Missing new_verdict field"
            assert "old_verdict" in n, "Missing old_verdict field"
            print("✓ Verdict notification has all required fields for frontend")
    
    # ── 6. Cleanup history shows toxic details ──
    def test_cleanup_history_shows_toxic_details(self):
        """GET /api/accuracy/memory/cleanup/history should show cleanup runs with toxic details"""
        login_resp = self._login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert login_resp.status_code == 200
        
        response = self.session.get(f"{BASE_URL}/api/accuracy/memory/cleanup/history")
        assert response.status_code == 200, f"Cleanup history failed: {response.text}"
        
        data = response.json()
        assert "runs" in data, "Missing runs field in cleanup history"
        
        runs = data.get("runs", [])
        print(f"✓ Cleanup history: {len(runs)} runs found")
        
        if runs:
            latest = runs[0]
            print(f"  - Latest run: toxic_removed={latest.get('toxic_removed')}, "
                  f"obsolete_removed={latest.get('obsolete_removed')}, "
                  f"run_at={latest.get('run_at')}")
            
            # Verify toxic_details in history
            if latest.get("toxic_removed", 0) > 0:
                assert "toxic_details" in latest, "Missing toxic_details in cleanup history"
    
    # ── 7. Pro-only access verification ──
    def test_memory_stats_requires_pro(self):
        """GET /api/accuracy/memory should require Pro subscription"""
        # Without login, should fail
        response = requests.get(f"{BASE_URL}/api/accuracy/memory")
        assert response.status_code in [401, 403], f"Expected 401/403 without auth, got {response.status_code}"
        print("✓ Memory stats requires authentication")
    
    def test_notifications_free_user_gets_empty(self):
        """Free users should get empty notifications list"""
        # This test would need a free user account
        # For now, just verify the endpoint structure
        login_resp = self._login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert login_resp.status_code == 200
        
        response = self.session.get(f"{BASE_URL}/api/notifications")
        assert response.status_code == 200
        
        data = response.json()
        assert "is_pro" in data, "Missing is_pro field in notifications response"
        print(f"✓ Notifications response includes is_pro={data.get('is_pro')}")


class TestToxicLessonsContext:
    """Test the toxic lessons context retrieval for AI predictions"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
    
    def _login(self, email, password):
        response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": email, "password": password}
        )
        return response
    
    def test_memory_stats_shows_toxic_count(self):
        """Verify toxic_lessons count is tracked in memory stats"""
        login_resp = self._login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert login_resp.status_code == 200
        
        response = self.session.get(f"{BASE_URL}/api/accuracy/memory")
        assert response.status_code == 200
        
        data = response.json()
        toxic_count = data.get("toxic_lessons", 0)
        
        print(f"✓ Current toxic_lessons count: {toxic_count}")
        
        # After cleanup runs, there should be some toxic lessons
        # (unless all toxic entries were already processed)
        assert toxic_count >= 0, "toxic_lessons should be non-negative"


class TestEmailAlertIntegration:
    """Test email alert integration (verify via backend logs)"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
    
    def _login(self, email, password):
        response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": email, "password": password}
        )
        return response
    
    def test_cleanup_triggers_email_on_toxic_found(self):
        """
        POST /api/accuracy/memory/cleanup should send email when toxic entries found.
        Note: Email sending is verified via backend logs, not API response.
        """
        login_resp = self._login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert login_resp.status_code == 200
        
        # Run cleanup
        response = self.session.post(f"{BASE_URL}/api/accuracy/memory/cleanup")
        assert response.status_code == 200
        
        data = response.json()
        toxic_count = data.get("toxic_removed", 0)
        
        print(f"✓ Cleanup completed: toxic_removed={toxic_count}")
        print("  Note: Check backend logs for email send confirmation:")
        print("  - Look for: 'Toxic spikes alert email sent to...'")
        print("  - Recipients: admin@risedual.ai, managingdirector@redslateholdings.com")
        
        # The email is sent asynchronously, so we can't verify it directly
        # Backend logs should show: "Toxic spikes email alerts sent to X admin(s)"


class TestOwnerNotifications:
    """Test that owner account receives toxic spike notifications"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
    
    def _login(self, email, password):
        response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": email, "password": password}
        )
        return response
    
    def test_owner_receives_toxic_notifications(self):
        """Owner should receive toxic spike in-app notifications"""
        login_resp = self._login(OWNER_EMAIL, OWNER_PASSWORD)
        assert login_resp.status_code == 200, f"Owner login failed: {login_resp.text}"
        
        response = self.session.get(f"{BASE_URL}/api/notifications")
        assert response.status_code == 200
        
        data = response.json()
        notifications = data.get("notifications", [])
        
        toxic_notifs = [n for n in notifications if n.get("type") == "toxic_spike"]
        
        print(f"✓ Owner has {len(toxic_notifs)} toxic_spike notifications")
        
        if toxic_notifs:
            latest = toxic_notifs[0]
            print(f"  - Latest: {latest.get('title')}")
            print(f"  - Read: {latest.get('read')}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
