-- ---------------------------------------------------------------------------
-- Delegated criteria: the payer adjudicates, but publishes no criteria.
--
-- Found while building UnitedHealthcare live crawling. UHC's "Surgery of the
-- Knee" commercial medical policy states that knee surgery "is proven and
-- medically necessary in certain circumstances" and then says, in full:
--
--     For medical necessity clinical coverage criteria, refer to the
--     InterQual(R) CP: Procedures
--
-- InterQual is licensed third-party content. The criteria that decide a knee
-- arthroplasty request are therefore not in the document, and they are not
-- anywhere else we can read either. The same construction appears across UHC's
-- surgical policies, and MCG appears in the equivalent role at other payers.
--
-- The failure mode this column prevents is specific. Without it, the parser
-- reads the Coverage Rationale section, finds a medical-necessity sentence,
-- and stores the deferral paragraph as `criteria_text` for CPT 27447. The
-- record then looks exactly like a real rule: it has a source URL, a recent
-- verification timestamp, and prose containing "medically necessary". Scan
-- scores against it and returns a confidence number for a determination whose
-- criteria nobody involved has seen.
--
-- So delegation is recorded as what it is. A delegated clause is surfaced to
-- the clinician — knowing the review runs against InterQual is genuinely useful
-- when preparing a submission — and is never selected for scoring.
-- ---------------------------------------------------------------------------

ALTER TABLE payer_rule_clauses
    DROP CONSTRAINT IF EXISTS payer_rule_clauses_polarity_check;

ALTER TABLE payer_rule_clauses
    ADD CONSTRAINT payer_rule_clauses_polarity_check
    CHECK (polarity IN ('covered', 'excluded', 'delegated'));

COMMENT ON COLUMN payer_rule_clauses.polarity IS
    'covered: this indication is approvable on evidence. '
    'excluded: this indication is denied regardless of evidence. '
    'delegated: the payer adjudicates this procedure against criteria it does '
    'not publish (InterQual, MCG). Shown to the clinician, never scored.';

-- A delegated clause carries no evidence requirements by construction: there is
-- no published evidence standard to carry. Enforced rather than assumed, because
-- a parser that silently attached duration or conservative-care requirements to
-- one would be inventing the very thing this column exists to say we lack.
ALTER TABLE payer_rule_clauses
    DROP CONSTRAINT IF EXISTS payer_rule_clauses_delegated_no_criteria;

ALTER TABLE payer_rule_clauses
    ADD CONSTRAINT payer_rule_clauses_delegated_no_criteria
    CHECK (
        polarity <> 'delegated'
        OR (
            required_duration_weeks IS NULL
            AND COALESCE(array_length(required_conservative_care, 1), 0) = 0
            AND COALESCE(array_length(required_imaging, 1), 0) = 0
        )
    );
