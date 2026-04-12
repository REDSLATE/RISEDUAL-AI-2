"""Waitlist API routes — public join/status + admin management."""
import logging
from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/waitlist", tags=["waitlist"])


@router.post("/join")
async def join_waitlist(request: Request):
    """Join the beta waitlist. Public endpoint."""
    body = await request.json()
    email = body.get("email", "").strip()
    name = body.get("name", "").strip()
    referred_by = body.get("referral_code", "").strip()

    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email is required")

    from services.waitlist_service import join_waitlist as _join
    result = await _join(email, name, referred_by)
    return result


@router.get("/status/{referral_code}")
async def get_status(referral_code: str):
    """Check waitlist position and referral stats. Public endpoint."""
    from services.waitlist_service import get_waitlist_status
    status = await get_waitlist_status(referral_code)
    if not status:
        raise HTTPException(status_code=404, detail="Referral code not found")
    return status


@router.get("/leaderboard")
async def leaderboard():
    """Public leaderboard of top referrers."""
    from services.waitlist_service import get_waitlist_leaderboard
    entries = await get_waitlist_leaderboard(limit=20)
    return {"leaderboard": entries}


@router.get("/stats")
async def stats():
    """Public waitlist stats (total count, etc.)."""
    from services.waitlist_service import get_waitlist_stats
    return await get_waitlist_stats()


# ── Admin Endpoints ──

async def _require_admin(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/admin/list")
async def admin_list(request: Request, skip: int = 0, limit: int = 50, sort_by: str = "priority"):
    """Admin: View waitlist entries sorted by priority or position."""
    await _require_admin(request)
    from services.waitlist_service import get_admin_waitlist
    return await get_admin_waitlist(skip, limit, sort_by)


@router.post("/admin/invite")
async def admin_invite(request: Request):
    """Admin: Invite the top N users from the waitlist."""
    await _require_admin(request)
    body = await request.json()
    count = body.get("count", 10)
    if count < 1 or count > 100:
        raise HTTPException(status_code=400, detail="Count must be 1-100")

    from services.waitlist_service import invite_users
    invited = await invite_users(count)
    return {"invited": invited, "count": len(invited)}


@router.post("/admin/select-founding")
async def admin_select_founding(request: Request):
    """Admin: Select the Founding 100 members."""
    await _require_admin(request)
    from services.waitlist_service import select_founding_100
    founders = await select_founding_100()
    return {"founders": founders, "count": len(founders)}
