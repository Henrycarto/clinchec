<div align="center">

# Clinchec

**Find what the payer's criteria ask for that the note does not say, before the
request is submitted.**

Prior authorization pre-screening that lives inside the physician's EHR
workflow. It reads the SOAP note the clinician already wrote, scores how
completely that note documents the payer's *current* published criteria, and
auto-populates the right form in one click.

</div>

---

## The problem

Prior authorization is the most expensive administrative process in American
medicine. A physician practice spends roughly two business days a week of staff
time on it. Around a quarter of physicians report a PA delay that led to a
serious adverse event.

Most of that cost is avoidable, and for a specific reason: **the information the
payer wants is almost always already in the note.** The clinician documented the
14 months of symptoms, the failed physical therapy, the weight-bearing films.
What they had no way of knowing is that *this* payer, for *this* CPT code,
*this month*, requires 12 weeks of documented conservative care and an explicit
laterality, and that the note says "arthroplasty" without saying "right".

So the request goes out, comes back three weeks later as a request for
additional information, and the whole loop runs again. Nobody learned anything
clinical. A field was missing.

Clinchec closes that loop before the request is ever sent, while the patient is
still in the room.

## What it does

Paste a SOAP note. In about 40 milliseconds you get:

- **Structured extraction.** Patient age and sex, chief complaint, symptom
  duration, diagnosis and procedure codes, conservative care trialled, prior
  imaging, functional impairment, red flags. Each carries a confidence
  indicator and traces back to the exact span of the note it came from.
- **A documentation score.** How completely the note documents what the payer's
  criteria ask for. It is not a probability of approval: Clinchec has never
  observed a submitted request's outcome, so it has nothing to calibrate one
  against. Banded *Well documented* (≥ 80%), *Gaps to close* (50 to 79%) or
  *Not ready to submit* (below 50%), scored against that payer's current
  published criteria for that line of business.
- **The reason for that number.** Every point decomposes into a named, signed
  driver. *"14 months documented, meeting the 12-week minimum: +13."*
  *"27447 requires a documented side: −5."*
- **What each gap is worth.** Every driver carries a counterfactual: the points
  the note would gain if that element were documented to ceiling. Gaps are
  keyed to their driver, priced by that number, ordered by it, and attributed
  to the payer clause demanding them, quoted verbatim.
- **A completed PA form**, mapped onto the right form for that payer and
  procedure, with every field labelled by where its value came from.

## The three products

### Clinchec Scan, the NLP engine

A spaCy pipeline over a curated clinical lexicon covering the highest-volume PA
categories: advanced imaging, musculoskeletal surgery, interventional pain,
sleep medicine.

It does the unglamorous things that decide whether clinical NLP is usable:

- **SOAP section segmentation**, so a diagnosis in *Assessment* is weighted
  differently from the same string in *Review of Systems*.
- **Assertion detection.** "Denies bowel incontinence" never becomes a billed
  finding. Negation scope stops at conjunctions, so "denies fever but reports
  progressive weakness" affirms the weakness.
- **Case-sensitive abbreviation handling.** Lowercase `pt` means *patient*,
  uppercase `PT` means *physical therapy*. Getting this wrong invents a course
  of conservative care that never happened.
- **Laterality resolution**, including trailing forms (*"arthroplasty, right"*),
  with side-specific ICD-10 codes rewritten to match. Laterality mismatch is one
  of the most common avoidable denial reasons.
- **Duration disambiguation.** A note mentioning "8 weeks of physical therapy"
  and "4 months of pain" reports the symptom duration, not the treatment
  duration, because that is what payer criteria mean.

### Clinchec Live, the rules engine

Payer criteria change constantly and are published as prose across hundreds of
documents. Live crawls them on a schedule and maintains a structured rules
database per insurer, procedure and line of business.

The interesting problem is not crawling. It is establishing that what was
crawled actually adjudicates anything.

- **A code in a policy's table is not a rule.** UnitedHealthcare's implanted
  spinal drug delivery policy lists CPT 62323 while adjudicating intrathecal
  pumps. A rule is emitted only when the code appears in the policy's own
  Applicable Codes table *and* an adjudication pattern fires for that procedure
  in the Coverage Rationale.
- **Some criteria are stated and then withheld.** UHC's surgical policies say a
  procedure "is proven and medically necessary in certain circumstances" and
  defer the circumstances to InterQual, which is licensed content. That is a
  third clause polarity, `DELEGATED`, carried end to end: shown to the
  clinician, never scored, and blocked by a database constraint from holding
  evidence requirements. Detection runs per adjudication rather than per
  document, because one policy can state real criteria for one procedure and
  defer another in the same file.
- **Medicare Advantage is a routing table, not a criteria set.** Its topic
  blocks point at CMS national and local determinations, at InterQual, or back
  at the commercial policy, and the pointers can be conditional on the member's
  state. All 114 are classified by whether the criteria they cite can be read
  at all.
- **Change detection is checksummed over the adjudicating fields only**,
  excluding source URL and effective date, with whitespace and ordering
  normalised. A payer republishing identical criteria at a new URL produces
  zero writes and zero alerts, because a rules feed that cries wolf nightly
  gets ignored, and the one night criteria genuinely change is the night that
  matters.
- **A rule disappearing is only evidence if the crawl was complete.** Crawls
  report whether they surveyed the payer at all, defaulting to no. Retirement
  counts absences across complete runs only, waits for three consecutively, and
  reverses the moment a rule reappears. Without that, one run of stale
  discovery paths withdraws a payer's entire rule set.

Three adapters ship, each solving a different structural problem:

| adapter | shape | state |
| --- | --- | --- |
| Aetna | numbered Clinical Policy Bulletins, criteria under a `Policy` heading | crawled live |
| UnitedHealthcare | one PDF per policy, discovered through the sitemap, with commercial and Medicare Advantage lines | crawled live |
| Blue Cross Blue Shield | 33 independent licensees, no national criteria endpoint | access registry, see below |

**No BCBS licensee publishes criteria to an unauthenticated client.** Ten state
plans were audited in August 2026, in eight registry entries. Every URL in the
previous version of the registry was dead, and the adapter reported that as a
normal empty crawl. The registry now
records what each licensee does when a crawler asks, dated, with the evidence:
provider login, click-through terms gate, client-side rendering, 403, 404, 406.
None of those is routed around. Clicking through a terms of use, spoofing a
browser user-agent past a stated block, or authenticating as a provider would
each get the data and each be the kind of thing that surfaces in a vendor
security review of a PHI-adjacent product. They sit on the commercial data
agreement track alongside the BAA and the clearinghouse contract. A licensee
becoming crawlable is a registry edit, not a code change.

The same line was drawn twice more. The CMS coverage database (LCDs and NCDs)
is reachable only through a click-through licence granting "personal use only
… non-commercial uses", identical across the web UI, the bulk export and the
API, so it was not accepted on a commercial product's behalf. That audit also
found the product storing AMA-copyright CPT descriptors, which were removed;
codes are stored, descriptions are not.

### Clinchec Forms, the auto-population engine

Maps extracted clinical data onto the correct PA form for that payer and
procedure, resolving most-specific-first: a procedure-specific form, then the
payer's catch-all, then the universal fax form.

Every field carries its provenance (**from record**, **reformatted**, **from
note**, **not found**), because auto-population a clinician cannot audit is
auto-population they have to re-read in full, which saves nothing.

It also refuses to help you fail. An invalid NPI (checked against the Luhn
check digit) is dropped rather than passed through. A required attestation the
note cannot support blocks submission with a 422 rather than going out
unchecked. When transmission is not enabled the response says so in as many
words and reports `transmitted: false`, never a silent no-op a clinician could
mistake for a filed request.

What it does *not* do yet is render or store anything. The submission result
carries an `export_url` of `/exports/<id>.pdf`, and no route serves that path;
no PDF is generated and no object is written. The mapping is real, the packet
is not.

## Technical architecture

```
   Epic / Cerner ──SMART on FHIR──► apps/web (Next.js 14, App Router)
                                          │
                        ┌─────────────────┼─────────────────┐
                        ▼                 ▼                 ▼
                  Clinchec Scan     Clinchec Live     Clinchec Forms
                  spaCy + rules     crawler + API     field mapper
                  FastAPI           FastAPI + Celery  FastAPI
                        │                 │                 │
                        └────────┬────────┴─────────────────┘
                                 ▼
                   PostgreSQL 16 + pgvector  ·  Redis
```

| Layer | Choice |
| --- | --- |
| Frontend | Next.js 14 (App Router), TypeScript, Tailwind, Radix UI |
| Backend | FastAPI, async throughout |
| Database | PostgreSQL 16 + pgvector (embedding search over payer criteria) |
| NLP | spaCy clinical pipeline; GPT-4o for justification drafting |
| Auth | SMART on FHIR OAuth 2.0 + PKCE (Epic / Cerner compatible) |
| Queue | Redis + Celery |
| Infra | AWS ECS Fargate, RDS, ElastiCache, S3, all in Terraform |

Four decisions worth calling out:

**The score is a rule engine, not a model, on purpose.** A supervised
approval-prediction model needs thousands of submitted requests paired with
payer outcomes, and Clinchec has to be useful before it can collect them. The
rule engine is built to *become* that dataset: every score decomposes into
named drivers with signed deltas, which is exactly the feature vector a model
trains on. It also has a property a model would not: a clinician can read why
the score is what it is and fix the note. An opaque 43% changes nobody's
behaviour.

**The metric claims only what it can support.** It was called "approval
likelihood" until the name was audited against the system behind it. Nothing in
Clinchec has ever seen a payer decision, so there is no outcome data to
calibrate a probability against. It is a documentation score, and the band
labels describe the note rather than predicting a decision. Driver
counterfactuals are stated in points, which is arithmetic over weights visible
on the same screen, and never in percentage points of approval.

**PHI surface is minimised by construction.** Note text is never persisted,
only a SHA-256 digest and the de-identified structured extraction. It is never
logged. The GPT-4o drafter is prompted with the extracted fact sheet, never the
raw note, which bounds what leaves the process to coded facts the payer will
see anyway. Its output is checked against those facts before being returned; a
draft containing a number we did not supply is discarded for the deterministic
template.

**Every response uses one envelope.** `{ data, error, meta }`, including
validation failures and unhandled exceptions. The Pydantic models are mirrored
as Zod schemas that the frontend validates against, so version skew between a
deployed service and a deployed frontend surfaces as a named error at the
boundary rather than as `undefined` rendering inside a clinical judgement. The
two directions of that mirror fail differently and both are now asserted in CI:
a missing enum value fails the whole parse, and a missing field is silently
stripped.

Deeper detail: [`docs/architecture.md`](docs/architecture.md),
[`docs/fhir-integration.md`](docs/fhir-integration.md),
[`docs/handover.md`](docs/handover.md),
`docs/payer-adapter-guide.md` in
[`clinchec-core-live`](https://github.com/Henrycarto/clinchec-core-live) (private).

## Running it locally

**Prerequisites:** Docker + Docker Compose, Node 20+, Python 3.12+.

```bash
git clone <repo> clinchec && cd clinchec
cp .env.example .env

# Generate a session key
openssl rand -base64 32   # paste into SESSION_SECRET

npm install
npm run stack:up
```

That brings up Postgres (with pgvector), Redis, all three services, the Celery
worker and beat scheduler, and the Next.js app:

| | |
| --- | --- |
| Web app | <http://localhost:3000> |
| Scan API + docs | <http://localhost:8001/docs> |
| Live API + docs | <http://localhost:8002/docs> |
| Forms API + docs | <http://localhost:8003/docs> |

Open the app, press **Load sample**, then **Scan**.

Schema is applied by a migration runner, not by container-init SQL, so an
existing database is upgraded rather than skipped. Migrations live in
`services/live/migrations/`, are tracked in a `schema_migrations` table with a
checksum per file, run under a Postgres advisory lock so concurrent tasks
cannot race, and refuse to proceed if an already-applied file has changed on
disk. The stack runs them once in a dedicated `migrate` container that Live,
the worker and the beat scheduler all wait on
(`condition: service_completed_successfully`), so nothing serves against a
schema that has not been brought up to date. To run it by hand:

```bash
docker compose -f infra/docker-compose.yml run --rm migrate
```

In AWS the same entry point runs as a one-off ECS task
(`infra/ecs/taskdef-migrate.json`), and its exit code gates both the service
rollout and the web deployment.

SMART on FHIR is optional for local development. Scan, Live and Forms have no
FHIR dependency, so you can work against them by pasting notes directly. To
exercise the real launch, register an app in the
[Epic sandbox](https://fhir.epic.com/) and fill in the `FHIR_*` variables; the
login page tells you if they are missing.

To open the clinician UI without registering anything, set
`DEV_AUTH_BYPASS=true` in `.env`. This mints a session that never touched an
EHR, and it is deliberately hard to misuse: it is refused outright when
`NODE_ENV=production`, its route returns **404** while disabled so production
never advertises it, the session carries a placeholder token pointed at an
unresolvable `.invalid` host, and every page shows a permanent warning banner.
The guard has its own tests and a dedicated CI assertion. Details in
[`docs/fhir-integration.md`](docs/fhir-integration.md#working-on-the-ui-without-an-ehr).

### Without Docker

```bash
cd services/scan
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm
uvicorn app.main:app --reload --port 8001
```

Same shape for `services/live` (port 8002) and `services/forms` (8003), then
`npm run dev --workspace apps/web`.

### Try the API directly

```bash
curl -s http://localhost:8001/extract \
  -H 'Content-Type: application/json' \
  -d '{
    "note": "S: 62 y/o female with right knee pain for 14 months. Failed 12 weeks of physical therapy and a corticosteroid injection. Unable to climb stairs.\n\nO: Weight-bearing x-ray shows bone-on-bone medial narrowing.\n\nA: Right knee osteoarthritis.\n\nP: Total knee arthroplasty, right.",
    "payer_slug": "aetna",
    "plan_type": "commercial"
  }' | jq '.data.approval'
```

`plan_type` matters: UnitedHealthcare states its own criteria for a knee
replacement commercially and routes the Medicare Advantage version to a CMS
determination. Omitting it leaves the rules service to pick by recency.

### Tests

```bash
npm test                                    # frontend typecheck + lint
cd services/scan  && pytest -q              # 109 tests
cd services/live  && pytest -q              # 139 tests
cd services/forms && pytest -q              # 30 tests
```

278 tests, no network required. Payer adapter tests run against captured
fixtures of real policy documents, kept whole rather than trimmed to the passage
under test, so a parser that only works on the excerpt fails. The parser tests
run against real note prose and assert the behaviours that actually matter,
negation scope, abbreviation case, duration disambiguation, laterality, rather
than mock plumbing.

## Where it stands

Stated plainly, because the distinction matters more to an investor than a
feature list.

### Working and verified

- **Clinchec Scan, end to end.** Extraction, code resolution, scoring,
  counterfactuals and justification drafting are complete and tested, with
  every number traceable to a driver and every driver to a span of the note.
- **Live crawling of two payers.** 259 commercial and 55 Medicare Advantage
  UnitedHealthcare policies surveyed, plus the Aetna bulletin index, with 21
  rules extracted from the subset that actually adjudicates. Most payer policy
  text adjudicates nothing, and the filtering that establishes that is the
  work.
- **The BCBS access registry.** Ten state plans audited with dated evidence,
  re-checked on every run, so an access change is visible rather than silent.
- **The SMART on FHIR auth layer.** Real OAuth 2.0 with mandatory endpoint
  discovery, S256 PKCE, `aud` binding, constant-time state comparison, an `iss`
  allowlist, encrypted JWE session cookies and automatic token refresh. Not
  mocked, not stubbed. It has never handshaken with a real EHR.
- **The payer adapter framework.** Rate limiting, robots.txt, per-payer error
  isolation, checksum change detection, an append-only revision log and the
  completeness-gated retirement path.
- **Clinchec Forms' mapping and validation.** Form resolution, field mapping,
  provenance labelling, NPI check-digit validation and the incomplete-submission
  gate.
- **The response envelope and its Zod mirror**, across every service, asserted
  in both directions.
- **Infrastructure, written and validating but never applied.** Terraform for a
  three-tier VPC (public ALB, private tasks, isolated data with no internet
  route), KMS-encrypted RDS Multi-AZ and ElastiCache, per-service IAM roles, VPC
  flow logs with 365-day retention, ECS with deployment circuit breakers, a
  pre-deploy migration task gating both the service and the web rollout, and a
  CI/CD pipeline using OIDC federation rather than long-lived AWS keys. No AWS
  account has seen it.

### Not done

- **Clinical accuracy is unvalidated.** Driver weights are reasoned, not
  measured. Validating them needs a licensed clinician and a corpus of real
  notes with known outcomes. This is the single most important open item, and
  no amount of further engineering substitutes for it.
- **Crawling defaults to seed mode.** `OFFLINE_SEED_MODE=true` serves
  transcribed criteria; setting it false runs the live crawl. Aetna and UHC
  have had hand-reviewed live runs. Anything added after them needs one before
  it is trusted.
- **Submission neither transmits nor renders.** Forms produces a complete,
  validated field mapping and says explicitly that nothing was sent. It does
  not yet write the PDF its `export_url` points at, and no route serves that
  path. Real transmission additionally needs per-payer portal credentials, an
  executed BAA, and an X12 278 clearinghouse contract. Those last three are
  commercial gates, not engineering ones; the export renderer is not.
- **The clinical NER model is lexicon-driven.** Roughly 40 ICD-10 and 30 CPT
  surface forms with a statistical noun-chunk fallback. High precision on the
  codes that drive PA volume; the long tail is surfaced as low-confidence
  rather than resolved. A fine-tuned clinical model swaps in behind
  `SPACY_CLINICAL_MODEL` with no code change.
- **pgvector is provisioned but unused.** The column, dimensions and HNSW index
  exist; nothing queries them yet. That is the seam for bootstrapping a new
  payer's rules from an existing one's similar criteria.
- **`pa_requests` is a table and nothing else.** `001_init.sql` creates it and
  indexes it; no service reads or writes it. Forms mints a request id per
  submission and returns it without persisting a row, so a PA request does not
  outlive its response. The detail page validates the identifier and reports
  that the record is not retrievable rather than rendering a fabricated one.
- **`apps/dashboard`,** the practice admin view, is a documented placeholder. It
  gets built once there are submitted outcomes to analyse.

### Known gaps, named

- `fhirUser` is read from the `id_token` without JWKS signature verification.
  Display only; flagged in code. Must be verified before any path grants access
  on that claim.
- Sign-out clears the local session but does not call the EHR's
  `revocation_endpoint`.
- No Da Vinci CRD/DTR support. That IG is the standards-track answer to this
  problem and payers are slowly adopting it; the seam exists
  (`SubmissionChannel.FHIR_CRD`). Portal scraping is what works today.

## Repository layout

```
apps/
  web/                  Next.js 14 clinician app
  dashboard/            Practice admin view (placeholder)
services/
  scan/                 Clinical NLP extraction + scoring
    app/models/         → submodule: clinchec-core-scan   (private)
  live/                 Payer crawler, rules DB, Celery tasks
    app/migrations.py   Migration runner (advisory lock, checksums)
    migrations/         Numbered SQL, tracked in schema_migrations
    app/payers/         → submodule: clinchec-core-live   (private)
  forms/                PA form registry + field mapper
    app/mappers/        → submodule: clinchec-core-forms  (private)
packages/
  shared-types/         Zod mirrors of every service contract
  fhir-client/          SMART on FHIR + narrow FHIR R4 client
infra/
  terraform/            VPC, ECS, RDS, ElastiCache, S3, IAM
  ecs/                  Task definitions, including the pre-deploy migrate task
  docker-compose.yml    Full local stack
docs/                   Architecture, FHIR integration, handover
```

### Open scaffold, private core

Three directories are git submodules pointing at private repositories:

| path | repository | what it holds |
| --- | --- | --- |
| `services/scan/app/models/` | `clinchec-core-scan` | clinical NLP pipeline, ICD-10/CPT lexicon, scoring engine |
| `services/live/app/payers/` | `clinchec-core-live` | payer adapter framework and adapter methodology |
| `services/forms/app/mappers/` | `clinchec-core-forms` | PA form registry and field mapping engine |

Everything else is open: the clinician UI, the SMART on FHIR client, the
response envelope, the migration runner, the infrastructure and the CI
pipeline. The split is at the package boundary, so imports and Docker builds are
identical either way. `from app.models.lexicon import …` resolves the same
whether or not you have access to the submodule.

CI runs in two modes and reports which. A green run without the private cores
has not tested the cores. A change that moves a symbol between a public and a
private repo has to land in the repo that *defines* it first, or the other
repo's CI goes red against a symbol that does not exist yet.

**Cloning without access to the private repos still gives you a working
skeleton.** The web app, Terraform, the FHIR client and all three service
scaffolds build and typecheck. The services will not start, because the modules
that do the clinical work are not there.

```bash
git clone --recurse-submodules https://github.com/Henrycarto/clinchec.git
```

---

<div align="center">

**Clinchec is clinical decision support.** It does not make coverage
determinations, and the ordering clinician remains responsible for the codes and
clinical content of every submitted request.

</div>
