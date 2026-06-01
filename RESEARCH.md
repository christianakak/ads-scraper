# GTM Intelligence Engine — Research Findings

Architecture validation against modern GTM systems, tooling constraints,
and signal coverage. Documents what changes from the initial plan and why.

---

## 1. What Top GTM Systems Do Architecturally

### Clay
Clay's architecture is "table-as-pipeline." Each column is an enrichment action.
The core insight that makes it powerful is **waterfall enrichment**: try Provider A,
fall back to Provider B, stop when confidence threshold is met. They don't call all
sources for every row — they stop early.

**Steal this:** Our 5 collectors should not all run unconditionally. Add a fast
screening layer (DNS + headers, ~1s) that confirms the domain is an active developer
before firing expensive Playwright/portal scrapers.

### Apollo / ZoomInfo
Built on proprietary contact databases first, signal layer second. Their "intent"
signals (job change, funding, tech install) are what drive outbound timing.

**Steal this:** Signal *timing* matters as much as signal *presence*. A developer
with static floor plans who just got planning permission is a fundamentally different
prospect than one who's been sitting on a stale listing for 2 years.

### Warmly / RB2B
Anonymous visitor identification. IP → company → CRM enrichment → Slack alert.
Architecture is: inbound pixel → real-time enrichment → instant notification.

**Steal this:** "High-intent" flag in our output. A domain that has ad spend +
recent planning permission + no digital reservation = HIGH INTENT. This should be
a first-class field, not derived by the Clay operator.

### Bombora
Intent data from a co-op of B2B content publishers. Detects "surge topics" — when
a company is consuming content about a subject more than baseline.

**Relevant insight:** They score intent on a TOPIC level, not just company level.
Our `hook_angle` field in pain_rules.json does the same thing. Keep it.

### Common Room
Social signal aggregation with entity resolution — same person across GitHub,
LinkedIn, Discord, Slack. Their key IP is cross-platform identity matching.

**Not directly applicable** for our use case, but entity resolution
(domain → company → people) matters when we add LinkedIn signals later.

---

## 2. Playwright on Lambda — Critical Architecture Change

**Problem with raw Playwright on Lambda:**
- Lambda layers have a 250MB unzipped limit. Chromium alone is ~300MB.
- Lambda container images (up to 10GB) work, but cold starts for Playwright
  containers are 8–15s. Combined with scrape time, the 45s Clay timeout
  becomes tight under load.
- Managing Chromium versions, ARM vs x86 compatibility, and font rendering
  issues across Lambda environments is ongoing maintenance overhead.

**Recommended change: Browserless.io**

Browserless is a managed Chromium-as-a-Service API. Instead of running
Playwright inside Lambda, Lambda sends a WebSocket/HTTP request to Browserless,
which runs the browser remotely and returns the result.

Benefits:
- Zero cold start for the browser layer
- Free tier: 6 hours/month of browser time (sufficient for MVP)
- Playwright SDK connects to it with a one-line `connect()` change
- No Lambda layer size issues
- Scales independently from Lambda

```python
# Before (Lambda runs Chromium locally):
browser = await playwright.chromium.launch()

# After (Lambda connects to Browserless):
browser = await playwright.chromium.connect_over_cdp(
    f"wss://chrome.browserless.io?token={BROWSERLESS_TOKEN}"
)
```

**Alternative:** Apify (more expensive, more features) or self-hosted
Browserless on a small EC2/Fly.io instance if volume justifies it.

**Architecture change:** Site Scanner and Portal Quality collectors connect
to Browserless. All other collectors are pure HTTP — no browser dependency.

---

## 3. Meta Ad Library API — Current State

**What's available (free):**
- Endpoint: `https://graph.facebook.com/v19.0/ads_archive`
- Requires: Facebook developer account + access token (free)
- Rate limit: ~200 calls/hour per token (sufficient for our volume)
- Fields available: `ad_delivery_start_time`, `ad_creative_bodies`,
  `ad_snapshot_url`, `impressions` (range), `spend` (range),
  `page_name`, `page_id`, `ad_delivery_stop_time`

**The spend field returns ranges, not exact values:**
```json
{ "spend": { "lower_bound": "100", "upper_bound": "499" } }
```
This is fine — we need spend TIER not exact figures.

**The domain → Page ID problem:**
The API searches by `search_terms` (advertiser name) or `search_page_ids`.
We need to resolve `developer.co.uk` → Facebook Page ID first.

Resolution approach:
1. Scrape the site for Facebook Page link (`facebook.com/[page]`)
2. If not found, search Ad Library by domain name as `search_terms`
3. Fuzzy match on page name vs company name from domain

**Google Ads Transparency Center:**
No official API. Must be scraped. However, it's a React SPA so requires
Playwright. Route through Browserless. Key data: is the domain running
Google Search/Display campaigns, and what landing pages do they point to.

**Consideration:** For MVP, Meta Ad Library alone gives us 80% of ad signals.
Google Ads Transparency can be Phase 2.

---

## 4. Free Signal Stack (No Paid APIs)

The best free stack for our signal needs:

| Need | Tool | Cost | Quality |
|------|------|------|---------|
| Page speed, Core Web Vitals | Google PageSpeed Insights API | Free, 25k/day | Excellent |
| Tech stack detection | Wappalyzer (Python lib) | Free, OSS | Excellent |
| DNS/headers/hosting | Direct DNS + `httpx` headers | Free | Good |
| Ad spend signals | Meta Ad Library API | Free | Good |
| Traffic proxy | Common Crawl via Athena | ~$0.005/query | Moderate |
| Email security setup | DNS MX/SPF/DKIM lookups | Free | Good |
| Domain age | WHOIS lookup | Free | Good |
| Technology/CRM pixels | Wappalyzer + script tag scan | Free | Excellent |

**Wappalyzer is critical and was missing from the original plan.**
It's a Python library (`python-wappalyzer`) that detects 1500+ technologies
from HTML source, script tags, HTTP headers, and meta tags. Install it in
the Site Scanner and get CRM detection, analytics tools, chat platforms,
ad pixels, and hosting stack for free.

Detects: HubSpot, Salesforce, Marketo, ActiveCampaign, Google Tag Manager,
Facebook Pixel, Google Analytics 4, Hotjar, Intercom, Drift, WordPress,
Webflow, Shopify, and 1490+ more.

---

## 5. ICP Classification Approach

**Rules-based weighted scoring is correct for MVP.** We don't have training
data yet, so ML is premature. Once we have 200+ audits with known conversion
outcomes, a logistic regression model would improve accuracy significantly.

The pattern that works best in production GTM systems is **additive scoring
with signal weighting:**

```python
# Each signal has a weight per persona
SIGNAL_WEIGHTS = {
    "scale_up_developer": {
        "project_count_3plus": 0.25,
        "ad_fatigue_score_high": 0.20,
        "no_chat_automation": 0.15,
        "enquire_cta": 0.20,
        "no_digital_reservation": 0.20,
    },
    "premium_visionary": {
        "static_floor_plans": 0.30,
        "no_virtual_tour": 0.25,
        "pricing_poa": 0.25,
        "high_design_quality_score": 0.20,
    },
    "data_driven_planner": {
        "recent_planning_granted": 0.40,
        "no_portal_listing": 0.30,
        "no_ad_activity": 0.30,
    }
}
```

**Add a "high_intent" composite flag** — separate from ICP persona.
High intent = the prospect has a live, urgent pain right now (not just
a structural weakness). Triggers: recent planning permission + no active
listing, or ad fatigue >45 days + no chat automation, or days on market >90.

---

## 6. Supabase for Audit Storage — Gotchas

**Works well for our use case with these caveats:**

1. **Store JSONB + extracted columns.** Don't just dump the full audit JSON.
   Extract key fields as typed columns so Clay/queries work without JSON parsing:
   ```sql
   icp_persona TEXT, icp_confidence FLOAT,
   top_pain_signal TEXT, top_pain_severity TEXT,
   primary_module TEXT, hook_text TEXT,
   raw_audit JSONB  -- full object for debugging
   ```

2. **Signal freshness / cache invalidation.** Add `collected_at TIMESTAMPTZ`.
   Before running a full audit, check if domain was audited in the last 30 days.
   Return cached result if fresh. This is critical for cost control.

3. **Free tier goes to sleep after 1 week of inactivity.** For production, use
   Pro ($25/mo). For development, keep it active with a weekly ping cron.

4. **Row-level security is on by default.** Lambda's service key needs explicit
   RLS policies to INSERT/SELECT. Easy to forget — will cause silent 403s.

5. **PGMQ (message queue extension) is available** but still early-stage.
   For batch job queuing, use a simple `jobs` table with a status enum
   (`queued`, `running`, `completed`, `failed`) — more reliable for now.

6. **Unique index on (domain, vertical)** prevents duplicate audits and
   enables efficient cache-hit lookups.

---

## 7. Critical Signals Missing from Original Plan

These signals should be added — all free, high-signal-to-noise:

### A. Wappalyzer Technology Stack (HIGH PRIORITY — was missing)
Detect CRM pixels, marketing tools, analytics, chat platforms from script tags.

New pain signals unlocked:
- No Facebook Pixel detected → not running paid social → opportunity signal
- No Google Tag Manager → not tracking user behaviour → unsophisticated
- HubSpot detected but no Journey → tool fragmentation (Journey pitch)
- Old analytics (UA vs GA4) → not modernising stack → data risk signal
- No retargeting pixel → spending on ads but not retargeting → Lemon pitch

### B. DNS / Email Infrastructure Signals (LOW COST, HIGH VALUE)
A DNS lookup takes <100ms and reveals:

- **SPF/DKIM/DMARC presence:** Missing = not doing email marketing seriously.
  Absent DMARC on a developer domain = they're not running CRM sequences.
- **Email provider (MX records):** Google Workspace vs Office 365 vs
  unknown provider = company maturity signal.
- **Domain age (WHOIS):** <2 years = likely newer company = Data-Driven
  Planner or Scale-Up starting out.
- **Hosting provider (NS records):** GoDaddy shared hosting = low
  sophistication. Cloudflare + AWS/GCP = technical team or agency.
- **Redirect chains:** `developer.co.uk` → 3 redirects → `www.devsite.co.uk`
  = technical debt signal.

### C. Social Media Activity (MODERATE VALUE)
LinkedIn company page last post date. >60 days inactive = team too busy
(Scale-Up overwhelm signal) or project not being marketed.

LinkedIn scraping is risky (ToS). Better approach: check for LinkedIn
company page URL on the site, then use the public LinkedIn profile URL
to check recent activity via their public feed (no auth required for
basic signals).

### D. Blog / Content Freshness (EASY, FREE)
Parse the site's blog/news section. Last post date > 90 days = not doing
content marketing. No blog at all = pure paid-media dependency.

Cross-reference with ad spend: high ad spend + no content = pure
interruption marketing = Lemon pitch (content automation).

### E. GDPR / Cookie Consent (UK/SWEDEN SPECIFIC)
UK GDPR and Swedish GDPR (GDPR applies in SE) require cookie consent.
Missing cookie consent banner on a UK/SE developer site = either new
company or non-compliant = risk signal (also: they're probably not
tracking properly, which undermines their ad optimisation).

Detection: Look for OneTrust, Cookiebot, CookieYes, Usercentrics script
tags or banner DOM elements.

---

## Architecture Changes from Research

### Change 1: Replace Lambda-hosted Playwright with Browserless.io
**Impact:** Site Scanner + Portal Quality collectors use Browserless
connection instead of local Chromium. Eliminates Lambda cold start,
size limits, and maintenance overhead.

### Change 2: Add Wappalyzer to Site Scanner
**Impact:** Site Scanner now returns tech stack signals for free.
Unlocks CRM detection, pixel detection, analytics tool detection.
This is a significant signal upgrade at zero cost.

### Change 3: Add DNS / Headers Collector (new, 5th collector → 6th)
**Impact:** Ultra-fast (<1s), zero cost, zero browser dependency.
Runs first as the screening layer before expensive collectors fire.
Rename existing "5 collectors" to "6 collectors":

```
0. DNS/Headers (screening layer, <1s, always runs first)
1. Ad Intelligence (Meta Ad Library)
2. Site Scanner + Wappalyzer (Browserless)
3. Portal Quality (Browserless)
4. Planning Intelligence
5. Social / Review Scanner
```

### Change 4: Add Signal Freshness / Cache Layer to Supabase
**Impact:** Check `collected_at` before re-scraping. Return cached
audit if <30 days old. Dramatically reduces cost and rate-limit pressure.

### Change 5: Add `high_intent` Flag to Output Schema
**Impact:** First-class boolean + composite score. Clay can filter
on this directly without needing to interpret pain signal arrays.

### Change 6: Add `tech_stack` and `email_infrastructure` to Output
**Impact:** Wappalyzer + DNS signals surfaced as dedicated fields
in `clay_flat` for direct column mapping.

---

## What Does NOT Need to Change

- **Registry pattern** — validated by how top GTM systems handle
  multi-vertical enrichment. Keep as designed.
- **Async parallel collection** — correct. asyncio.gather() is
  the right pattern. All collectors run in parallel after screening.
- **Claude API for hook generation** — no better alternative for
  persona-aware, context-rich hook writing.
- **Pain-to-module JSON rules** — correct pattern. Bombora and
  Keyplay both use declarative rules files that non-engineers can edit.
- **ICP classification logic** — weighted scoring is the right MVP
  approach. ML upgrade path is valid for v2.
- **Supabase** — correct choice. JSONB + extracted columns pattern
  is the right implementation approach.
- **Synchronous Clay API contract** — correct. With Browserless
  eliminating cold start, 15-25s total is achievable.
