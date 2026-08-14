<div align="center">

# Clinchec

**Know whether a prior authorization will be approved — before you submit it.**

AI-powered prior authorization pre-screening that lives inside the physician's
EHR workflow. It reads the SOAP note the clinician already wrote, predicts
approval likelihood against the payer's *current* criteria, and auto-populates
the right form in one click.

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
laterality — and that the note says "arthroplasty" without saying "right".

So the request goes out, comes back three weeks later as a request for
additional information, and the whole loop runs again. Nobody learned anything
clinical. A field was missing.

Clinchec closes that loop before the request is ever sent, while the patient is
still in the room.

## What it does

Paste a SOAP note. In about 40 milliseconds you get:

- **Structured extraction** — patient age and sex, chief complaint, symptom
  duration, diagnosis and procedure codes, conservative care trialled, prior
  imaging, functional impairment, red flags. Each with a confidence indicator,
  and each traceable back to the exact span of the note it came from.
- **An approval likelihood** — a single number, banded green (≥ 80%), amber
  (50–79%) or red (< 50%), scored against that payer's current published
  criteria.
- **The reason for that number** — every point decomposes into a named,
  signed driver. *"14 months documented, meeting the 12-week minimum: +13."*
  *"27447 requires a documented side: −5."*
- **A prioritised fix list** — the specific documentation that would move the
  score, ordered by impact.
- **A completed PA form** — mapped onto the right form for that payer and
  procedure, with every field labelled by where its value came from.

## The three products

### Clinchec Scan — the NLP engine

A spaCy pipeline over a curated clinical lexicon covering the highest-volume PA
categories: advanced imaging, musculoskeletal surgery, interventional pain,
sleep medicine.

It does the unglamorous things that decide whether clinical NLP is usable:

- **SOAP section segmentation**, so a diagnosis in *Assessment* is weighted
  differently from the same string in *Review of Systems*.
- **Assertion detection** — "denies bowel incontinence" never becomes a billed
  finding. Negation scope stops at conjunctions, so "denies fever but reports
  progressive weakness" affirms the weakness.
- **Case-sensitive abbreviation handling** — lowercase `pt` means *patient*,
  uppercase `PT` means *physical therapy*. Getting this wrong invents a course
  of conservative care that never happened.
- **Laterality resolution**, including trailing forms (*"arthroplasty, right"*),
  with side-specific ICD-10 codes rewritten to match. Laterality mismatch is one
  of the most common avoidable denial reasons.
- **Duration disambiguation** — a note mentioning "8 weeks of physical therapy"
  and "4 months of pain" reports the symptom duration, not the treatment
  duration, because that is what payer criteria mean.

### Clinchec Live — the rules engine

Payer criteria change constantly and are published as prose across hundreds of
documents. Live crawls them on a schedule and maintains a structured rules
database per insurer and procedure.

The interesting problem is not crawling; it is **knowing when something actually
changed.** Rules are checksummed over the adjudicating fields only, excluding
source URL and effective date, with whitespace and ordering normalised. A payer
republishing identical criteria at a new URL produces zero writes and zero
alerts — because a rules feed that cries wolf nightly gets ignored, and the one
night criteria genuinely change is the night that matters.

Three adapters ship, each solving a different structural problem: Aetna
(numbered policy bulletins), UnitedHealthcare (a machine-readable PA list plus
separate narrative guidelines), and Blue Cross Blue Shield — which is not one
payer at all, but 33 independent licensees with no national criteria endpoint.

### Clinchec Forms — the auto-population engine

Maps extracted clinical data onto the correct PA form for that payer and
procedure, resolving most-specific-first: a procedure-specific form, then the
payer's catch-all, then the universal fax form.

Every field carries its provenance — **from record**, **reformatted**, **from
note**, or **not found** — because auto-population a clinician cannot audit is
auto-population they have to re-read in full, which saves nothing.

It also refuses to help you fail. An invalid NPI (checked against the Luhn
check digit) is dropped rather than passed through. A required attestation the
note cannot support blocks submission rather than going out unchecked. And when
payer credentials are not configured, it produces a downloadable packet and says
so explicitly — never a silent no-op a clinician could mistake for a filed
request.

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
| Infra | AWS ECS Fargate, RDS, ElastiCache, S3 — Terraform |

Three decisions worth calling out:

**The score is a rule engine, not a model — on purpose.** A supervised
approval-prediction model needs thousands of submitted requests paired with
payer outcomes, and Clinchec has to be useful before it can collect them. The
rule engine is built to *become* that dataset: every score decomposes into named
drivers with signed deltas, which is exactly the feature vector a model trains
on. It also has a property a model would not — a clinician can read why the
score is what it is and fix the note. An opaque 43% changes nobody's behaviour.

**PHI surface is minimised by construction.** Note text is never persisted —
only a SHA-256 digest and the de-identified structured extraction. It is never
logged. The GPT-4o drafter is prompted with the extracted fact sheet, never the
raw note, which bounds what leaves the process to coded facts the payer will see
anyway. Its output is checked against those facts before being returned; a draft
containing a number we did not supply is discarded for the deterministic
template.

**Every response uses one envelope.** `{ data, error, meta }` — including
validation failures and unhandled exceptions. The Pydantic models are mirrored
as Zod schemas that the frontend validates against, so a version skew between a
deployed service and a deployed frontend surfaces as a named error at the
boundary rather than as `undefined` rendering inside a clinical judgement.

Deeper detail: [`docs/architecture.md`](docs/architecture.md),
[`docs/fhir-integration.md`](docs/fhir-integration.md),
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

That brings up Postgres (with pgvector and the schema applied), Redis, all three
services, the Celery worker and beat scheduler, and the Next.js app:

| | |
| --- | --- |
| Web app | <http://localhost:3000> |
| Scan API + docs | <http://localhost:8001/docs> |
| Live API + docs | <http://localhost:8002/docs> |
| Forms API + docs | <http://localhost:8003/docs> |

Open the app, press **Load sample**, then **Scan**.

SMART on FHIR is optional for local development — Scan, Live and Forms have no
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
    "payer_slug": "aetna"
  }' | jq '.data.approval'
```

### Tests

```bash
npm test                                    # frontend typecheck + lint
cd services/scan  && pytest -q              # 44 tests
cd services/live  && pytest -q              # 25 tests
cd services/forms && pytest -q              # 30 tests
```

99 tests, no network required. The parser tests run against real note prose and
assert the behaviours that actually matter — negation scope, abbreviation case,
duration disambiguation, laterality — rather than mock plumbing.

## Production-ready vs in progress

Stated plainly, because the distinction matters more to an investor than a
feature list.

### Production-ready

- **Clinchec Scan, end to end.** Extraction, code resolution, scoring and
  justification drafting are complete and tested. Bands are calibrated: a
  well-documented request scores 0.92, a partially documented one 0.53, an
  undocumented one 0.00.
- **The SMART on FHIR auth layer.** Real OAuth 2.0 with mandatory endpoint
  discovery, S256 PKCE, `aud` binding, constant-time state comparison, an `iss`
  allowlist, encrypted JWE session cookies and automatic token refresh. Not
  mocked, not stubbed.
- **The payer adapter framework.** Rate limiting, robots.txt, per-payer error
  isolation, checksum-based change detection and an append-only revision log.
- **Clinchec Forms' mapping and validation.** Form resolution, field mapping,
  provenance labelling, NPI check-digit validation and the incomplete-submission
  gate.
- **The response envelope and its Zod mirror**, across every service.
- **Infrastructure.** Terraform for a three-tier VPC (public ALB / private
  tasks / isolated data with no internet route), KMS-encrypted RDS Multi-AZ and
  ElastiCache, per-service IAM roles, VPC flow logs with 365-day retention, ECS
  with deployment circuit breakers, and a CI/CD pipeline using OIDC federation
  rather than long-lived AWS keys.

### In progress

- **Payer portal crawling runs in seed mode by default.** The adapters,
  scheduling, politeness and diffing are all real; the criteria they load are
  transcribed from published policy rather than freshly scraped. Flipping
  `OFFLINE_SEED_MODE=false` runs the live crawl. Every adapter needs one
  hand-reviewed run against the real portal before it is trusted in production.
- **Submission does not transmit.** Forms produces a complete, validated packet
  and says explicitly that nothing was sent. Real transmission needs per-payer
  portal credentials, an executed BAA, and an X12 278 clearinghouse contract —
  commercial gates, not engineering ones.
- **The clinical NER model is lexicon-driven.** ~40 ICD-10 and ~30 CPT surface
  forms with a statistical noun-chunk fallback. High precision on the codes that
  drive PA volume; the long tail is surfaced as low-confidence rather than
  resolved. A fine-tuned clinical model swaps in behind `SPACY_CLINICAL_MODEL`
  with no code change.
- **The approval score is rules, not ML.** By design, as above — and the
  transition path is already in the wire contract (`basis` flips from
  `rule_engine` to `ml_model`).
- **pgvector is provisioned but unused.** The column, dimensions and HNSW index
  exist; nothing queries them yet. That is the seam for bootstrapping a new
  payer's rules from an existing one's similar criteria.
- **`pa_requests` has a write path but no read path.** The PA request detail
  page says so rather than rendering a fabricated record.
- **`apps/dashboard`** — the practice admin view — is a documented placeholder.
  It gets built once there are submitted outcomes to analyse.

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
    app/payers/         → submodule: clinchec-core-live   (private)
  forms/                PA form registry + field mapper
    app/mappers/        → submodule: clinchec-core-forms  (private)
packages/
  shared-types/         Zod mirrors of every service contract
  fhir-client/          SMART on FHIR + narrow FHIR R4 client
infra/
  terraform/            VPC, ECS, RDS, ElastiCache, S3, IAM
  sql/                  Schema, applied on first Postgres boot
  docker-compose.yml    Full local stack
docs/                   Architecture, FHIR integration
```

### Open scaffold, private core

Three directories are git submodules pointing at private repositories:

| path | repository | what it holds |
| --- | --- | --- |
| `services/scan/app/models/` | `clinchec-core-scan` | clinical NLP pipeline, ICD-10/CPT lexicon, approval scoring engine |
| `services/live/app/payers/` | `clinchec-core-live` | payer adapter framework and adapter methodology |
| `services/forms/app/mappers/` | `clinchec-core-forms` | PA form registry and field mapping engine |

Everything else — the clinician UI, the SMART on FHIR client, the response
envelope, the infrastructure and the CI pipeline — is open. The split is at the
package boundary, so imports and Docker builds are identical either way:
`from app.models.lexicon import …` resolves the same whether or not you have
access to the submodule.

**Cloning without access to the private repos still gives you a working
skeleton** — the web app, Terraform, the FHIR client and all three service
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
