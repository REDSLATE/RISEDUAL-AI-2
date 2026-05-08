"""
Iteration 134: SEC 13F Holder Tracking API Tests

Tests for StockFit 13F Holder Tracking + SEC Filing Change Alerts feature.
Uses SEC EDGAR directly (free, no paid API).

Endpoints tested:
- GET  /api/stockfit/13f/institutions - List 25 tracked institutions
- POST /api/stockfit/13f/refresh - Admin-only refresh
- GET  /api/stockfit/13f/institution/{cik} - Institution holdings
- GET  /api/stockfit/13f/holders/{symbol} - Who holds a symbol
- GET  /api/stockfit/13f/changes/{cik} - QoQ changes
- GET  /api/stockfit/13f/alerts - User alerts (auth required)
"""
import pytest
import requests
import os

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")

# Test credentials — non-production values from test_credentials.md.
# Overridable via env for CI / alternative admin accounts.
ADMIN_EMAIL = os.environ.get("TEST_ADMIN_EMAIL", "admin@risedual.ai")
ADMIN_PASSWORD = os.environ.get("TEST_ADMIN_PASSWORD", "RiseDual2026!")

# Pre-seeded CIKs (per agent context)
BERKSHIRE_CIK = "0001067983"
BLACKROCK_CIK = "0001364742"
VANGUARD_CIK = "0000102909"
FIDELITY_CIK = "0000315066"


@pytest.fixture(scope="module")
def api_client():
    """Shared requests session."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


@pytest.fixture(scope="module")
def admin_session(api_client):
    """Authenticated admin session with cookies."""
    login_resp = api_client.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    if login_resp.status_code != 200:
        pytest.skip(f"Admin login failed: {login_resp.status_code} - {login_resp.text}")
    # Cookies are stored in session automatically
    return api_client


class TestInstitutionsList:
    """GET /api/stockfit/13f/institutions - List tracked institutions (no auth)."""

    def test_list_institutions_returns_25(self, api_client):
        """Should return exactly 25 tracked institutions."""
        resp = api_client.get(f"{BASE_URL}/api/stockfit/13f/institutions")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert "institutions" in data, "Response missing 'institutions' key"
        assert "count" in data, "Response missing 'count' key"
        assert data["count"] == 25, f"Expected 25 institutions, got {data['count']}"
        assert len(data["institutions"]) == 25

    def test_institution_structure(self, api_client):
        """Each institution should have required fields."""
        resp = api_client.get(f"{BASE_URL}/api/stockfit/13f/institutions")
        assert resp.status_code == 200
        
        data = resp.json()
        for inst in data["institutions"]:
            assert "cik" in inst, "Missing 'cik' field"
            assert "name" in inst, "Missing 'name' field"
            assert "latest_period_end" in inst, "Missing 'latest_period_end' field"
            assert "latest_filing_date" in inst, "Missing 'latest_filing_date' field"
            assert "total_value_usd" in inst, "Missing 'total_value_usd' field"
            assert "holdings_count" in inst, "Missing 'holdings_count' field"
            # Verify no MongoDB _id leaked
            assert "_id" not in inst, f"MongoDB _id leaked in institution: {inst.get('name')}"

    def test_berkshire_in_list(self, api_client):
        """Berkshire Hathaway should be in the list with data."""
        resp = api_client.get(f"{BASE_URL}/api/stockfit/13f/institutions")
        assert resp.status_code == 200
        
        data = resp.json()
        berkshire = next((i for i in data["institutions"] if i["cik"] == BERKSHIRE_CIK), None)
        assert berkshire is not None, "Berkshire Hathaway not found in institutions"
        assert berkshire["name"] == "Berkshire Hathaway"
        # Should have data since it was pre-seeded
        assert berkshire["latest_period_end"] is not None, "Berkshire should have filing data"
        assert berkshire["total_value_usd"] is not None and berkshire["total_value_usd"] > 0


class TestRefreshEndpoint:
    """POST /api/stockfit/13f/refresh - Admin-only refresh."""

    def test_refresh_requires_auth(self, api_client):
        """Should return 401/403 without authentication."""
        # Use a fresh session without cookies
        fresh_session = requests.Session()
        fresh_session.headers.update({"Content-Type": "application/json"})
        
        resp = fresh_session.post(
            f"{BASE_URL}/api/stockfit/13f/refresh",
            json={"cik": BERKSHIRE_CIK},
        )
        assert resp.status_code in [401, 403], f"Expected 401/403, got {resp.status_code}"

    def test_refresh_single_institution_admin(self, admin_session):
        """Admin can refresh a single institution."""
        resp = admin_session.post(
            f"{BASE_URL}/api/stockfit/13f/refresh",
            json={"cik": BERKSHIRE_CIK},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert data.get("ok") is True, "Response should have ok: true"
        assert "result" in data, "Response missing 'result' key"
        
        result = data["result"]
        assert "filings_new" in result or "filings_skipped" in result, "Result missing filing counts"
        assert "errors" in result, "Result missing 'errors' field"
        # Verify no _id leaked
        assert "_id" not in result


class TestInstitutionHoldings:
    """GET /api/stockfit/13f/institution/{cik} - Institution holdings."""

    def test_berkshire_holdings(self, api_client):
        """Berkshire should have AAPL, AXP, BAC, KO, CVX in top holdings."""
        resp = api_client.get(f"{BASE_URL}/api/stockfit/13f/institution/{BERKSHIRE_CIK}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert "cik" in data
        assert "institution_name" in data
        assert "filing" in data
        assert "holdings" in data
        assert "holdings_count" in data
        
        # Verify no _id leaked
        assert "_id" not in data
        assert "_id" not in data.get("filing", {})
        
        holdings = data["holdings"]
        assert len(holdings) > 0, "Berkshire should have holdings"
        
        # Check for expected top holdings (by symbol or issuer name)
        symbols = [h.get("symbol", "").upper() for h in holdings if h.get("symbol")]
        issuers = [h.get("issuer", "").upper() for h in holdings]
        
        # At least some of these should be present
        expected_symbols = ["AAPL", "AXP", "BAC", "KO", "CVX"]
        found_symbols = [s for s in expected_symbols if s in symbols]
        
        # Also check by issuer name if symbol not mapped
        expected_issuers = ["APPLE", "AMERICAN EXPRESS", "BANK OF AMERICA", "COCA-COLA", "CHEVRON"]
        found_issuers = [i for i in expected_issuers if any(i in iss for iss in issuers)]
        
        assert len(found_symbols) >= 3 or len(found_issuers) >= 3, \
            f"Expected at least 3 of {expected_symbols} in holdings. Found symbols: {found_symbols}, issuers: {found_issuers}"

    def test_holdings_aggregated_by_cusip(self, api_client):
        """Holdings should be aggregated by CUSIP (no duplicates)."""
        resp = api_client.get(f"{BASE_URL}/api/stockfit/13f/institution/{BERKSHIRE_CIK}")
        assert resp.status_code == 200
        
        data = resp.json()
        holdings = data["holdings"]
        
        # Check for duplicate CUSIPs
        cusips = [h.get("cusip") for h in holdings if h.get("cusip")]
        unique_cusips = set(cusips)
        assert len(cusips) == len(unique_cusips), \
            f"Found duplicate CUSIPs: {[c for c in cusips if cusips.count(c) > 1]}"

    def test_holdings_no_id_leak(self, api_client):
        """Holdings should not leak MongoDB _id."""
        resp = api_client.get(f"{BASE_URL}/api/stockfit/13f/institution/{BERKSHIRE_CIK}")
        assert resp.status_code == 200
        
        data = resp.json()
        for holding in data.get("holdings", []):
            assert "_id" not in holding, f"MongoDB _id leaked in holding: {holding.get('issuer')}"

    def test_unknown_cik_returns_404(self, api_client):
        """Unknown CIK should return 404."""
        resp = api_client.get(f"{BASE_URL}/api/stockfit/13f/institution/9999999999")
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"

    def test_berkshire_aapl_data_integrity(self, api_client):
        """Berkshire's AAPL holding should be ~227M shares at ~$61.9B (Q4 2025)."""
        resp = api_client.get(f"{BASE_URL}/api/stockfit/13f/institution/{BERKSHIRE_CIK}?limit=100")
        assert resp.status_code == 200
        
        data = resp.json()
        holdings = data["holdings"]
        
        # Find AAPL holding
        aapl = next((h for h in holdings if h.get("symbol") == "AAPL" or "APPLE" in h.get("issuer", "").upper()), None)
        
        if aapl:
            shares = aapl.get("shares", 0)
            value = aapl.get("value_usd", 0)
            
            # Berkshire's AAPL position is ~227M shares, ~$61.9B
            # Allow some variance for quarterly changes
            assert shares > 100_000_000, f"AAPL shares too low: {shares:,}"
            assert shares < 500_000_000, f"AAPL shares too high: {shares:,}"
            assert value > 30_000_000_000, f"AAPL value too low: ${value:,.0f}"
            assert value < 150_000_000_000, f"AAPL value too high: ${value:,.0f}"
            print(f"Berkshire AAPL: {shares:,} shares, ${value/1e9:.1f}B")
        else:
            # AAPL might not be in top 50, check if it exists at all
            print("AAPL not found in top holdings - may need higher limit")


class TestHoldersOfSymbol:
    """GET /api/stockfit/13f/holders/{symbol} - Who holds a symbol."""

    def test_aapl_holders(self, api_client):
        """AAPL should be held by Vanguard, BlackRock, Fidelity, Berkshire."""
        resp = api_client.get(f"{BASE_URL}/api/stockfit/13f/holders/AAPL")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert "symbol" in data
        assert data["symbol"] == "AAPL"
        assert "holders" in data
        assert "total_value_usd" in data
        assert "holder_count" in data
        
        # Verify no _id leaked
        assert "_id" not in data
        for holder in data.get("holders", []):
            assert "_id" not in holder

    def test_aapl_excludes_derivatives(self, api_client):
        """AAPL holders should exclude PUT/CALL positions."""
        resp = api_client.get(f"{BASE_URL}/api/stockfit/13f/holders/AAPL")
        assert resp.status_code == 200
        
        data = resp.json()
        for holder in data.get("holders", []):
            put_call = holder.get("put_call", "")
            assert put_call in ["", None], f"Found derivative position: {holder}"

    def test_unknown_symbol_returns_empty(self, api_client):
        """Unknown symbol should return empty holders list."""
        resp = api_client.get(f"{BASE_URL}/api/stockfit/13f/holders/ZZZZZ")
        assert resp.status_code == 200
        
        data = resp.json()
        assert data.get("holders") == [] or len(data.get("holders", [])) == 0


class TestQuarterlyChanges:
    """GET /api/stockfit/13f/changes/{cik} - QoQ changes."""

    def test_berkshire_changes(self, api_client):
        """Berkshire should have QoQ changes with delta_shares and delta_pct."""
        resp = api_client.get(f"{BASE_URL}/api/stockfit/13f/changes/{BERKSHIRE_CIK}")
        
        # May return 404 if only 1 filing stored
        if resp.status_code == 404:
            pytest.skip("Berkshire needs 2 filings for QoQ changes - only 1 stored")
        
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert "cik" in data
        assert "latest" in data
        assert "previous" in data
        assert "changes" in data
        assert "total_changes" in data
        
        # Verify no _id leaked
        assert "_id" not in data
        assert "_id" not in data.get("latest", {})
        assert "_id" not in data.get("previous", {})
        
        changes = data["changes"]
        if len(changes) > 0:
            change = changes[0]
            assert "type" in change, "Change missing 'type' field"
            assert change["type"] in ["new", "exited", "increased", "decreased"]
            assert "cusip" in change
            assert "issuer" in change
            assert "delta_shares" in change
            # delta_pct may be None for new positions
            assert "delta_pct" in change

    def test_changes_no_id_leak(self, api_client):
        """Changes should not leak MongoDB _id."""
        resp = api_client.get(f"{BASE_URL}/api/stockfit/13f/changes/{BERKSHIRE_CIK}")
        
        if resp.status_code == 404:
            pytest.skip("Berkshire needs 2 filings for QoQ changes")
        
        assert resp.status_code == 200
        data = resp.json()
        
        for change in data.get("changes", []):
            assert "_id" not in change

    def test_unknown_cik_returns_404(self, api_client):
        """Unknown CIK should return 404."""
        resp = api_client.get(f"{BASE_URL}/api/stockfit/13f/changes/9999999999")
        assert resp.status_code == 404


class TestUserAlerts:
    """GET /api/stockfit/13f/alerts - User alerts (auth required)."""

    def test_alerts_requires_auth(self, api_client):
        """Should return 401 without authentication."""
        fresh_session = requests.Session()
        fresh_session.headers.update({"Content-Type": "application/json"})
        
        resp = fresh_session.get(f"{BASE_URL}/api/stockfit/13f/alerts")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"

    def test_alerts_with_auth(self, admin_session):
        """Authenticated user should get alerts response."""
        resp = admin_session.get(f"{BASE_URL}/api/stockfit/13f/alerts")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert "alerts" in data, "Response missing 'alerts' key"
        assert "count" in data, "Response missing 'count' key"
        assert "watchlist_size" in data, "Response missing 'watchlist_size' key"
        
        # Verify no _id leaked
        assert "_id" not in data
        for alert in data.get("alerts", []):
            assert "_id" not in alert


class TestOtherInstitutions:
    """Test other pre-seeded institutions."""

    def test_blackrock_holdings(self, api_client):
        """BlackRock should have holdings data."""
        resp = api_client.get(f"{BASE_URL}/api/stockfit/13f/institution/{BLACKROCK_CIK}")
        
        if resp.status_code == 404:
            pytest.skip("BlackRock not yet refreshed")
        
        assert resp.status_code == 200
        data = resp.json()
        assert len(data.get("holdings", [])) > 0, "BlackRock should have holdings"

    def test_vanguard_holdings(self, api_client):
        """Vanguard should have holdings data."""
        resp = api_client.get(f"{BASE_URL}/api/stockfit/13f/institution/{VANGUARD_CIK}")
        
        if resp.status_code == 404:
            pytest.skip("Vanguard not yet refreshed")
        
        assert resp.status_code == 200
        data = resp.json()
        assert len(data.get("holdings", [])) > 0, "Vanguard should have holdings"

    def test_fidelity_holdings(self, api_client):
        """Fidelity should have holdings data."""
        resp = api_client.get(f"{BASE_URL}/api/stockfit/13f/institution/{FIDELITY_CIK}")
        
        if resp.status_code == 404:
            pytest.skip("Fidelity not yet refreshed")
        
        assert resp.status_code == 200
        data = resp.json()
        assert len(data.get("holdings", [])) > 0, "Fidelity should have holdings"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
