import 'server-only';

import { cookies } from 'next/headers';
import { EncryptJWT, jwtDecrypt } from 'jose';

import type { SmartSession } from '@clinchec/fhir-client';

/**
 * Encrypted session cookie.
 *
 * The SMART access token grants read access to patient records, so it is
 * stored as an encrypted JWE (A256GCM) in an httpOnly cookie and is never
 * exposed to client-side JavaScript. Encryption rather than signing matters
 * here: a signed cookie is readable by anyone holding it, and this payload
 * contains a bearer token plus the patient identifier.
 */

const COOKIE_NAME = 'clinchec_session';
const PKCE_COOKIE_NAME = 'clinchec_pkce';

/** Sessions expire well inside a clinic session; refresh extends them. */
const SESSION_TTL_SECONDS = 60 * 60 * 8;
const PKCE_TTL_SECONDS = 60 * 10;

export interface AppSession {
  smart: SmartSession;
  /** Denormalised for display so the header does not re-fetch on every render. */
  clinicianName?: string;
  practiceName?: string;
  /**
   * Set only by the development auth bypass. Never set by a real SMART launch,
   * so anything downstream can distinguish a session that authenticated against
   * an EHR from one that did not. See `lib/dev-auth.ts`.
   */
  dev?: boolean;
}

export interface PkceState {
  state: string;
  codeVerifier: string;
  iss: string;
  returnTo?: string;
}

function secret(): Uint8Array {
  const value = process.env.SESSION_SECRET;
  if (!value || value.length < 32) {
    throw new Error(
      'SESSION_SECRET must be set to at least 32 characters. ' +
        'Generate one with: openssl rand -base64 32',
    );
  }
  // A256GCM needs exactly 32 bytes; take a stable slice of the configured key.
  return new TextEncoder().encode(value).slice(0, 32);
}

async function encrypt(payload: Record<string, unknown>, ttlSeconds: number): Promise<string> {
  return new EncryptJWT(payload)
    .setProtectedHeader({ alg: 'dir', enc: 'A256GCM' })
    .setIssuedAt()
    .setIssuer('clinchec')
    .setExpirationTime(`${ttlSeconds}s`)
    .encrypt(secret());
}

async function decrypt<T>(token: string): Promise<T | null> {
  try {
    const { payload } = await jwtDecrypt(token, secret(), { issuer: 'clinchec' });
    return payload as T;
  } catch {
    // Tampered, expired, or encrypted under a rotated key — all mean "no session".
    return null;
  }
}

const baseCookieOptions = {
  httpOnly: true,
  secure: process.env.NODE_ENV === 'production',
  sameSite: 'lax' as const,
  path: '/',
};

// ---------------------------------------------------------------------------
// App session
// ---------------------------------------------------------------------------

export async function saveSession(session: AppSession): Promise<void> {
  const token = await encrypt({ session }, SESSION_TTL_SECONDS);
  cookies().set(COOKIE_NAME, token, { ...baseCookieOptions, maxAge: SESSION_TTL_SECONDS });
}

export async function getSession(): Promise<AppSession | null> {
  const cookie = cookies().get(COOKIE_NAME);
  if (!cookie) return null;
  const payload = await decrypt<{ session: AppSession }>(cookie.value);
  return payload?.session ?? null;
}

export function clearSession(): void {
  cookies().delete(COOKIE_NAME);
  cookies().delete(PKCE_COOKIE_NAME);
}

// ---------------------------------------------------------------------------
// PKCE handshake state
// ---------------------------------------------------------------------------

/**
 * Park the PKCE verifier and CSRF state between the authorize redirect and the
 * callback. Short-lived and separate from the session cookie, because it must
 * survive exactly one round trip and no longer.
 */
export async function savePkceState(state: PkceState): Promise<void> {
  const token = await encrypt({ ...state }, PKCE_TTL_SECONDS);
  cookies().set(PKCE_COOKIE_NAME, token, { ...baseCookieOptions, maxAge: PKCE_TTL_SECONDS });
}

export async function consumePkceState(): Promise<PkceState | null> {
  const cookie = cookies().get(PKCE_COOKIE_NAME);
  if (!cookie) return null;
  const payload = await decrypt<PkceState>(cookie.value);
  // Single use: whether or not it validated, this handshake is finished.
  cookies().delete(PKCE_COOKIE_NAME);
  return payload;
}
