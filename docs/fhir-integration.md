# SMART on FHIR integration

Clinchec authenticates clinicians through their EHR using the
[SMART App Launch Framework](https://hl7.org/fhir/smart-app-launch/) 2.0. There
is no Clinchec password, anywhere. That is a deliberate security decision: the
hospital's own directory, MFA policy and offboarding process govern access, and
a Clinchec breach cannot leak clinician credentials that do not exist.

Implementation lives in `packages/fhir-client` (protocol) and
`apps/web/lib/fhir.ts` + `apps/web/app/api/auth/*` (wiring).

## Supported launch modes

**EHR launch** — the clinician opens Clinchec from inside a patient's chart.
The EHR calls our launch URL with the FHIR base and an opaque launch token:

```
GET /api/auth/launch?iss=https://fhir.epic.com/.../api/FHIR/R4&launch=abc123
```

The patient in context arrives in the token response, so the note and payer are
pre-filled with no patient search.

**Standalone launch** — the clinician opens Clinchec directly. There is no
launch token, so `launch` is swapped for `launch/patient` and the EHR shows a
patient picker at the authorize screen.

## The flow

```
  EHR                     Clinchec                   Authorization server
   │                          │                              │
   │ launch?iss=…&launch=…    │                              │
   ├─────────────────────────►│                              │
   │                          │ GET .well-known/             │
   │                          │     smart-configuration      │
   │                          ├─────────────────────────────►│
   │                          │◄─── authorize + token URLs ──┤
   │                          │                              │
   │                          │ 302 → authorize?…            │
   │                          │   code_challenge=S256(v)     │
   │                          │   state=<random>             │
   │                          │   aud=<iss>                  │
   │                          ├─────────────────────────────►│
   │                          │                              │  (clinician
   │                          │                              │   authenticates)
   │                          │◄─── 302 callback?code&state ─┤
   │                          │                              │
   │                          │ POST token                   │
   │                          │   code_verifier=v            │
   │                          ├─────────────────────────────►│
   │                          │◄── access_token, patient,    │
   │                          │    encounter, refresh_token  │
```

### Discovery is mandatory

Endpoints are never hardcoded. A single Clinchec build serves many EHR tenants
and the `iss` arrives with the launch, so `discoverSmartConfiguration()` reads
`/.well-known/smart-configuration`, falling back to
`/.well-known/oauth-authorization-server` (some Cerner tenants) and then to the
`oauth-uris` extension on the CapabilityStatement (older Epic versions).

### PKCE is required

S256 only. Epic and Cerner both require PKCE for public clients, and Epic
requires it even for confidential ones. The verifier is 96 random bytes
(128 base64url characters — the spec maximum). If the server does not advertise
S256 support, `buildAuthorizeUrl` refuses rather than downgrading.

### `aud` is not optional

The authorize request carries `aud=<FHIR base URL>`. Epic rejects the request
without it. It binds the token to the resource server it is meant for.

### Client authentication

Confidential clients (registered with a secret) use HTTP Basic on the token
request. Public clients send `client_id` in the body. Sending both is a spec
violation that Epic rejects — `exchangeCodeForToken` picks exactly one based on
whether `FHIR_CLIENT_SECRET` is set.

## The `iss` allowlist

`GET /api/auth/launch` validates `iss` against `FHIR_ALLOWED_ISSUERS` before
doing anything with it.

This is the single most important check in the file. `iss` is attacker-supplied
input, and it determines where we send our client credentials and where the
authorization code goes. An unvalidated `iss` lets an attacker point the launch
at a server they control and harvest both. HTTPS is also enforced (except for
`localhost`).

```bash
# Single tenant — FHIR_ISSUER is used as the allowlist
FHIR_ISSUER=https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4

# Multi-tenant — comma-separated base URLs or host suffixes
FHIR_ALLOWED_ISSUERS=https://fhir.epic.com/...,https://fhir-myrecord.cerner.com/...
```

## Session storage

The token is stored in an encrypted JWE cookie (`dir` + `A256GCM`, httpOnly,
`sameSite=lax`, `secure` in production), keyed off `SESSION_SECRET`.

Encryption rather than signing, because the payload contains a bearer token
granting read access to patient records plus the patient identifier. A signed
cookie is readable by anyone holding it.

The PKCE verifier and CSRF `state` are parked in a *separate*, 10-minute cookie
that is deleted on first read — it needs to survive exactly one round trip.

On callback, `state` is compared in constant time against the parked value. A
mismatch means the callback did not originate from a launch we started, and the
handshake is rejected.

## Scopes requested

```
openid fhirUser launch
patient/Patient.read
patient/Condition.read
patient/Coverage.read
patient/DocumentReference.read
offline_access
```

Read-only, and only the four resource types the product actually uses:

| Scope | What it is used for |
| --- | --- |
| `Patient.read` | Demographics for the PA form |
| `Condition.read` | Active problem list, reconciled against the note's diagnoses |
| `Coverage.read` | Member ID, group number, and which payer's rules to apply |
| `DocumentReference.read` | The SOAP note itself |
| `offline_access` | Refresh token, so a clinic session does not expire mid-shift |

**Clinchec never requests write access.** It does not write to the chart, and
the scopes make that verifiable rather than promised. This is the first thing a
hospital security review checks, and `packages/fhir-client/src/client.ts` is
deliberately narrow so the full set of possible PHI requests fits on one screen.

`Condition` reads are filtered to `clinical-status=active`; a condition resolved
in 2014 is not an indication. `DocumentReference` reads are filtered to LOINC
`11506-3` (progress note) and `11488-4` (consult note), because an unfiltered
query returns scanned faxes and discharge paperwork.

## Token refresh

`FhirClient` calls `ensureValidSession()` before every request and refreshes 60
seconds ahead of expiry, so no call races the clock. A refresh writes the new
session straight back to the cookie via the `onSessionRefreshed` callback.

Servers that rotate refresh tokens send a new one; those that do not expect the
original to keep working — `refreshSession` handles both. Launch context
(`patient`, `encounter`, `fhirUser`) is not re-issued on refresh and is carried
forward explicitly.

## Local development

The Epic sandbox is the fastest path to a working launch.

1. Register an app at <https://fhir.epic.com/> (Patient-facing or
   Provider-facing, R4).
2. Set the redirect URI to `http://localhost:3000/api/auth/callback`.
3. Fill in `.env`:

```bash
FHIR_ISSUER=https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4
FHIR_CLIENT_ID=<your non-production client id>
FHIR_CLIENT_SECRET=            # public client: leave blank
FHIR_REDIRECT_URI=http://localhost:3000/api/auth/callback
SESSION_SECRET=$(openssl rand -base64 32)
```

4. Visit `http://localhost:3000/login` and press **Continue with your EHR**.

Epic's sandbox test patients (Camila Lopez, Derrick Lin) have problem lists and
notes attached, which is enough to exercise the full pre-fill path.

Without these variables the login page says so explicitly and the rest of the
app still runs — Scan, Live and Forms have no FHIR dependency, so you can
develop against them by pasting notes directly.

### Working on the UI without an EHR

Registering a sandbox client is a signup, and sometimes you just want to see
the Scan screen. `DEV_AUTH_BYPASS=true` adds a **Continue without an EHR**
button to the login page that mints a session which never touched an EHR.

This is an authentication bypass in an application that handles PHI, so it is
built to fail closed:

| Control | Behaviour |
| --- | --- |
| Default | Off. Only the exact string `true` enables it — not `1`, `yes` or `TRUE`. |
| Production | Refused outright when `NODE_ENV=production`, **even with the flag set**. Checked independently of the flag, in `lib/dev-auth-guard.ts`. |
| Discovery | `/api/auth/dev-login` returns **404** while disabled, not 403, so a production deployment does not advertise that the endpoint exists. |
| Credential | The token is a literal placeholder and `iss` is `https://dev-bypass.invalid/` — the reserved `.invalid` TLD (RFC 2606) can never resolve, so a stray FHIR call fails at DNS rather than reaching a live server. |
| Visibility | Sessions are tagged `dev: true`, render a permanent non-dismissible banner, and log a `[SECURITY]` warning on every use. |
| FHIR calls | `getFhirClient()` returns `null` for a dev session, so callers take their existing "no EHR context" path instead of issuing a doomed request. |

The guard is covered by `apps/web/lib/dev-auth-guard.test.ts` (Node's built-in
test runner, no framework), and CI additionally asserts the production refusal
at the environment level so a regression cannot ship unnoticed.

There is no EHR data in a bypassed session, so the dashboard opens with an
empty note field rather than a pre-filled one — which is exactly the path a
standalone launch takes anyway.

## Known gaps

- **`fhirUser` is read from the `id_token` without JWKS signature
  verification.** It is used for display only; the access token is what
  authorizes, and the EHR validated that. Any future path that grants access
  based on the claim must verify the signature first. Flagged in
  `smart.ts:decodeFhirUser`.
- **Sign-out does not revoke at the EHR.** Clearing the cookie removes our
  ability to use the token, but where a `revocation_endpoint` is advertised we
  should call it.
- **No Da Vinci CRD/DTR support.** The
  [Da Vinci Coverage Requirements Discovery](https://hl7.org/fhir/us/davinci-crd/)
  IG is the standards-track answer to exactly this problem, and payers are
  slowly adopting it. `SubmissionChannel.FHIR_CRD` exists in the Forms schema as
  the seam for it. Portal scraping is what works today; CRD is where this goes.
