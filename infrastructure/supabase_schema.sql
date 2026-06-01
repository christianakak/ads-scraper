-- GTM Intelligence Engine — Supabase Schema
-- Run this in the Supabase SQL editor to set up the database.
-- All tables include RLS policies for the Lambda service key.

-- ---------------------------------------------------------------------------
-- audits
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS audits (
  -- Identity
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  domain                      TEXT NOT NULL,
  vertical                    TEXT NOT NULL DEFAULT 'proptech',
  geography                   TEXT NOT NULL,            -- 'uk' | 'se'
  rules_version               TEXT NOT NULL,            -- e.g. '1.0.0'

  -- ICP Classification
  icp_persona                 TEXT,    -- 'scale_up_developer' | 'premium_visionary' | 'data_driven_planner'
  icp_confidence              FLOAT,   -- 0.0–1.0

  -- Intent
  high_intent                 BOOL DEFAULT FALSE,
  high_intent_reason          TEXT,

  -- Top pain signal (extracted for fast Clay queries — no JSON parsing needed)
  top_pain_signal             TEXT,
  top_pain_severity           TEXT,    -- 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW'
  top_pain_confidence         FLOAT,

  -- Recommended action
  primary_module              TEXT,    -- 'Journey' | 'EVE3D' | 'Lemon' | 'Plot.ai' | 'Newbuilds.com'
  recommended_modules         TEXT[],

  -- Outbound copy
  hook_text                   TEXT,
  subject_line                TEXT,
  follow_up_angle             TEXT,

  -- Key signals extracted for Clay column mapping
  ad_creative_age_days        INT,
  has_digital_reservation     BOOL,
  has_virtual_tour            BOOL,
  has_interactive_floor_plans BOOL,
  cta_type                    TEXT,    -- 'enquire' | 'reserve' | 'call' | 'other'
  load_time_ms                INT,
  mobile_score                INT,     -- 0–100
  project_count               INT,
  crm_detected                TEXT,
  has_facebook_pixel          BOOL,
  has_google_tag_manager      BOOL,
  analytics_platform          TEXT,
  domain_age_years            FLOAT,
  has_spf                     BOOL,
  has_dkim                    BOOL,
  has_dmarc                   BOOL,
  days_on_market              INT,
  listing_quality_score       FLOAT,
  avg_review_rating           FLOAT,
  review_count                INT,
  planning_granted_date       DATE,
  development_stage           TEXT,    -- 'pre_launch' | 'active' | 'sold_out'

  -- Triage
  review_status               TEXT NOT NULL DEFAULT 'pending_review',
                                       -- 'auto_approved' | 'pending_review' | 'flagged' | 'approved' | 'rejected'
  review_note                 TEXT,
  audit_confidence            FLOAT,

  -- Full JSON blobs (for re-analysis without re-scraping)
  pain_signals                JSONB DEFAULT '[]',
  tech_stack                  JSONB DEFAULT '{}',
  email_infrastructure        JSONB DEFAULT '{}',
  raw_collector_output        JSONB DEFAULT '{}',

  -- Full report JSON — enables cache retrieval without re-scraping
  full_audit_json             JSONB,

  -- Cache & provenance
  collected_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
  collector_errors            JSONB DEFAULT '[]',

  UNIQUE (domain, vertical)
);

-- Auto-update updated_at on every write
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audits_updated_at
  BEFORE UPDATE ON audits
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Indexes
CREATE INDEX IF NOT EXISTS idx_audits_domain         ON audits(domain);
CREATE INDEX IF NOT EXISTS idx_audits_review_status  ON audits(review_status);
CREATE INDEX IF NOT EXISTS idx_audits_high_intent    ON audits(high_intent) WHERE high_intent = TRUE;
CREATE INDEX IF NOT EXISTS idx_audits_icp_persona    ON audits(icp_persona);
CREATE INDEX IF NOT EXISTS idx_audits_collected_at   ON audits(collected_at DESC);
CREATE INDEX IF NOT EXISTS idx_audits_vertical       ON audits(vertical);


-- ---------------------------------------------------------------------------
-- outcomes (the learning loop feed)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS outcomes (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  audit_id    UUID NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
  outcome     TEXT NOT NULL CHECK (outcome IN ('meeting_booked', 'uninterested', 'no_reply')),
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  notes       TEXT
);

CREATE INDEX IF NOT EXISTS idx_outcomes_audit_id ON outcomes(audit_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_outcome  ON outcomes(outcome);


-- ---------------------------------------------------------------------------
-- Row-level security
-- Sets up permissive policies for the Lambda service key.
-- Replace 'service_role' with your actual role if different.
-- ---------------------------------------------------------------------------

ALTER TABLE audits ENABLE ROW LEVEL SECURITY;
ALTER TABLE outcomes ENABLE ROW LEVEL SECURITY;

-- Allow service role full access
CREATE POLICY "service_role_audits" ON audits
  FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "service_role_outcomes" ON outcomes
  FOR ALL TO service_role USING (true) WITH CHECK (true);
