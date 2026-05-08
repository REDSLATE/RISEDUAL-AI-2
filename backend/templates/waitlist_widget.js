(function() {
  var API = '__API_URL__/api/waitlist';
  var SITE = '__API_URL__';
  
  function createWidget(containerId, opts) {
    opts = opts || {};
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
        '<div style="position:absolute;left:-9999px;" aria-hidden="true"><input type="text" id="rd-wl-hp" name="first_name_field" tabindex="-1" autocomplete="off" /></div>' +
        (ref ? '<input type="hidden" id="rd-wl-ref" value="' + ref + '" />' : '') +
        '<button type="submit" style="width:100%;padding:12px;background:linear-gradient(135deg,#14B8A6,#06B6D4);color:#fff;font-size:13px;font-weight:600;border:none;border-radius:10px;cursor:pointer;">Join the Waitlist</button>' +
      '</form>' +
      '<div id="rd-wl-result" style="display:none;text-align:center;"></div>' +
      '<div style="text-align:center;margin-top:12px;font-size:10px;color:#64748B;">Founding 100 get exclusive perks &middot; <a href="' + SITE + '" style="color:#3DE8D9;text-decoration:none;">risedual.ai</a></div>' +
    '</div>';
    
    document.getElementById('rd-wl-form').addEventListener('submit', function(e) {
      e.preventDefault();
      var email = document.getElementById('rd-wl-email').value;
      var name = document.getElementById('rd-wl-name').value;
      var refEl = document.getElementById('rd-wl-ref');
      var refCode = refEl ? refEl.value : '';
      var hpEl = document.getElementById('rd-wl-hp');
      var hp = hpEl ? hpEl.value : '';
      var btn = e.target.querySelector('button');
      btn.textContent = 'Joining...';
      btn.disabled = true;
      
      fetch(API + '/join', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({email: email, name: name, referral_code: refCode, first_name_field: hp})
      })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        document.getElementById('rd-wl-form').style.display = 'none';
        var res = document.getElementById('rd-wl-result');
        res.style.display = 'block';
        var link = SITE + '?ref=' + d.referral_code;
        res.innerHTML = '<div style="background:#0f172a;border:1px solid #334155;border-radius:12px;padding:16px;margin-bottom:12px;">' +
          '<div style="color:#3DE8D9;font-size:11px;font-weight:600;margin-bottom:8px;">&#10003; You\'re in!</div>' +
          '<div style="display:flex;justify-content:space-around;margin-bottom:12px;">' +
            '<div><div style="color:#fff;font-size:20px;font-weight:800;">#' + (d.rank || d.position) + '</div><div style="color:#64748B;font-size:9px;">RANK</div></div>' +
            '<div><div style="color:#a78bfa;font-size:20px;font-weight:800;">' + d.referral_count + '</div><div style="color:#64748B;font-size:9px;">REFERRALS</div></div>' +
            '<div><div style="color:#f97316;font-size:20px;font-weight:800;">' + d.priority_score + '</div><div style="color:#64748B;font-size:9px;">SCORE</div></div>' +
          '</div>' +
          '<div style="font-size:10px;color:#94A3B8;">Share to skip 20 spots per referral:</div>' +
          '<input readonly value="' + link + '" style="width:100%;box-sizing:border-box;padding:8px;background:#1E293B;border:1px solid #475569;border-radius:8px;color:#94A3B8;font-size:11px;margin-top:6px;outline:none;" onclick="this.select()" />' +
        '</div>';
      })
      .catch(function() {
        btn.textContent = 'Try Again';
        btn.disabled = false;
      });
    });
  }
  
  window.RiseDualWaitlist = { init: createWidget };
})();
