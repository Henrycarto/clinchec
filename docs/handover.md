# Handover

For whoever picks this up next. The README sells the product and
[`architecture.md`](architecture.md) describes the shape; this is the part that
is only obvious once you have already made the mistake.

Read the first three sections before you change anything. The rest is reference.

---

## 1. The repository is three repositories

The public repo is scaffolding. The clinical core lives in two private
submodules, mounted at package boundaries so no import path changes:

| Path | Repo | Holds |
| --- | --- | --- |
| `services/scan/app/models` | `clinchec-core-scan` | scoring engine, clinical lexicon, indication matcher |
| `services/live/app/payers` | `clinchec-core-live` | payer adapters, adjudication registry, content guard, fixtures |
| `services/forms/app/mappers` | `clinchec-core-forms` | form field mappings |

```bash
git clone --recurse-submodules <public>
# already cloned:
git submodule update --init --recursive
```

Without the submodules the services still lint and typecheck, and their tests
skip with a message naming what is missing. CI has the same two modes: **FULL**
when `CORE_REPO_TOKEN` is present, **SCAFFOLD** when it is not. A green
SCAFFOLD run has not tested the core.

### The push-ordering rule

**Push the repo that DEFINES a symbol before the repo that USES it.**

Wire types live in the public repo (`services/*/app/schemas.py`,
`packages/shared-types`); the code that constructs them often lives in a core.
Push a core first and its CI checks out the *published* public scaffold, which
does not yet have the definition, and fails on import.

I got this wrong twice. Both times the fix was to push the other side and
re-run — but the fifteen minutes of red is avoidable, and on a shared branch it
is somebody else's morning.

One case the rule does not cover: a **behaviour** change in a core whose
**test** lives in the public repo. No ordering avoids that; expect one red run
and land the pair quickly.

---

## 2. Doctrine

These look like over-engineering until you know what each one is scar tissue
from. Do not simplify them without reading the reason.

### Never manufacture a plausible answer

The failure that shaped this codebase: an early payer-bulletin mapping was
**2 of 9 correct**, because plausible-looking identifiers were generated instead
of verified against source. The same shape recurred as an invented `< 80
characters` junk-content guard that sat *below* the junk it was meant to reject,
and as synthetic test fixtures that passed while the real documents failed.

Consequences that are now load-bearing:

- Thresholds are derived from measured distributions with the margin recorded
  beside them, never picked by intuition.
- Fixtures are captured whole. Truncating one flipped Aetna CPB 0236's verdict,
  because the head of a policy is denser than the whole. Where a fixture *is*
  cut, the cut point and original length are recorded in the file.
- A mapping is derived from the payer's own statement — an Applicable Codes
  table, a policy title — not from a filename or a keyword guess.
- When something cannot be determined, the code says so and emits nothing.
  Silence beats a confident wrong answer everywhere in this system.

### The score is not a probability

It is called a **documentation score** and it measures how completely a note
documents what the criteria ask for. Clinchec has never observed a submitted
request's outcome, so there is nothing to calibrate a probability against.
"Approval likelihood 92%" was the old label and it claimed exactly the
calibration it lacked.

Same reason the counterfactual is "documenting this adds 26 points" and not
"raises approval odds by N%". The first is arithmetic over weights on screen;
the second is invented.

If outcome data ever exists, `basis` flips from `rule_engine` to `ml_model` and
the drivers become the feature vector. That path is already in the wire
contract.

### Three clause polarities, not two

`covered` and `excluded` are obvious. `delegated` is the one people delete:

> "Surgery of the knee is proven and medically necessary **in certain
> circumstances**. For medical necessity clinical coverage criteria, refer to
> the InterQual® CP: Procedures."

That paragraph contains "medically necessary", so a parser reads it as coverage
and stores the deferral sentence as criteria. The score that follows is
indistinguishable from a real one — same band, same drivers, same source URL,
same fresh timestamp — for a determination nobody involved has seen. A delegated
clause is surfaced and never scored, and migration `004` enforces that it
carries no evidence requirements.

### Advisory clauses

A clause whose indication could not be resolved to ICD-10 prefixes is
`advisory`: shown to the clinician, never allowed to decide the score. An
unscoped exclusion would otherwise match every request and deny the lot.

The exception, and it is deliberate: a decisive **text** match can select an
advisory clause, because the note's own language is sometimes the only thing
that separates two indications sharing a code (Aetna CPB 0673 — meniscal root
tears and mechanical-symptom tears are both M23.2xx).

### Absence is evidence only from a complete crawl

`CrawlResult.complete` reports whether a run actually surveyed the payer, and
defaults to `False`. Rule retirement counts absences only across complete runs
and waits for three in a row.

The reason: UHC discovery once returned four stale paths and parsed nothing.
"Absent means gone" would have read that as UnitedHealthcare withdrawing its
entire rule set.

---

## 3. Running it

```bash
npm install
cp .env.example .env
docker compose -f infra/docker-compose.yml up -d
```

Web on `:3000`, Scan `:8001`, Live `:8002`, Forms `:8003`.

`OFFLINE_SEED_MODE=true` by default — adapters load transcribed seed criteria
and touch no payer infrastructure. That is the right default for development
and CI. Set it false only for a deliberate live crawl.

To sign in locally without an EHR, set `DEV_AUTH_BYPASS=true` and restart
`web`. The bypass is refused under `NODE_ENV=production`, asserted in CI at the
environment level as well as in a unit test.

### Gotchas that cost real time

- **Docker on Windows does not always propagate file events** over the bind
  mount. If the web container serves markup you know you changed, `docker
  compose restart web`. I chased a "cached" layout for twenty minutes.
- **Python venvs must sit on a short path.** spaCy's DLL loading fails on long
  Windows paths; use something like `C:\…\Temp\clv`.
- **Migrations do not self-apply to an existing volume.** They used to rely on
  Postgres's initdb hook, which only fires on an empty data directory — see §5.
- **`npm run build --workspace packages/shared-types` is a no-op** by design;
  the package ships TypeScript source consumed via `transpilePackages`.

---

## 4. The wire contract, and how it breaks

Every service response is parsed through a Zod mirror of its Pydantic model in
`packages/shared-types`. This is what makes a shape mismatch fail loudly at the
boundary instead of propagating `undefined` into a component rendering a
clinical judgement.

The cost is that the mirror has to be maintained, and it drifts in two ways
that fail *differently*:

| Drift | Symptom |
| --- | --- |
| Missing **enum value** | Zod rejects the whole response. The UI shows "version mismatch". Loud. |
| Missing **field** | Zod strips unknown keys. The field silently never arrives. Silent. |

Both have happened. `criteria_delegated` was missing from the enum, so every
UnitedHealthcare knee, hip and shoulder scan rendered "version mismatch" —
exactly the delegated cases. `plan_type` was missing from the request mirror, so
the web app could not send it at all and every scan was scored against whichever
line of business the rules service returned by default.

`services/scan/tests/test_wire_contract.py` now compares the enums and the field
sets directly, in both directions. **Extend it when you add a service.** Neither
the service tests nor the web tests catch this on their own: the service tests
never go through Zod, and the web tests never call the service.

---

## 5. Database and migrations

Migrations are `services/live/migrations/*.sql`, applied by
`services/live/app/migrations.py`:

- ordered by filename, tracked in `schema_migrations` with a checksum;
- a changed checksum is a hard failure, not silent divergence;
- a Postgres advisory lock serialises concurrent starts;
- failure is fatal at startup.

They live beside Live because **Live is the only service with database access** —
Scan and Forms reach it through Live's API. That also puts them inside the one
Docker build context that needs them.

Two entry points, same function: a one-shot `migrate` service in compose that
everything else waits on, and Live's own startup. In ECS the pre-deploy task is
authoritative and `RUN_MIGRATIONS_ON_STARTUP=false` on the service, so a bad
migration stops the deploy instead of failing every task and tripping the
circuit breaker.

**Every migration must be backward compatible with the code currently serving
traffic** — it runs *before* the new images roll out. Add columns and tables;
never drop or rename in the same release. A destructive change is two deploys.
Keep them re-runnable (`IF NOT EXISTS`, `ON CONFLICT DO NOTHING`); a test
asserts it.

---

## 6. Payer reality

Crawling is not a uniform engineering problem. As audited **2026-08-15**:

| Payer | State |
| --- | --- |
| **Aetna** | Crawlable. Clinical Policy Bulletins, HTML, adjudication registry works well. |
| **UnitedHealthcare** | Crawlable. Sitemap → index pages → per-policy PDFs. 14 rules live. |
| **BCBS** | **No licensee publishes criteria to an unauthenticated client.** HCSC behind a click-through "I Agree"; Michigan behind a provider login; Premera client-rendered; Anthem 403s a self-identifying crawler; NC 406; Alabama 403. Serves the seeded national baseline. |

Each licensee carries an `Access` value and dated evidence in the registry, and
the non-crawlable ones are logged every run. That is deliberate: the previous
registry held four dead URLs and reported an empty crawl as success for months.

**None of these blocks is a bug to route around.** A terms gate, a login, and a
stated preference not to be crawled are answers. The response is a data
agreement, not a workaround that surfaces in a vendor security review of a
PHI-adjacent product.

### The CMS coverage database is licence-blocked

UHC's Medicare Advantage policies route to CMS LCDs, and CMS publishes bulk
exports and a documented API. Both gate on an AMA agreement granting **"personal
use only … non-commercial uses"**, and the API's 401 body embeds the same terms.
Not accepted on this product's behalf. An AMA CPT licence unblocks it.

**You likely need that licence anyway.** Scan returns CPT descriptors in its
extraction output. Live no longer *stores* them — that was removed once the
licence question surfaced — but the descriptors reaching a browser are AMA text.

---

## 7. Honest state

### Real and verified against live systems

Scan end to end; Live crawling Aetna and UHC; the SMART on FHIR scaffold (real
OAuth, discovery, PKCE, `aud` binding, issuer allowlist, JWE cookies); the
adjudication registry with named patterns; retirement; migrations; CI green
across three repos; a deploy pipeline with a gated migration step.

The local database at handover holds **21 active rules and 23 clauses** — UHC 14
(3 retired), Aetna 4, BCBS 3 seeded. The UHC and Aetna rules came from live
crawls; the BCBS three are the seeded national baseline, for the reason in §6.

### Scaffolding

- **Forms.** Mapping and validation are real; `pa_requests` has no read path and
  submission deliberately transmits nothing.
- **`apps/dashboard`** is a placeholder.
- **pgvector** is provisioned and unqueried.
- **Terraform has never been applied.** It validates; no AWS account has seen it.

### Unvalidated

- **Clinical accuracy.** Scoring weights are reasoned, not measured. No clinician
  has reviewed extractions against real notes. This is the largest gap and it
  needs a person, not code.
- **SMART auth has never completed a handshake with a real EHR.** The scaffold is
  complete; the issuer allowlist plus `getLaunchConfig` make Athenahealth or
  DrChrono a config change rather than a code change.
- **No outcome data**, hence no ML model and no calibrated probability.

---

## 8. If you want somewhere to start

1. **Read `services/live/app/payers/docs/payer-adapter-guide.md`** (private repo)
   before touching a crawler. It is the densest document here and every rule in
   it was paid for by a live run.
2. **`README.md`'s "Production-ready vs in progress" section is drifting.** It
   still says the score is an "approval likelihood" and describes crawling as
   seed-only. Reconcile it.
3. **Per-document retirement tracking.** Any fetch failure currently marks a
   whole crawl incomplete, so one permanently flaky document blocks retirement
   for that payer forever. Logged each run, but it can silently never fire.
4. **Extend governance verification.** Only ~10 declared CPTs have been
   hand-checked against their bulletins. Also: nothing yet identifies the
   bulletin that actually governs CPT 27130.
5. **Recalibrate `guard.py` per adapter.** Its thresholds were measured on
   Aetna's HTML and are not applied to the PDF path at all.
6. **`criteria_text` truncates at 4000 characters**, which now cuts a Medicare
   Advantage record's referenced commercial criteria mid-sentence.

---

## 9. Things that will look wrong and are not

- `_CEILING` in the scoring engine duplicates weights that appear as literals in
  the branches. It is cited by line and guarded by a test asserting no driver
  exceeds its entry. Deriving it automatically was tried; the satisfied weights
  are computed expressions, so a static table plus a guard is the honest option.
- `missing_elements` and `gaps` carry the same text. `gaps` is the structured
  form with the driver key and price; `missing_elements` is the flat projection
  the justification drafter reads. A test asserts they agree.
- Gaps are paired to drivers **by key, never by position**. The two lists are
  not parallel — functional impairment records a gap from a branch with no unmet
  driver. A positional join was written, checked, and removed; a test asserts it
  stays removed.
- Band labels describe the note ("Well documented", "Gaps to close", "Not ready
  to submit"), not the payer's decision. See §2.
- The signed-out page is not a marketing page. Clinchec launches from inside an
  EHR; the people reaching that URL are a clinician whose session died and
  somebody doing diligence. It shows live rule counts and freshness because that
  is checkable, where a feature grid is not.
