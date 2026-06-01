# GTM Intelligence Engine — Architecture Plan

## Project: Newbuilds (PropTech Vertical)

A modular, vertical-agnostic "Digital Sales Forensic Suite" for B2B outbound.
Ingests a domain → outputs a structured pain audit → generates a personalised
cold outreach hook → feeds directly into Clay/CRM sequences.

---

## Locked Decisions

| Concern            | Decision                                                    |
|--------------------|-------------------------------------------------------------|
| Deployment         | AWS Lambda + API Gateway                                    |
| Browser automation | Browserless.io (managed Chromium API, not Lambda-hosted)    |
| Storage            | Supabase (Postgres + JSONB + extracted typed columns)       |
| Hook generation    | Claude API (Sonnet)                                         |
| Data sources       | Free/open initially (no paid APIs)                          |
| Geography          | UK + Sweden                                                 |
| Collectors         | 6 collectors (DNS/Headers screening layer added)            |
| Interface          | Clean REST API — Clay, CLI, and standalone                  |
| Initial vertical   | PropTech (registry-extensible)                              |
| ICP                | Norwegian property developers                               |

---

## Repository Structure

```
gtm-intelligence-engine/
│
├── core/                          # Vertical-agnostic. Zero PropTech knowledge here.
│   ├── base/
│   │   ├── collector.py           # BaseCollector ABC + NormalizationMixin
│   │   ├── analyzer.py            # BaseAnalyzer ABC
│   │   ├── schemas.py             # Pydantic: CollectorResult, PainSignal, AuditReport
│   │   └── registry.py            # Maps vertical slug → collectors + rules
│   ├── engine.py                  # DomainAuditor — parallel async orchestration
│   ├── hook_generator.py          # Claude API, persona-aware prompt builder
│   ├── normalizer.py              # NormalizationLayer: currency, dates, CTA enums
│   └── stealth.py                 # HTTP client with UA rotation, backoff, cookie jar
│
├── verticals/
│   └── proptech/                  # Add saas/, fintech/ here for new verticals
│       ├── collectors/
│       │   ├── dns_headers.py     # Screening layer: DNS, WHOIS, HTTP headers
│       │   ├── ad_intelligence.py # Meta Ad Library + Google Ads Transparency
│       │   ├── site_scanner.py    # Browserless + Wappalyzer + PageSpeed
│       │   ├── portal_quality.py  # Rightmove (UK) + Hemnet (SE) listing scanner
│       │   ├── planning_intel.py  # UK Planning Portal + Swedish Lantmäteriet
│       │   └── social_review.py   # Google Places, Trustpilot, HomeViews
│       ├── analyzers/
│       │   ├── pain_mapper.py     # Signal → {pain, confidence, emotional_trigger, module}
│       │   ├── icp_classifier.py  # Score signals → Scale-Up / Premium / Planner
│       │   └── benchmarks.py      # Industry baseline values
│       └── rules/
│           ├── pain_rules.v1.json # Versioned rules — never overwrite, always increment
│           └── icp_rules.v1.json  # ICP classification weights
│
├── api/
│   ├── app.py                     # FastAPI app (local + Lambda via Mangum)
│   ├── lambda_handler.py          # Mangum wrapper — one-line Lambda adapter
│   └── models.py                  # Request/response Pydantic models
│
├── cli/
│   └── audit.py                   # Typer CLI: single domain, batch CSV, export
│
├── infrastructure/
│   ├── template.yaml              # AWS SAM (Lambda + API Gateway)
│   └── supabase_schema.sql        # Tables: audits, outcomes, triage_queue + indexes
│
└── tests/
    ├── unit/                      # Analyzer/classifier logic, no network calls
    ├── integration/               # Collector tests against real domains
    └── fixtures/                  # Cached responses for fast unit tests
```

---

## The 6 Collectors

Collectors run in two phases:
- **Phase 0 — Screening** (DNS/Headers, ~1s): Confirms domain is active before
  firing expensive browser collectors. Acts as a waterfall gate.
- **Phase 1 — Full Audit** (remaining 5 in parallel via `asyncio.gather`, ~15–25s):
  Only fires if screening passes. Browser work routed through Browserless.io.

---

### 0. DNS / Headers Collector (Screening Layer)
**Source:** Direct DNS lookups + `httpx` HTTP headers. Zero browser dependency.

| Signal                  | Detection method                        | Pain trigger                      |
|-------------------------|-----------------------------------------|-----------------------------------|
| `domain_age_years`      | WHOIS `creation_date`                   | <2yr = new company signal         |
| `hosting_provider`      | NS records + IP ASN lookup              | Shared hosting = low maturity     |
| `email_provider`        | MX records                              | Unknown = not doing CRM sequences |
| `has_spf_dkim_dmarc`    | TXT record lookup                       | Missing = no email marketing      |
| `redirect_chain_length` | Follow HTTP redirects, count hops       | >2 = technical debt               |
| `has_ssl`               | SSL cert presence + expiry              | Expired = neglected site          |
| `cdn_provider`          | `CF-Ray`, `X-Amz-Cf-Id` response headers | Cloudflare = some sophistication |

---

### 1. Ad Intelligence Collector
**Sources:** Meta Ad Library (free API), Google Ads Transparency Center (scraped)

| Signal               | Method                                        | Pain trigger         |
|----------------------|-----------------------------------------------|----------------------|
| `creative_age_days`  | `ad_delivery_start_time` from Ad Library      | >30d → Lemon         |
| `ad_count`           | Count active ads per domain                   | Fragmentation signal |
| `cta_in_ad_copy`     | Regex on ad body text                         | "Enquire" intent gap |
| `landing_page_url`   | Ad Library `snapshot.link_url`                | Off-portal leak      |
| `ad_fatigue_score`   | Composite: age + frequency + copy repetition  | HIGH → Lemon pitch   |
| `spend_tier`         | Estimated from ad count + age + placements    | High spend + weak LP |

**Emotional trigger:** Scale-Up Developer's invisible money drain.

---

### 2. Site Scanner Collector
**Sources:** Browserless.io (managed Chromium), Playwright SDK, Google PageSpeed Insights API (free), Wappalyzer (OSS tech detection library)

| Signal                        | Detection method                                           | Pain trigger          |
|-------------------------------|------------------------------------------------------------|-----------------------|
| `has_interactive_floor_plans` | iFrame detection: Giraffe360, iSpy, Matterport, Plot.ai   | Static → EVE3D        |
| `has_virtual_tour`            | URL patterns, Matterport embeds, `<iframe src="*matter*">`| Missing → EVE3D       |
| `has_digital_reservation`     | URL scan: /reserve, /book, payment form fields            | Missing → Journey     |
| `cta_type`                    | Primary CTA text classification: enquire vs reserve       | Friction → Journey    |
| `has_chat_automation`         | Wappalyzer: Intercom, Drift, Tidio, Crisp, HubSpot chat   | Missing = overwhelm   |
| `pricing_transparency`        | "Price on application" vs actual price shown              | POA = confidence gap  |
| `load_time_ms`                | PageSpeed `first_contentful_paint`                        | >3000ms → conversion  |
| `mobile_score`                | PageSpeed mobile score                                    | <70 = tech debt       |
| `project_count`               | Count project cards on site                               | 3+ = Scale-Up signal  |
| `crm_detected`                | Wappalyzer: HubSpot, Salesforce, Marketo, ActiveCampaign  | Fragmented stack pain |
| `has_facebook_pixel`          | Wappalyzer + script tag scan                              | No pixel = blind spend|
| `has_google_tag_manager`      | Wappalyzer                                                | Missing = not tracking|
| `analytics_platform`          | Wappalyzer: GA4, UA, Plausible, etc.                     | UA = legacy = debt    |
| `has_cookie_consent`          | Wappalyzer: OneTrust, Cookiebot, CookieYes                | Missing = non-compliant (UK/SE GDPR) |
| `content_freshness_days`      | Parse blog/news section, last post date                   | >90 days = neglected  |
| `tech_stack`                  | Full Wappalyzer result                                    | Stack sophistication  |

**Note:** Browser work routed to Browserless.io. Lambda connects via `playwright.chromium.connect_over_cdp()`. Eliminates cold-start and Chromium size issues on Lambda.

**Emotional trigger:** Premium Visionary's quiet humiliation.

---

### 3. Portal Quality Collector
**Sources:** Rightmove (UK), Hemnet (SE), Zoopla (UK)

| Signal                    | Detection method                     | Pain trigger              |
|---------------------------|--------------------------------------|---------------------------|
| `portal_listed`           | Search portals by developer name     | Not listed = gap          |
| `listing_photo_count`     | Parse listing image count            | <8 photos = low intent    |
| `has_floorplan_on_portal` | Floorplan tab/section present        | Missing = lower intent    |
| `has_virtual_tour_on_portal` | Virtual tour badge on portal      | Missing = EVE3D           |
| `listing_quality_score`   | Composite: photos + description + price | Low score = Newbuilds  |
| `days_on_market`          | Listing date vs today                | High = velocity problem   |
| `price_shown`             | Price displayed vs POA               | POA = confidence gap      |

**Emotional trigger:** Data-Driven Planner's distribution fear.

---

### 4. Planning Intelligence Collector
**Sources:** UK Planning Portal + PlanningAlerts.org, Swedish Lantmäteriet / PBL

| Signal                  | Detection method                             | Pain trigger               |
|-------------------------|----------------------------------------------|----------------------------|
| `recent_planning_apps`  | Company name / postcode search               | Recent = timing trigger    |
| `planning_granted_date` | Parse decision date                          | <180d = pre-launch window  |
| `estimated_unit_count`  | Parse planning docs                          | Scale indicator            |
| `development_stage`     | Cross-ref planning date vs portal listing    | Pre-launch = Plot.ai pitch |
| `new_geography_flag`    | Planning location vs company HQ              | Expanding = Planner ICP    |

**Emotional trigger:** Data-Driven Planner's pre-regret anxiety.

---

### 5. Social / Review Scanner
**Sources:** Google Places API (free tier), Trustpilot (scraped), HomeViews (UK, scraped)

| Signal               | Detection method                              | Pain trigger               |
|----------------------|-----------------------------------------------|----------------------------|
| `review_count`       | Total reviews across platforms                | Low = trust gap            |
| `avg_rating`         | Weighted average                              | <4.0 = CX issue            |
| `response_rate`      | % reviews with owner response                 | Low = manual overload      |
| `sentiment_keywords` | NLP on review text                            | "slow response" = Journey  |
| `has_reviews_page`   | Dedicated reviews section on site             | Missing = weak proof       |

**Emotional trigger:** Scale-Up Developer's team capacity fear.

---

## ICP Classification Logic

Each detected signal contributes a weighted score to three persona buckets.
Highest score wins. Confidence = winner / total.

```
scale_up_score  ← project_count(3+), ad_fatigue, no_chat_automation,
                  low_response_rate, high_ad_count, enquire_cta

premium_score   ← high_design_quality, static_floor_plans, no_virtual_tour,
                  pricing_poa, no_digital_reservation, low_photo_count

planner_score   ← recent_planning_granted, no_portal_listing, no_ad_activity,
                  new_geography_flag, low_review_count
```

---

## Pain → Module Mapping Schema (pain_rules.json)

```json
{
  "stale_creative": {
    "threshold": { "creative_age_days": { "gte": 30 } },
    "severity": "HIGH",
    "business_pain": "Ad fatigue driving CPL up, CTR declining",
    "emotional_trigger": "You're paying for impressions that stopped converting weeks ago",
    "m360_module": "Lemon",
    "hook_angle": "velocity",
    "icp_fit": ["scale_up_developer"]
  },
  "static_floor_plans": {
    "threshold": { "has_interactive_floor_plans": false },
    "severity": "HIGH",
    "business_pain": "Lower buyer confidence = slower reservation velocity",
    "emotional_trigger": "Buyers can't picture themselves in the space — they leave",
    "m360_module": "EVE3D",
    "hook_angle": "premium",
    "icp_fit": ["premium_visionary", "scale_up_developer"]
  },
  "enquire_cta": {
    "threshold": { "cta_type": "enquire" },
    "severity": "CRITICAL",
    "business_pain": "Manual funnel = team bottleneck + lead leakage at scale",
    "emotional_trigger": "Every enquiry form requires a human — at volume, this breaks",
    "m360_module": "Journey",
    "hook_angle": "velocity",
    "icp_fit": ["scale_up_developer"]
  },
  "no_virtual_tour": {
    "threshold": { "has_virtual_tour": false },
    "severity": "HIGH",
    "business_pain": "Buyers cannot experience the project off-plan",
    "emotional_trigger": "Competitors with 3D tours are reserving faster than you",
    "m360_module": "EVE3D",
    "hook_angle": "premium",
    "icp_fit": ["premium_visionary"]
  },
  "pre_launch_no_data": {
    "threshold": { "development_stage": "pre_launch" },
    "severity": "CRITICAL",
    "business_pain": "Unit mix decided without live demand data = execution risk",
    "emotional_trigger": "Building the wrong thing in a new market is a multi-million mistake",
    "m360_module": "Plot.ai",
    "hook_angle": "certainty",
    "icp_fit": ["data_driven_planner"]
  },
  "high_days_on_market": {
    "threshold": { "days_on_market": { "gte": 90 } },
    "severity": "CRITICAL",
    "business_pain": "Stalled velocity = carrying costs mounting, investor pressure rising",
    "emotional_trigger": "Every week unsold is money bleeding from the project",
    "m360_module": "Newbuilds.com",
    "hook_angle": "velocity",
    "icp_fit": ["scale_up_developer", "data_driven_planner"]
  }
}
```

---

## Output Schema

```json
{
  "audit_id": "uuid",
  "domain": "developer.co.uk",
  "timestamp": "2026-01-01T00:00:00Z",
  "geography": "uk",
  "vertical": "proptech",

  "icp_persona": "scale_up_developer",
  "icp_confidence": 0.84,

  "high_intent": true,
  "high_intent_reason": "recent_planning_permission + no_portal_listing",

  "rules_version": "1.0.0",

  "pain_signals": [
    {
      "signal_id": "stale_creative",
      "severity": "HIGH",
      "confidence": 0.92,
      "detected_value": { "creative_age_days": 47 },
      "business_pain": "Ad fatigue driving CPL up, CTR declining",
      "emotional_trigger": "You're paying for impressions that stopped converting weeks ago",
      "m360_module": "Lemon",
      "corroborating_signals": ["high_ad_count", "no_facebook_pixel_conversion_tracking"]
    }
  ],

  "recommended_modules": ["Journey", "Lemon"],
  "primary_module": "Journey",

  "outbound": {
    "hook_text": "3-sentence opener referencing specific findings...",
    "subject_line": "...",
    "follow_up_angle": "..."
  },

  "tech_stack": {
    "crm": "HubSpot",
    "analytics": "GA4",
    "has_facebook_pixel": true,
    "has_google_tag_manager": false,
    "has_cookie_consent": true,
    "hosting": "Cloudflare + Vercel",
    "raw_wappalyzer": {}
  },

  "email_infrastructure": {
    "has_spf": true,
    "has_dkim": false,
    "has_dmarc": false,
    "email_provider": "Google Workspace",
    "domain_age_years": 3.2
  },

  "triage": {
    "review_status": "auto_approved",
    "review_reason": null,
    "audit_confidence": 0.88
  },

  "cache_meta": {
    "collected_at": "2026-01-01T00:00:00Z",
    "cache_hit": false,
    "collectors_run": ["dns_headers", "ad_intelligence", "site_scanner",
                       "portal_quality", "planning_intel", "social_review"],
    "collector_errors": []
  },

  "raw_collector_output": {},

  "clay_flat": {
    "icp_persona": "scale_up_developer",
    "icp_confidence": 0.84,
    "high_intent": true,
    "review_status": "auto_approved",
    "top_pain_signal": "stale_creative",
    "top_pain_signal_confidence": 0.92,
    "top_pain_severity": "HIGH",
    "primary_module": "Journey",
    "hook_text": "...",
    "subject_line": "...",
    "ad_creative_age_days": 47,
    "has_digital_reservation": false,
    "has_virtual_tour": false,
    "cta_type": "enquire",
    "crm_detected": "HubSpot",
    "has_facebook_pixel": true,
    "has_google_tag_manager": false,
    "domain_age_years": 3.2,
    "rules_version": "1.0.0",
    "collected_at": "2026-01-01T00:00:00Z"
  }
}
```

---

## API Design

```
POST /v1/audit
  Body:     { "domain": "developer.co.uk", "vertical": "proptech", "geography": "uk" }
  Response: Full AuditReport JSON
  Notes:    Returns cached result if audit exists and collected_at < 30 days ago.
            Force refresh with ?force=true

GET  /v1/audit/{audit_id}
  Response: Retrieve stored audit from Supabase

POST /v1/audit/batch
  Body:     { "domains": ["a.com", "b.com"], "vertical": "proptech" }
  Response: { "job_id": "uuid", "status": "queued" }

GET  /v1/audit/batch/{job_id}
  Response: Batch job status + results array

GET  /v1/triage
  Query:    ?status=pending_review&vertical=proptech&limit=50
  Response: Paginated list of audits awaiting manual review
  Notes:    Used to build a review queue UI or Airtable/Notion view

PATCH /v1/triage/{audit_id}
  Body:     { "review_status": "approved" | "rejected", "reviewer_note": "..." }
  Response: Updated audit row
  Notes:    Approved audits become eligible for Clay sequence pickup

POST /v1/outcome
  Body:     { "audit_id": "uuid", "outcome": "meeting_booked" | "uninterested" | "no_reply",
              "notes": "..." }
  Response: { "outcome_id": "uuid", "recorded": true }
  Notes:    Feeds the learning loop. Over time, high-converting signal combos
            get higher weight in ICP classifier and hook generator prompt.

GET  /v1/outcome/stats
  Query:    ?vertical=proptech&signal=stale_creative
  Response: Conversion rates per pain signal — powers rule weight tuning

GET  /health
```

Collectors run in parallel via `asyncio.gather()`. Target latency: 15–25s.
Clay HTTP Enrichment timeout: 45s. Lambda timeout: 60s.

**Triage logic:**
- `audit_confidence >= 0.80` → `auto_approved` → eligible for Clay immediately
- `0.60 <= audit_confidence < 0.80` → `pending_review` → sits in triage queue
- `audit_confidence < 0.60` OR conflicting signals → `flagged` → manual review required

---

## M360 Module Reference

| Module        | Solves                                         | ICP fit                        |
|---------------|------------------------------------------------|--------------------------------|
| Plot.ai       | Unit mix feasibility, demand data, pricing     | Data-Driven Planner            |
| EVE3D         | 3D visualisation, digital twin, virtual tour   | Premium Visionary              |
| Newbuilds.com | Portal distribution, high-intent buyer traffic | Scale-Up + Data-Driven Planner |
| Lemon         | Creative automation, multi-channel ad ops      | Scale-Up Developer             |
| Journey       | Digital reservation, automated post-sale CX   | Scale-Up Developer             |

---

## ICP Reference

| Persona               | Core fear                        | Digital fingerprint                              |
|-----------------------|----------------------------------|--------------------------------------------------|
| Scale-Up Developer    | Invisible leakage at volume      | Stale ads, enquire CTA, no chat automation       |
| Premium Visionary     | Premium product, standard price  | Static floor plans, POA, no virtual tour         |
| Data-Driven Planner   | Wrong bet = multi-million loss   | Recent planning permission, no active listing    |

---

## Build Sequence

| Phase | Scope                                                              | Target    |
|-------|--------------------------------------------------------------------|-----------|
| 1     | Core abstractions, Pydantic schemas, registry, Supabase SQL        | Days 1–2  |
| 2     | DNS/Headers collector + Ad Intelligence collector                  | Days 3–4  |
| 3     | Site Scanner (Browserless + Wappalyzer) + PageSpeed integration    | Days 5–7  |
| 4     | Portal Quality (Rightmove/Hemnet) + Planning Intel collectors      | Days 8–10 |
| 5     | Social/Review collector + cache layer (Supabase freshness check)   | Days 11–12|
| 6     | ICP classifier, pain mapper, benchmark engine, high_intent flag    | Days 13–14|
| 7     | Hook generator (Claude API) + FastAPI app + Mangum Lambda handler  | Days 15–16|
| 8     | Outcome Feed endpoint, triage queue, normalization tests           | Days 17–18|
| 9     | SAM infra, CLI tool, env config, end-to-end integration test       | Days 19–20|

---

## Normalization Layer

Every collector's raw output passes through `NormalizationLayer` before hitting
the intelligence layer. This ensures the analyzers never see locale-specific formats.

| Field type       | Raw variants                            | Normalized output              |
|------------------|-----------------------------------------|--------------------------------|
| Currency         | "£450,000" / "4 500 000 kr" / "€500k"  | `{ amount: 450000, currency: "GBP" }` |
| Date             | "3rd March 2025" / "2025-03-03" / "03/03/25" | ISO8601 `"2025-03-03"` |
| CTA type         | "Enquire" / "Förfrågan" / "Ask about"  | `enum: enquire`                |
| CTA type         | "Reserve" / "Boka" / "Book now"        | `enum: reserve`                |
| Unit count       | "32 homes" / "32 bostäder" / "32 units"| `integer: 32`                  |
| Days on market   | "Listed 14 Feb" → today diff           | `integer: 106` (days)          |
| Rating           | "4.2 / 5" / "8.4 / 10"               | Normalized to 0.0–1.0 float    |
| Boolean signals  | "Yes" / "Ja" / true / 1               | `bool: true`                   |

The `NormalizationMixin` on `BaseCollector` runs automatically after `collect()`.
Collectors return raw data; the mixin applies the mapping table before the result
is passed to analyzers. Vertical-specific mappings live in `rules/normalizer_map.json`.

---

## Stealth & Rate-Limiting Strategy

Browserless.io handles anti-bot for all browser-based requests (Chromium via
residential IP pool). The following applies to all non-browser HTTP requests.

**HTTP Client (`core/stealth.py`):**
- User-Agent pool: rotate across 8 realistic browser UA strings (Chrome/Firefox, Win/Mac)
- `Accept-Language`: set to locale of target geography (en-GB for UK, sv-SE for Sweden)
- `Referer`: set to the referring site (e.g., Rightmove referrer when scraping Rightmove)
- Cookie jar: persisted per domain across requests in the same audit session
- Request timing: randomised 1.5–3.5s delay between sequential requests to the same domain
- Retry policy: exponential backoff (1s → 2s → 4s → give up) with jitter on 429/503
- Max retries: 3 per request before marking collector as `partial_failure`

**Rate limits by source:**
| Source                  | Limit                  | Our strategy              |
|-------------------------|------------------------|---------------------------|
| Meta Ad Library API     | ~200 req/hr per token  | Single token sufficient for MVP volume |
| Google PageSpeed API    | 25,000 req/day         | No issue                  |
| UK Planning Portal      | Unspecified, be polite | 2s delay, max 5 pages     |
| Rightmove               | No API, anti-bot       | Browserless + rate limit  |
| Hemnet                  | No API, stronger bot detection | Browserless essential |
| Google Places API       | 100 req/day free tier  | Cache results aggressively |

---

## Rule Versioning

Pain rules and ICP rules are versioned files. Never overwrite — always create a
new version file and update the registry pointer.

```
verticals/proptech/rules/
  pain_rules.v1.0.json     ← first release
  pain_rules.v1.1.json     ← tweaked thresholds based on outcome data
  pain_rules.v2.0.json     ← new signal added (breaking schema change)
  icp_rules.v1.0.json
```

**Rule file header:**
```json
{
  "schema_version": "1.1.0",
  "effective_from": "2026-02-01",
  "changelog": "Raised stale_creative threshold from 30d to 21d based on 40-lead outcome study",
  "rules": { ... }
}
```

**Each audit stores `rules_version`** in the output. This means:
- Historical audits can be re-analysed with a different rule version without re-scraping
- A/B test: run v1.0 rules on cohort A, v1.1 on cohort B, compare `meeting_booked` rate
- Roll back a rule change if reply rate drops

---

## Outcome Feed & Learning Loop

```sql
-- outcomes table (Supabase)
CREATE TABLE outcomes (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  audit_id     UUID REFERENCES audits(id),
  outcome      TEXT CHECK (outcome IN ('meeting_booked', 'uninterested', 'no_reply')),
  recorded_at  TIMESTAMPTZ DEFAULT now(),
  notes        TEXT
);
```

**How the learning loop works:**

1. Outbound sequence runs → outcome recorded via `POST /v1/outcome`
2. Weekly: `GET /v1/outcome/stats` shows conversion rate per `pain_signal` + `icp_persona` combo
3. High-converting combos → raise their `weight` in `icp_rules.vX.json`
4. Hook generator prompt gets a `high_converting_patterns` context block injected
5. Over time, the system learns that e.g. `stale_creative` + `scale_up_developer`
   converts at 22% while `no_virtual_tour` + `premium_visionary` converts at 31%

This loop is the compounding moat. The longer it runs, the harder it is to replicate.

---

## Error Handling Policy

| Failure scenario                    | Behaviour                                      |
|-------------------------------------|------------------------------------------------|
| Collector HTTP timeout (>30s)       | Mark collector `timed_out`, continue with others |
| Collector returns empty result      | Mark `no_data`, exclude signals from that source |
| Browserless connection failure      | Retry once, then mark Site Scanner `partial_failure` |
| Meta Ad Library rate limit (429)    | Exponential backoff × 3, then `rate_limited`   |
| Domain DNS not resolving            | Return early: `{ "error": "domain_unreachable" }` |
| PageSpeed API failure               | Omit speed signals, do not fail whole audit    |
| All collectors fail                 | Return `{ "error": "audit_failed", "audit_id": "..." }` — stored in Supabase for retry |
| Partial failure (3+ collectors ok)  | Return audit with `collector_errors` populated. Flag `review_status: pending_review` |
| Supabase write failure              | Return result in response body; log error; retry write async |

**Rule:** A partial audit is better than no audit. The system never throws a 500
to the caller if at least 3 collectors returned data. Errors are surfaced in
`cache_meta.collector_errors[]` and the triage status.

---

## Data Dictionary

Full field-level specification for the `audits` Supabase table and API response.

```sql
CREATE TABLE audits (
  -- Identity
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  domain                TEXT NOT NULL,
  vertical              TEXT NOT NULL DEFAULT 'proptech',
  geography             TEXT NOT NULL,          -- 'uk' | 'se'
  rules_version         TEXT NOT NULL,          -- e.g. '1.0.0'

  -- ICP Classification
  icp_persona           TEXT,   -- 'scale_up_developer' | 'premium_visionary' | 'data_driven_planner'
  icp_confidence        FLOAT,  -- 0.0–1.0

  -- Intent
  high_intent           BOOL DEFAULT FALSE,
  high_intent_reason    TEXT,

  -- Top pain signal (extracted for fast Clay queries)
  top_pain_signal       TEXT,   -- signal_id of highest-severity pain
  top_pain_severity     TEXT,   -- 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW'
  top_pain_confidence   FLOAT,

  -- Recommended action
  primary_module        TEXT,   -- 'Journey' | 'EVE3D' | 'Lemon' | 'Plot.ai' | 'Newbuilds.com'
  recommended_modules   TEXT[], -- ordered array

  -- Outbound copy
  hook_text             TEXT,
  subject_line          TEXT,
  follow_up_angle       TEXT,

  -- Key signals (extracted for filtering without JSON parsing)
  ad_creative_age_days  INT,
  has_digital_reservation BOOL,
  has_virtual_tour      BOOL,
  has_interactive_floor_plans BOOL,
  cta_type              TEXT,   -- 'enquire' | 'reserve' | 'call' | 'other'
  load_time_ms          INT,
  mobile_score          INT,    -- 0–100
  project_count         INT,
  crm_detected          TEXT,
  has_facebook_pixel    BOOL,
  has_google_tag_manager BOOL,
  analytics_platform    TEXT,
  domain_age_years      FLOAT,
  has_spf               BOOL,
  has_dkim              BOOL,
  has_dmarc             BOOL,
  days_on_market        INT,
  listing_quality_score FLOAT,
  avg_review_rating     FLOAT,
  review_count          INT,
  planning_granted_date DATE,
  development_stage     TEXT,   -- 'pre_launch' | 'active' | 'sold_out'

  -- Triage
  review_status         TEXT DEFAULT 'pending_review',
                        -- 'auto_approved' | 'pending_review' | 'flagged' | 'approved' | 'rejected'
  review_note           TEXT,
  audit_confidence      FLOAT,  -- overall audit confidence (avg of signal confidences)

  -- Full JSON (for re-analysis without re-scraping)
  pain_signals          JSONB,  -- array of PainSignal objects
  tech_stack            JSONB,
  email_infrastructure  JSONB,
  raw_collector_output  JSONB,

  -- Cache & provenance
  collected_at          TIMESTAMPTZ DEFAULT now(),
  updated_at            TIMESTAMPTZ DEFAULT now(),
  collector_errors      JSONB DEFAULT '[]',

  UNIQUE (domain, vertical)
);

CREATE INDEX idx_audits_domain       ON audits(domain);
CREATE INDEX idx_audits_review_status ON audits(review_status);
CREATE INDEX idx_audits_high_intent  ON audits(high_intent) WHERE high_intent = TRUE;
CREATE INDEX idx_audits_icp_persona  ON audits(icp_persona);
CREATE INDEX idx_audits_collected_at ON audits(collected_at);
```

---

## Future Verticals (Registry Pattern)

Adding a new vertical requires only:
1. A new folder under `verticals/` with its own collectors + rules
2. `pain_rules.v1.0.json` and `icp_rules.v1.0.json` for that vertical
3. A `normalizer_map.json` for locale-specific field normalisation
4. Registration in the global `Registry`

Zero changes to core engine, API, hook generator, or Supabase schema.
