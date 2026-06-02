"""
Terminal UI served at /ui — matrix-aesthetic browser interface over the same
FastAPI backend that Clay uses. Real-time via Server-Sent Events.

Clay keeps hitting POST /v1/audit as normal. This is additive.
"""

from __future__ import annotations

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GTM Intelligence Engine</title>
<style>
  :root {
    --green:   #00ff41;
    --green2:  #00cc33;
    --dim:     #005a14;
    --dimmer:  #003009;
    --red:     #ff3333;
    --orange:  #ff9900;
    --yellow:  #ffee00;
    --bg:      #080c08;
    --panel:   #090e09;
    --border:  #003a0a;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--green);
    font-family: 'Courier New', Courier, monospace;
    font-size: 13px;
    line-height: 1.55;
    min-height: 100vh;
    padding: 0;
    overflow-x: hidden;
  }

  /* subtle scanlines */
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background: repeating-linear-gradient(
      0deg,
      transparent,
      transparent 2px,
      rgba(0,0,0,.15) 2px,
      rgba(0,0,0,.15) 4px
    );
    pointer-events: none;
    z-index: 9999;
  }

  #wrap {
    max-width: 900px;
    margin: 0 auto;
    padding: 24px 20px 60px;
  }

  /* header */
  #hdr {
    border-bottom: 1px solid var(--border);
    padding-bottom: 14px;
    margin-bottom: 22px;
    display: flex;
    align-items: baseline;
    gap: 16px;
  }
  #hdr h1 {
    font-size: 11px;
    letter-spacing: 4px;
    text-transform: uppercase;
    color: var(--green2);
    font-weight: normal;
  }
  #hdr .ver { color: var(--dim); font-size: 11px; }
  #timer { margin-left: auto; color: var(--dim); font-size: 11px; }

  /* form */
  #form-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 28px;
    flex-wrap: wrap;
  }
  .prompt { color: var(--dim); user-select: none; }
  #domain {
    background: transparent;
    border: none;
    border-bottom: 1px solid var(--green2);
    color: var(--green);
    font-family: inherit;
    font-size: 14px;
    padding: 4px 6px;
    width: 260px;
    outline: none;
    caret-color: var(--green);
  }
  #domain::placeholder { color: var(--dim); }
  #geo {
    background: var(--panel);
    border: 1px solid var(--border);
    color: var(--green2);
    font-family: inherit;
    font-size: 12px;
    padding: 4px 6px;
    cursor: pointer;
    outline: none;
  }
  #geo option { background: var(--bg); }
  #scanBtn {
    background: transparent;
    border: 1px solid var(--green2);
    color: var(--green);
    font-family: inherit;
    font-size: 12px;
    letter-spacing: 2px;
    padding: 5px 18px;
    cursor: pointer;
    transition: background .15s, color .15s;
  }
  #scanBtn:hover { background: var(--green); color: var(--bg); }
  #scanBtn:disabled { opacity: .4; cursor: not-allowed; }

  /* sections */
  .section {
    margin-bottom: 20px;
  }
  .section-label {
    color: var(--dim);
    font-size: 10px;
    letter-spacing: 3px;
    text-transform: uppercase;
    border-top: 1px solid var(--border);
    padding-top: 10px;
    margin-bottom: 12px;
  }

  /* collector rows */
  #collectors { font-size: 12px; }
  .coll-row {
    display: grid;
    grid-template-columns: 160px 50px 1fr;
    gap: 0 12px;
    padding: 3px 0;
    align-items: baseline;
    opacity: .35;
    transition: opacity .3s;
  }
  .coll-row.done { opacity: 1; }
  .coll-row.running { opacity: .7; }
  .coll-name { color: var(--green2); }
  .coll-time { color: var(--dim); text-align: right; font-size: 11px; }
  .coll-summary { color: var(--green); }
  .spinner { display: inline-block; animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* signals */
  #signals { }
  .sig-meta {
    color: var(--green2);
    margin-bottom: 14px;
    font-size: 12px;
  }
  .sig-row { display: grid; grid-template-columns: 90px 220px 1fr; gap: 0 10px; margin-bottom: 6px; align-items: start; }
  .sev { font-size: 11px; font-weight: bold; padding: 1px 6px; display: inline-block; }
  .sev.CRITICAL { color: var(--red); border: 1px solid var(--red); }
  .sev.HIGH     { color: var(--orange); border: 1px solid var(--orange); }
  .sev.MEDIUM   { color: var(--yellow); border: 1px solid var(--yellow); }
  .sev.LOW      { color: var(--dim); border: 1px solid var(--dimmer); }
  .sig-id   { color: var(--green); font-size: 12px; }
  .sig-trig { color: var(--dim); font-size: 11px; font-style: italic; }

  /* outbound */
  #outbound .subject {
    color: var(--green2);
    font-size: 13px;
    margin-bottom: 16px;
  }
  #outbound .subject span { color: var(--green); font-size: 15px; }
  .hook-box, .followup-box {
    background: var(--panel);
    border: 1px solid var(--border);
    padding: 14px 16px;
    margin-bottom: 12px;
    color: var(--green);
    font-size: 13px;
    line-height: 1.65;
  }
  .hook-box { border-color: var(--green2); }
  .box-label {
    font-size: 10px;
    letter-spacing: 2px;
    color: var(--dim);
    text-transform: uppercase;
    margin-bottom: 8px;
  }

  /* footer */
  #footer-bar {
    margin-top: 28px;
    border-top: 1px solid var(--border);
    padding-top: 10px;
    color: var(--dim);
    font-size: 11px;
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
  }
  .tag { padding: 1px 8px; border: 1px solid var(--dimmer); border-radius: 2px; }
  .tag.dummy   { color: #aa8800; border-color: #443300; }
  .tag.skipped { color: #882222; border-color: #330a0a; }
  .tag.real    { color: var(--green2); border-color: var(--border); }

  /* status line */
  #status {
    color: var(--dim);
    font-size: 11px;
    min-height: 18px;
    margin-bottom: 6px;
  }
  #status .blink { animation: blink 1s step-end infinite; }
  @keyframes blink { 50% { opacity: 0; } }

  /* error */
  .error-msg { color: var(--red); font-size: 12px; padding: 12px 0; }

  /* copy button */
  .copy-btn {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--dim);
    font-family: inherit;
    font-size: 10px;
    letter-spacing: 1px;
    padding: 2px 8px;
    cursor: pointer;
    float: right;
    margin-top: -2px;
    transition: color .15s, border-color .15s;
  }
  .copy-btn:hover { color: var(--green); border-color: var(--green2); }

  .hidden { display: none; }
</style>
</head>
<body>
<div id="wrap">

  <div id="hdr">
    <h1>GTM Intelligence Engine</h1>
    <span class="ver">v1.0.0</span>
    <span id="timer" class="hidden">0s</span>
  </div>

  <form id="scanForm">
    <div id="form-row">
      <span class="prompt">&gt;</span>
      <input id="domain" type="text" placeholder="developer.co.uk" autocomplete="off" spellcheck="false">
      <select id="geo">
        <option value="uk">UK</option>
        <option value="se">SE</option>
      </select>
      <button id="scanBtn" type="submit">SCAN</button>
    </div>
  </form>

  <div id="status"></div>

  <div id="output" class="hidden">

    <div class="section">
      <div class="section-label">collectors</div>
      <div id="collectors"></div>
    </div>

    <div id="sig-section" class="section hidden">
      <div class="section-label">intelligence</div>
      <div id="sig-meta" class="sig-meta"></div>
      <div id="signals"></div>
    </div>

    <div id="out-section" class="section hidden">
      <div class="section-label">outbound copy</div>
      <div id="outbound"></div>
    </div>

    <div id="footer-bar"></div>

  </div>

</div>

<script>
const COLLECTORS = [
  'dns_headers', 'ad_intelligence', 'planning_intel',
  'social_review', 'site_scanner', 'portal_quality'
];
const LABELS = {
  dns_headers:    'dns / infrastructure',
  ad_intelligence:'ad intelligence',
  planning_intel: 'planning intel',
  social_review:  'reviews',
  site_scanner:   'site scanner',
  portal_quality: 'portal quality',
};

let timerInterval = null;
let startTime = null;

function fmt(html) {
  // Convert basic Rich-style tags to styled spans (not needed here — we format in JS)
  return html;
}

function collectorSummary(id, data, source) {
  const parts = [];
  if (source === 'dummy')   parts.push('<span style="color:var(--dim)">~dummy</span>');
  if (source === 'skipped') parts.push('<span style="color:var(--red)">skipped</span>');

  if (id === 'dns_headers') {
    if (data.has_ssl)   parts.push('<span style="color:var(--green2)">SSL ✓</span>');
    if (data.has_spf)   parts.push('<span style="color:var(--green2)">SPF ✓</span>');
    if (data.has_dmarc) parts.push('<span style="color:var(--green2)">DMARC ✓</span>');
    if (data.cdn_provider) parts.push('CDN:' + data.cdn_provider);
    if (data.email_provider && data.email_provider !== 'Unknown')
      parts.push(data.email_provider);
  }

  else if (id === 'ad_intelligence' && source !== 'dummy' && source !== 'skipped') {
    const age = data.creative_age_days;
    if (age) {
      const col = age > 45 ? 'var(--red)' : age > 30 ? 'var(--orange)' : 'var(--green)';
      parts.push(`age:<span style="color:${col};font-weight:bold">${age}d</span>`);
    }
    parts.push(`fatigue:${data.ad_fatigue_score}`);
    parts.push(`cta:${data.primary_cta_type}`);
    parts.push(`spend:${data.spend_tier}`);
  } else if (id === 'ad_intelligence' && source === 'dummy') {
    const age = data.creative_age_days;
    if (age) parts.push(`age:${age}d`);
    parts.push(`fatigue:${data.ad_fatigue_score}`);
    parts.push(`cta:${data.primary_cta_type}`);
  }

  else if (id === 'site_scanner') {
    const load = data.load_time_ms;
    const mob  = data.mobile_score;
    const crm  = data.tech_stack && data.tech_stack.crm;
    const px   = data.tech_stack && data.tech_stack.has_facebook_pixel;
    const res  = data.has_digital_reservation;
    const tp   = data.trustpilot_business_id;
    if (load) {
      const col = load > 3000 ? 'var(--red)' : load > 2000 ? 'var(--orange)' : 'var(--green)';
      parts.push(`<span style="color:${col}">${load}ms</span>`);
    }
    if (mob != null) {
      const col = mob < 25 ? 'var(--red)' : mob < 50 ? 'var(--orange)' : 'var(--green)';
      parts.push(`mob:<span style="color:${col}">${Math.round(mob)}/100</span>`);
    }
    if (crm) parts.push(`CRM:${crm}`);
    if (px === true)  parts.push('<span style="color:var(--green2)">FB ✓</span>');
    if (px === false) parts.push('<span style="color:var(--dim)">no pixel</span>');
    if (res) parts.push('<span style="color:var(--green2)">reservation ✓</span>');
    if (tp)  parts.push('<span style="color:var(--dim)">TP widget</span>');
  }

  else if (id === 'portal_quality') {
    if (data.portal_listed) {
      const s = data.listing_quality_score || 0;
      const col = s < 0.45 ? 'var(--red)' : s < 0.7 ? 'var(--orange)' : 'var(--green)';
      parts.push(`Rightmove <span style="color:${col}">${Math.round(s*100)}%</span>`);
      if (data.days_on_market) parts.push(`${data.days_on_market}d on market`);
    } else {
      parts.push('<span style="color:var(--red)">NOT listed on Rightmove</span>');
    }
  }

  else if (id === 'planning_intel') {
    const stage = data.development_stage || 'unknown';
    const col = stage === 'pre_launch' ? 'var(--yellow)' : stage === 'active' ? 'var(--green2)' : 'var(--dim)';
    parts.push(`<span style="color:${col}">${stage}</span>`);
    if (data.has_register_interest_page)
      parts.push('<span style="color:var(--yellow)">register interest</span>');
    const apps = (data.recent_planning_apps || []).length;
    if (apps) parts.push(`${apps} planning app${apps > 1 ? 's' : ''}`);
  }

  else if (id === 'social_review') {
    const r = data.avg_rating;
    const tp = data.trustpilot_business_id;
    if (r) {
      const col = r < 3.5 ? 'var(--red)' : r < 4.2 ? 'var(--orange)' : 'var(--green)';
      parts.push(`Trustpilot <span style="color:${col}">${r}/5</span> (${data.review_count || 0} reviews)`);
    } else if (tp) {
      parts.push('<span style="color:var(--dim)">TP ID found · ratings need API key</span>');
    } else {
      parts.push('<span style="color:var(--dim)">no reviews found</span>');
    }
  }

  return parts.join('  ');
}

// Build initial (pending) collector rows
function initCollectors() {
  const el = document.getElementById('collectors');
  el.innerHTML = '';
  COLLECTORS.forEach(id => {
    const row = document.createElement('div');
    row.className = 'coll-row';
    row.id = 'coll-' + id;
    row.innerHTML = `
      <span class="coll-name">${LABELS[id] || id}</span>
      <span class="coll-time" id="ct-${id}"></span>
      <span class="coll-summary" id="cs-${id}"><span style="color:var(--dimmer)">···</span></span>
    `;
    el.appendChild(row);
  });
}

function markCollectorRunning(id) {
  const row = document.getElementById('coll-' + id);
  if (row) row.classList.add('running');
  const sum = document.getElementById('cs-' + id);
  if (sum) sum.innerHTML = '<span class="spinner">◌</span>';
}

function markCollectorDone(id, data, source, elapsed) {
  const row = document.getElementById('coll-' + id);
  if (row) { row.classList.remove('running'); row.classList.add('done'); }
  const timeEl = document.getElementById('ct-' + id);
  if (timeEl) timeEl.textContent = elapsed.toFixed(1) + 's';
  const sumEl = document.getElementById('cs-' + id);
  if (sumEl) sumEl.innerHTML = collectorSummary(id, data, source);
}

function renderSignals(signals, icp, confidence, triage) {
  const section = document.getElementById('sig-section');
  section.classList.remove('hidden');

  const triageCol = triage === 'auto_approved' ? 'var(--green2)' : triage === 'pending_review' ? 'var(--yellow)' : 'var(--red)';
  document.getElementById('sig-meta').innerHTML =
    `<strong>${signals.length} signals</strong>  ·  ` +
    `<span style="color:var(--green2)">${icp.replace(/_/g,' ')}</span> ${Math.round(confidence*100)}%  ·  ` +
    `<span style="color:${triageCol}">${triage}</span>`;

  const el = document.getElementById('signals');
  el.innerHTML = '';
  signals.forEach(sig => {
    const row = document.createElement('div');
    row.className = 'sig-row';
    const trigger = (sig.emotional_trigger || '').slice(0, 80);
    row.innerHTML = `
      <span class="sev ${sig.severity}">[${sig.severity}]</span>
      <span class="sig-id">${sig.signal_id}</span>
      <span class="sig-trig">"${trigger}"</span>
    `;
    el.appendChild(row);
  });
}

function renderHook(subject, hookText, followUp) {
  const section = document.getElementById('out-section');
  section.classList.remove('hidden');
  const el = document.getElementById('outbound');

  const copyHook = () => navigator.clipboard.writeText(hookText).catch(() => {});
  const id = 'hook-text-' + Date.now();

  el.innerHTML = `
    <div class="subject">SUBJECT &nbsp; <span>${subject}</span></div>
    <div class="hook-box">
      <div class="box-label">
        hook
        <button class="copy-btn" onclick="navigator.clipboard.writeText(document.getElementById('${id}').textContent)">COPY</button>
      </div>
      <div id="${id}">${hookText}</div>
    </div>
    <div class="followup-box">
      <div class="box-label">follow-up · day 3</div>
      <div>${followUp}</div>
    </div>
  `;
}

function renderFooter(dq) {
  const el = document.getElementById('footer-bar');
  el.innerHTML = '';
  Object.entries(dq).forEach(([k, v]) => {
    const tag = document.createElement('span');
    tag.className = 'tag ' + v;
    tag.textContent = v === 'real' ? '✓ ' + k : v === 'dummy' ? '~ ' + k + ' (dummy)' : '✗ ' + k + ' (no key)';
    el.appendChild(tag);
  });
}

function setStatus(msg, blink) {
  const el = document.getElementById('status');
  el.innerHTML = blink ? `> ${msg} <span class="blink">_</span>` : `> ${msg}`;
}

// Main scan
document.getElementById('scanForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const domain = document.getElementById('domain').value.trim();
  const geo    = document.getElementById('geo').value;
  if (!domain) return;

  // Reset UI
  document.getElementById('scanBtn').disabled = true;
  document.getElementById('output').classList.remove('hidden');
  document.getElementById('sig-section').classList.add('hidden');
  document.getElementById('out-section').classList.add('hidden');
  document.getElementById('footer-bar').innerHTML = '';
  document.getElementById('timer').classList.remove('hidden');

  initCollectors();
  setStatus('initialising scan for ' + domain, true);

  startTime = Date.now();
  if (timerInterval) clearInterval(timerInterval);
  timerInterval = setInterval(() => {
    const s = Math.floor((Date.now() - startTime) / 1000);
    document.getElementById('timer').textContent = s + 's';
  }, 1000);

  const url = `/v1/audit/stream?domain=${encodeURIComponent(domain)}&geography=${geo}`;
  const source = new EventSource(url);

  source.onmessage = (e) => {
    const ev = JSON.parse(e.data);

    if (ev.type === 'start') {
      setStatus('scanning ' + ev.domain, true);
    }
    else if (ev.type === 'collector_running') {
      markCollectorRunning(ev.collector_id);
    }
    else if (ev.type === 'collector_done') {
      markCollectorDone(ev.collector_id, ev.data, ev.data_source, ev.elapsed);
    }
    else if (ev.type === 'analysis_done') {
      setStatus('mapping signals', true);
      renderSignals(ev.signals, ev.icp_persona, ev.icp_confidence, ev.triage);
    }
    else if (ev.type === 'generating_hook') {
      setStatus('generating outbound copy', true);
    }
    else if (ev.type === 'hook') {
      renderHook(ev.subject_line, ev.hook_text, ev.follow_up_angle);
    }
    else if (ev.type === 'data_quality') {
      renderFooter(ev.data_quality);
    }
    else if (ev.type === 'complete') {
      clearInterval(timerInterval);
      setStatus('done · ' + Math.floor((Date.now() - startTime) / 1000) + 's', false);
      document.getElementById('scanBtn').disabled = false;
      source.close();
    }
    else if (ev.type === 'error') {
      setStatus('error: ' + ev.message, false);
      document.getElementById('scanBtn').disabled = false;
      source.close();
      clearInterval(timerInterval);
    }
  };

  source.onerror = () => {
    source.close();
    clearInterval(timerInterval);
    setStatus('connection lost', false);
    document.getElementById('scanBtn').disabled = false;
  };
});

// Focus domain input on load
window.addEventListener('load', () => document.getElementById('domain').focus());
</script>
</body>
</html>
"""
