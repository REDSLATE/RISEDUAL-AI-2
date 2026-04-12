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



# ── Embeddable Widget ──

@router.get("/embed/widget.js")
async def embed_widget_js():
    """Serve the embeddable waitlist widget JavaScript."""
    js = f"""
(function() {{
  var API = '{APP_URL}/api/waitlist';
  var SITE = '{APP_URL}';
  
  function createWidget(containerId, opts) {{
    opts = opts || {{}};
    var ref = opts.ref || '';
    var el = document.getElementById(containerId);
    if (!el) return console.error('RiseDual Waitlist: Container not found: ' + containerId);
    
    el.innerHTML = '<div id="rd-wl-inner" style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:400px;background:#0B1426;border:1px solid #334155;border-radius:16px;padding:24px;color:#fff;">' +
      '<div style="display:flex;align-items:center;gap:8px;margin-bottom:16px;">' +
        '<div style="width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,#14B8A6,#06B6D4);display:flex;align-items:center;justify-content:center;">' +
          '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>' +
        '</div>' +
        '<div><div style="font-size:14px;font-weight:700;">RISEDUAL AI</div><div style="font-size:10px;color:#94A3B8;">Join the Founding 100</div></div>' +
      '</div>' +
      '<form id="rd-wl-form">' +
        '<input id="rd-wl-email" type="email" placeholder="your@email.com" required style="width:100%;box-sizing:border-box;padding:10px 14px;background:#1E293B;border:1px solid #475569;border-radius:10px;color:#fff;font-size:13px;margin-bottom:8px;outline:none;" />' +
        '<input id="rd-wl-name" type="text" placeholder="Your name (optional)" style="width:100%;box-sizing:border-box;padding:10px 14px;background:#1E293B;border:1px solid #475569;border-radius:10px;color:#fff;font-size:13px;margin-bottom:12px;outline:none;" />' +
        (ref ? '<input type="hidden" id="rd-wl-ref" value="' + ref + '" />' : '') +
        '<button type="submit" style="width:100%;padding:12px;background:linear-gradient(135deg,#14B8A6,#06B6D4);color:#fff;font-size:13px;font-weight:600;border:none;border-radius:10px;cursor:pointer;">Join the Waitlist</button>' +
      '</form>' +
      '<div id="rd-wl-result" style="display:none;text-align:center;"></div>' +
      '<div style="text-align:center;margin-top:12px;font-size:10px;color:#64748B;">Founding 100 get exclusive perks &middot; <a href="' + SITE + '" style="color:#3DE8D9;text-decoration:none;">risedual.ai</a></div>' +
    '</div>';
    
    document.getElementById('rd-wl-form').addEventListener('submit', function(e) {{
      e.preventDefault();
      var email = document.getElementById('rd-wl-email').value;
      var name = document.getElementById('rd-wl-name').value;
      var refEl = document.getElementById('rd-wl-ref');
      var refCode = refEl ? refEl.value : '';
      var btn = e.target.querySelector('button');
      btn.textContent = 'Joining...';
      btn.disabled = true;
      
      fetch(API + '/join', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{email: email, name: name, referral_code: refCode}})
      }})
      .then(function(r) {{ return r.json(); }})
      .then(function(d) {{
        document.getElementById('rd-wl-form').style.display = 'none';
        var res = document.getElementById('rd-wl-result');
        res.style.display = 'block';
        var link = SITE + '?ref=' + d.referral_code;
        res.innerHTML = '<div style="background:#0f172a;border:1px solid #334155;border-radius:12px;padding:16px;margin-bottom:12px;">' +
          '<div style="color:#3DE8D9;font-size:11px;font-weight:600;margin-bottom:8px;">&#10003; You\\\'re in!</div>' +
          '<div style="display:flex;justify-content:space-around;margin-bottom:12px;">' +
            '<div><div style="color:#fff;font-size:20px;font-weight:800;">#' + (d.rank || d.position) + '</div><div style="color:#64748B;font-size:9px;">RANK</div></div>' +
            '<div><div style="color:#a78bfa;font-size:20px;font-weight:800;">' + d.referral_count + '</div><div style="color:#64748B;font-size:9px;">REFERRALS</div></div>' +
            '<div><div style="color:#f97316;font-size:20px;font-weight:800;">' + d.priority_score + '</div><div style="color:#64748B;font-size:9px;">SCORE</div></div>' +
          '</div>' +
          '<div style="font-size:10px;color:#94A3B8;">Share to skip 20 spots per referral:</div>' +
          '<input readonly value="' + link + '" style="width:100%;box-sizing:border-box;padding:8px;background:#1E293B;border:1px solid #475569;border-radius:8px;color:#94A3B8;font-size:11px;margin-top:6px;outline:none;" onclick="this.select()" />' +
        '</div>';
      }})
      .catch(function() {{
        btn.textContent = 'Try Again';
        btn.disabled = false;
      }});
    }});
  }}
  
  window.RiseDualWaitlist = {{ init: createWidget }};
}})();
"""
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
