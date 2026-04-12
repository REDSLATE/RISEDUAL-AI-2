"""Waitlist API routes — public join/status + admin management."""
import os
import logging
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/waitlist", tags=["waitlist"])

APP_URL = os.environ.get('FRONTEND_URL', 'https://risedual.ai')


@router.post("/join")
async def join_waitlist(request: Request):
    """Join the beta waitlist. Public endpoint with honeypot bot protection."""
    body = await request.json()
    email = body.get("email", "").strip()
    name = body.get("name", "").strip()
    referred_by = body.get("referral_code", "").strip()
    honeypot = body.get("first_name_field", "").strip()

    # Honeypot: if this hidden field is filled, it's a bot
    if honeypot:
        logger.warning(f"Waitlist honeypot triggered: {email}")
        # Return fake success to not alert the bot
        return {"already_joined": False, "position": 999, "referral_code": "RDXXXXXX", "referral_count": 0, "priority_score": 999, "status": "waiting", "total_waitlist": 999}

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



@router.post("/admin/auto-invite")
async def admin_auto_invite(request: Request):
    """Admin: Manually trigger the daily auto-invite (top 5 by priority, with beta keys + emails)."""
    await _require_admin(request)
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    batch_size = body.get("batch_size", 5)
    if batch_size < 1 or batch_size > 20:
        raise HTTPException(status_code=400, detail="batch_size must be 1-20")

    from services.waitlist_service import auto_invite_top_users
    invited = await auto_invite_top_users(batch_size)
    return {"invited": invited, "count": len(invited)}


@router.get("/admin/analytics")
async def admin_analytics(request: Request, days: int = 30):
    """Admin: Waitlist analytics — daily signups, referral funnel, conversion rates, top referrers."""
    await _require_admin(request)
    if days < 1 or days > 365:
        days = 30
    from services.waitlist_service import get_waitlist_analytics
    return await get_waitlist_analytics(days)



# ── Embeddable Widget ──

_WIDGET_JS_CACHE = None

def _load_widget_template() -> str:
    """Load and cache the widget JS template from file."""
    global _WIDGET_JS_CACHE
    if _WIDGET_JS_CACHE is None:
        import pathlib
        template_path = pathlib.Path(__file__).parent.parent / "templates" / "waitlist_widget.js"
        _WIDGET_JS_CACHE = template_path.read_text()
    return _WIDGET_JS_CACHE


@router.get("/embed/widget.js")
async def embed_widget_js():
    """Serve the embeddable waitlist widget JavaScript."""
    js = _load_widget_template().replace("__API_URL__", APP_URL)
    return HTMLResponse(content=js, media_type="application/javascript")


@router.get("/embed/snippet")
async def embed_snippet():
    """Return the HTML embed code for the waitlist widget."""
    script_url = f"{APP_URL}/api/waitlist/embed/widget.js"
    snippet = f"""<!-- RISEDUAL AI Waitlist Widget -->
<div id="risedual-waitlist"></div>
<script src="{script_url}"></script>
<script>RiseDualWaitlist.init('risedual-waitlist');</script>"""

    return {
        "snippet": snippet,
        "script_url": script_url,
        "instructions": "Paste this HTML into any webpage to embed the RISEDUAL AI waitlist form. Add {ref: 'CODE'} as second argument to pre-fill a referral code.",
    }
