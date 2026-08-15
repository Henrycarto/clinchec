# Clinchec — architecture

## The problem this shape solves

A prior authorization is decided on evidence the clinician has already
documented. The note usually contains the diagnosis, the duration, the failed
conservative care and the functional impact. What it does not contain is any
signal about whether *this payer, for this procedure, this month* considers that
sufficient.

So the work splits into three questions, and the services map one-to-one onto
them:

| Question | Service |
| --- | --- |
| What does this note actually say? | **Clinchec Scan** |
| What does this payer currently require? | **Clinchec Live** |
| How do I get that onto the payer's form? | **Clinchec Forms** |

Keeping these separate is not microservice fashion. They have genuinely
different runtime shapes — Scan is CPU-bound and latency-critical, Live is a
long-running crawler on a schedule, Forms is I/O-bound and stateless — and
different blast radii. A payer redesigning their portal must not be able to slow
down a clinician's scan.

## System diagram

```
                        ┌──────────────────────────────┐
   Epic / Cerner ──────► │  apps/web (Next.js 14)       │
   SMART launch          │  server components + routes  │
                        └───────┬──────────────┬───────┘
                                │              │
                   POST /extract│              │POST /populate, /submit
                                ▼              ▼
                     ┌────────────────┐  ┌────────────────┐
                     │ Clinchec Scan  │  │ Clinchec Forms │
                     │ spaCy + rules  │  │ field mapper   │
                     └───────┬────────┘  └───────┬────────┘
                             │                   │
              GET /rules/{payer}/{cpt}           │
                             ▼                   ▼
                     ┌──────────────────────────────────┐
                     │ Clinchec Live (FastAPI read API) │
                     └────────────┬─────────────────────┘
                                  │
              ┌───────────────────┴───────────────────┐
              ▼                                       ▼
     ┌──────────────────┐                   ┌──────────────────┐
     │ PostgreSQL       │                   │ Redis            │
     │ + pgvector       │◄──── Celery ──────│ broker + results │
     └──────────────────┘      worker/beat  └──────────────────┘
                                  │
                                  ▼
                         Payer portals (Aetna,
                         BCBS licensees, UHC)
```

## Request path: one scan

1. The clinician presses **Scan**. `apps/web` posts the note to Scan.
2. Scan segments the note into SOAP sections, then runs two `PhraseMatcher`
   passes over a curated clinical lexicon plus a noun-chunk fallback for the
   long tail. Assertion status (negation, hedging) is resolved with a
   NegEx-style sentence-scoped trigger scan.
3. Matched entities resolve to ICD-10-CM and CPT candidates, with laterality
   detected from surrounding text and side-specific codes rewritten to match.
4. If the caller named a payer, Scan asks Live for that payer's current criteria
   for the requested CPT. **Live being unavailable never fails a scan** — the
   rule engine falls back to the national baseline and reports which basis it
   used.
5. The rule engine produces a score in `[0, 1]` with a signed, named
   contribution per criterion.
6. The response comes back in the standard envelope; the UI bands it green
   (≥ 80%), amber (50–79%) or red (< 50%).

Typical end-to-end latency is 25–60 ms for a full-length note, dominated by
spaCy tokenisation. The model is loaded once at process start, not per request.

## Why the score is a rule engine, not a model

There is no labelled dataset yet. A supervised approval-prediction model needs
thousands of submitted requests paired with payer outcomes, and Clinchec has to
be useful before it can collect them.

The rule engine is designed to be the thing that collects them. Every score
decomposes into named drivers with signed deltas — `duration`, `conservative_care`,
`indication` — and those drivers are exactly the feature vector a model would
train on. When there are enough labelled outcomes, `basis` flips from
`rule_engine` to `ml_model` and the wire contract does not change.

It also has a property a model would not: a clinician can read *why* the score
is what it is and fix the note. An opaque 43% changes no behaviour.

## Data model notes

Full schema: `services/live/migrations/001_init.sql`. Migrations are
applied by `services/live/app/migrations.py` — at Live's startup, and by a
one-shot `migrate` step the rest of the stack waits on. Live is the only
service with database access, which is why it owns them.

- **`scans` stores no note text.** Only a SHA-256 digest for deduplication, the
  character count, and the de-identified structured extraction. The note itself
  exists in the EHR, which is already the system of record; copying it here
  would double the PHI surface for no benefit.
- **`payer_rules.criteria_embedding` is a 1536-dimension pgvector column** with
  an HNSW index. This backs similarity search over criteria prose — "which
  payers word this requirement the same way" — which is how a new payer's rules
  get bootstrapped from an existing one rather than parsed from scratch.
- **`payer_rule_revisions` is append-only.** When criteria change, the diff is
  recorded. This is what lets a practice answer "was this denial correct under
  the rule in force on the submission date".
- **`audit_events`** satisfies HIPAA §164.312(b). Every read of PHI writes a
  row.

## The response envelope

Every endpoint on every service returns:

```json
{ "data": {...} | null, "error": {"code","message","details"} | null, "meta": {...} }
```

including validation failures and unhandled exceptions. The frontend therefore
has exactly one unwrap path, and error handling never depends on reading a
status code correctly. `meta.request_id` propagates from the browser through
every service, so one clinician action is traceable end to end.

The Pydantic models are mirrored as Zod schemas in `packages/shared-types`, and
`apps/web/lib/api.ts` parses every response through them. A schema drift between
a deployed service and a deployed frontend surfaces as a named
`schema_mismatch` error at the boundary rather than as `undefined` rendering
inside a component that displays a clinical judgement.

## Failure behaviour

The design principle is that degradation must be visible and honest.

| Failure | Behaviour |
| --- | --- |
| Live unreachable | Scan scores against the national baseline, reports `basis: "rule_engine"` |
| spaCy model missing | Blank pipeline with sentencizer; lexicon matching still works, noun-chunk fallback goes quiet, `/health` reports `degraded` |
| One payer's portal changes markup | That adapter's parse yields no rules rather than junk; the other payers sync normally |
| OpenAI unavailable or hallucinating | Justification falls back to the deterministic template drafter |
| Forms has no payer credentials | Produces a downloadable packet and says explicitly that nothing was transmitted |

That last one matters most. A clinician who believes a request was filed when it
was not will find out weeks later, when the patient calls.

## Security posture

- **No credentials of our own.** Identity comes from the EHR over SMART on
  FHIR. Clinchec never holds a clinician password, so access is governed by the
  hospital's directory, MFA and offboarding. See `docs/fhir-integration.md`.
- **Tokens never reach the browser.** The SMART session lives in an encrypted
  JWE cookie (A256GCM, httpOnly). Encryption rather than signing, because the
  payload contains a bearer token and a patient identifier.
- **Note text is never logged.** Access logs record method, path, status and
  duration. The `X-Request-ID` is the correlation key.
- **The LLM sees structured facts, not prose.** The GPT-4o drafter is prompted
  with the extracted fact sheet only. That bounds what leaves the process to
  coded facts the payer will see anyway, and its output is checked against the
  supplied facts before being returned.
- **Three network tiers in AWS.** The ALB is public; ECS tasks are private; RDS
  and ElastiCache are in isolated subnets with no route to the internet at all.

## What is deliberately not built yet

- Real submission to payer portals (needs per-payer credentials and an executed
  BAA)
- The `pa_requests` read path
- pgvector similarity search (the column and index exist; nothing queries them)
- `apps/dashboard`

See the README for the honest production-readiness breakdown.
