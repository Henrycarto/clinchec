import 'server-only';

import type { SmartSession } from '@clinchec/fhir-client';

import { assertDevBypassAllowed } from './dev-auth-guard';
import type { AppSession } from './session';

/**
 * Development-only authentication bypass.
 *
 * This exists so the Scan and Forms UI can be exercised without a registered
 * SMART on FHIR client. It creates a session that never touched an EHR.
 *
 * It is an auth bypass in an application that handles PHI, so it is built to
 * fail closed and to be impossible to miss:
 *
 *   1. **Off unless explicitly enabled.** `DEV_AUTH_BYPASS=true`, nothing else.
 *   2. **Refuses to run in production.** `NODE_ENV=production` throws, even
 *      with the flag set, so a misconfigured deploy crashes loudly rather than
 *      quietly accepting unauthenticated users. See `dev-auth-guard.ts`.
 *   3. **Carries no usable credential.** The token is a literal placeholder and
 *      `iss` points at the reserved `.invalid` TLD (RFC 2606), which can never
 *      resolve — so a stray FHIR call fails at DNS instead of reaching a real
 *      server with a real patient behind it.
 *   4. **Announces itself.** Sessions are tagged `dev: true`, the UI shows a
 *      permanent banner, and every use is logged.
 *
 * The route that consumes this returns 404 when disabled, so a production
 * deployment does not advertise that the endpoint exists.
 */

export {
  assertDevBypassAllowed,
  DEV_BYPASS_ENV_FLAG,
  DevBypassForbiddenError,
  isDevBypassEnabled,
} from './dev-auth-guard';

/** Eight hours — the same lifetime a real SMART session gets. */
const DEV_SESSION_TTL_MS = 1000 * 60 * 60 * 8;

/**
 * A synthetic session for a fictional clinician.
 *
 * Every value here is deliberately obvious. If any of it reaches a payer form
 * or a log line, it should be self-evidently fake to whoever reads it.
 */
export function buildDevSession(): AppSession {
  assertDevBypassAllowed();

  const smart: SmartSession = {
    accessToken: 'DEV-BYPASS-NOT-A-REAL-TOKEN',
    expiresAt: Date.now() + DEV_SESSION_TTL_MS,
    scope: 'dev-bypass',
    // Reserved TLD: guaranteed never to resolve, so an accidental FHIR call
    // fails immediately instead of reaching a live endpoint.
    iss: 'https://dev-bypass.invalid/fhir',
    tokenEndpoint: 'https://dev-bypass.invalid/token',
  };

  return {
    smart,
    clinicianName: 'Dr. Dev Bypass (not signed in)',
    practiceName: 'Local development',
    dev: true,
  };
}

/** True when a session came from the bypass rather than a real EHR launch. */
export function isDevSession(session: AppSession | null): boolean {
  return session?.dev === true;
}
