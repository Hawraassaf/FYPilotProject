/* FYPilot — Razor Pages Frontend */
'use strict';

var FYPilot = window.FYPilot || {};

// ── SystemTest ────────────────────────────────────────────────────────────────
FYPilot.SystemTest = {
    setStatus: function (id, status, detail) {
        var card = document.getElementById('test-' + id);
        if (!card) return;
        var dot    = card.querySelector('.status-dot');
        var badge  = card.querySelector('.test-badge');
        var detDiv = card.querySelector('.test-detail');
        var map = {
            'ok':      { dot:'bg-success',   badge:'bg-success',              text:'PASS'    },
            'error':   { dot:'bg-danger',    badge:'bg-danger',               text:'FAIL'    },
            'running': { dot:'bg-warning',   badge:'bg-warning text-dark',    text:'RUNNING' },
            'pending': { dot:'bg-secondary', badge:'bg-secondary',             text:'PENDING' },
        };
        var cfg = map[status] || map['pending'];
        dot.className     = 'status-dot ' + cfg.dot;
        badge.className   = 'test-badge badge ' + cfg.badge;
        badge.textContent = cfg.text;
        if (detail) { detDiv.textContent = detail; detDiv.style.display = 'block'; }
    },
    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("[data-auto-dismiss]").forEach(function (alertBox) {
            const delay = parseInt(alertBox.getAttribute("data-auto-dismiss") || "2000", 10);

            setTimeout(function () {
                alertBox.style.transition = "opacity 0.35s ease, transform 0.35s ease";
                alertBox.style.opacity = "0";
                alertBox.style.transform = "translateY(-6px)";

                setTimeout(function () {
                    alertBox.remove();
                }, 350);
            }, delay);
        });
    });

    run: async function () {
        var btn = document.getElementById('run-btn');
        if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Running…'; }
        ['database','ai-health','skill-analysis','feasibility','similarity'].forEach(function(id){ FYPilot.SystemTest.setStatus(id,'running',''); });

        var token   = document.querySelector('[name="__RequestVerificationToken"]');
        var headers = { 'Content-Type':'application/json' };
        if (token) headers['RequestVerificationToken'] = token.value;

        try {
            var resp = await fetch(location.pathname + '?handler=RunAll', { method:'POST', headers: headers });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            var results = await resp.json();
            results.forEach(function(r){ FYPilot.SystemTest.setStatus(r.id, r.status, r.detail); });
            var passed = results.filter(function(r){ return r.status === 'ok'; }).length;
            var summary = document.getElementById('test-summary');
            summary.style.display = 'block';
            summary.className = 'alert mt-3 ' + (passed === results.length ? 'alert-success' : 'alert-warning');
            summary.innerHTML = '<strong>' + passed + '/' + results.length + ' tests passed</strong>' + (passed < results.length ? ' — see failures above.' : ' — All systems operational!');
        } catch(err) {
            ['database','ai-health','skill-analysis','feasibility','similarity'].forEach(function(id){ FYPilot.SystemTest.setStatus(id,'error',err.message); });
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-play-circle me-2"></i>Run Tests'; }
        }
    }
};

document.addEventListener('DOMContentLoaded', function(){
    document.querySelectorAll('.alert-success.auto-dismiss').forEach(function(el){
        setTimeout(function(){ el.style.transition='opacity 0.5s'; el.style.opacity='0'; setTimeout(function(){ el.remove(); }, 500); }, 4000);
    });
});

window.FYPilot = FYPilot;
