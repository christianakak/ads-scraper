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
│   │   ├── collector.py           # BaseCollector ABC
│   │   ├── analyzer.py            # BaseAnalyzer ABC
│   │   ├── schemas.py             # Pydantic: CollectorResult, PainSignal, AuditReport
│   │   └── registry.py            # Maps vertical slug → collectors + rules
│   ├── engine.py                  # DomainAuditor — parallel async orchestration
│   └── hook_generator.py          # Claude API, persona-aware prompt builder
│
├── verticals/
│   └── proptech/                  # Add saas/, fintech/ here for new verticals
│       ├── collectors/
│       │   ├── ad_intelligence.py # Meta Ad Library + Google Ads Transparency
│       │   ├── site_scanner.py    # Playwright: CTA, floor plans, reservation flow
│       │   ├── portal_quality.py  # Rightmove (UK) + Hemnet (SE) listing scanner
│       │   ├── planning_intel.py  # UK Planning Portal + Swedish Lantmäteriet
│       │   └── social_review.py   # Google Places, Trustpilot, HomeViews
│       ├── analyzers/
│       │   ├── pain_mapper.py     # Signal → {pain, emotional_trigger, m360_module}
│       │   ├── icp_classifier.py  # Score signals → Scale-Up / Premium / Planner
│       │   └── benchmarks.py      # Industry baseline values
│       └── rules/
│           ├── pain_rules.json    # Diagnostic logic as config (hot-swappable)
│           └── icp_rules.json     # ICP classification weights
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
│   └── supabase_schema.sql        # Table definitions + indexes
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

  "pain_signals": [
    {
      "signal_id": "stale_creative",
      "severity": "HIGH",
      "detected_value": { "creative_age_days": 47 },
      "business_pain": "...",
      "emotional_trigger": "...",
      "m360_module": "Lemon"
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

  "cache_meta": {
    "collected_at": "2026-01-01T00:00:00Z",
    "cache_hit": false,
    "collectors_run": ["dns_headers", "ad_intelligence", "site_scanner",
                       "portal_quality", "planning_intel", "social_review"]
  },

  "raw_collector_output": {},

  "clay_flat": {
    "icp_persona": "scale_up_developer",
    "icp_confidence": 0.84,
    "high_intent": true,
    "top_pain_signal": "stale_creative",
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

GET  /v1/audit/{audit_id}
  Response: Retrieve stored audit from Supabase

POST /v1/audit/batch
  Body:     { "domains": ["a.com", "b.com"], "vertical": "proptech" }
  Response: { "job_id": "uuid", "status": "queued" }

GET  /v1/audit/batch/{job_id}
  Response: Batch status + results array

GET  /health
```

Collectors run in parallel via `asyncio.gather()`. Target latency: 15–25s.
Clay HTTP Enrichment timeout: 45s. Lambda timeout: 60s.

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
| 8     | SAM infra, CLI tool, env config, end-to-end integration test       | Days 17–18|

---

## Future Verticals (Registry Pattern)

Adding a new vertical requires only:
1. A new folder under `verticals/` with its own collectors + rules
2. A `pain_rules.json` and `icp_rules.json` for that vertical
3. Registration in the global `Registry`

Zero changes to core engine, API, or hook generator.
