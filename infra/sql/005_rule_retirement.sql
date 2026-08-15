-- ---------------------------------------------------------------------------
-- Retirement: a rule whose source document stopped existing.
--
-- `payer_rules` only ever accumulated. A crawl wrote what it found and left
-- everything else alone, which is right for a payer that was briefly
-- unreachable and wrong for a policy that was withdrawn. Three UnitedHealthcare
-- rules made the difference concrete:
--
--     72148  source_url .../coverage-determination-guidelines.html   404s
--     73721  source_url .../coverage-determination-guidelines.html   404s
--     E0601  source_url .../coverage-determination-guidelines.html   404s
--
-- All three cite a path the August 2026 validation found dead, all three read
-- as hours-fresh because an offline seed touched them, and E0601 asserts
-- criteria for a code UnitedHealthcare publishes none for. Nothing in the
-- schema could tell "we re-verified this today" from "nothing has looked at
-- this since its source disappeared".
--
-- The hard part is not retiring, it is not retiring the wrong things. A crawl
-- produces nothing for many reasons that have no bearing on whether a policy
-- still exists: the payer was down, discovery returned stale paths, the
-- per-run page cap truncated the document list, the run was an offline seed.
-- The August validation is the cautionary case — UHC discovery returned four
-- stale paths and parsed nothing, and a naive "absent means gone" rule would
-- have retired the entire UnitedHealthcare rule set on the strength of it.
--
-- So absence is counted, not acted on. `missed_crawls` increments only on a
-- crawl that was complete — succeeded, not capped, no fetch failures, not a
-- seed run — and retirement waits for several of those in a row. Any crawl
-- that produces the rule again resets the counter and clears `retired_at`,
-- because a payer republishing a policy should bring its rule back rather than
-- leave a tombstone.
-- ---------------------------------------------------------------------------

ALTER TABLE payer_rules
    ADD COLUMN IF NOT EXISTS missed_crawls INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS retired_at    TIMESTAMPTZ;

COMMENT ON COLUMN payer_rules.missed_crawls IS
    'Consecutive COMPLETE crawls that did not produce this rule. Reset to 0 '
    'whenever a crawl produces it. Only complete crawls count: a capped, '
    'failed, or offline-seed run says nothing about whether the policy exists.';

COMMENT ON COLUMN payer_rules.retired_at IS
    'When the source document was concluded gone. A retired rule is not served '
    'to Scan — it is withheld rather than deleted, so the decision is visible '
    'and reversible, and so a payer republishing the policy un-retires it.';

-- Reads filter on it constantly; retirement is rare. Index the common case.
CREATE INDEX IF NOT EXISTS idx_payer_rules_active
    ON payer_rules (payer_id, cpt_code, plan_type)
    WHERE retired_at IS NULL;
