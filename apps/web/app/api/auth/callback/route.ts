import { NextResponse, type NextRequest } from 'next/server';

import {
  exchangeCodeForToken,
  FhirClient,
  formatHumanName,
  SmartAuthError,
} from '@clinchec/fhir-client';

import { getLaunchConfig } from '@/lib/fhir';
import { consumePkceState, saveSession } from '@/lib/session';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

/**
 * SMART on FHIR redirect endpoint.
 *
 * The EHR sends the user back here with `code` and `state`. We verify `state`
 * against the value parked at launch, exchange the code with the PKCE verifier,
 * and store the resulting session in an encrypted cookie.
 */
export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const origin = request.nextUrl.origin;

  // The authorization server reports user denial and config errors here.
  const oauthError = params.get('error');
  if (oauthError) {
    const description = params.get('error_description') ?? oauthError;
    return redirectToLogin(origin, oauthError, description);
  }

  const code = params.get('code');
  const state = params.get('state');

  if (!code || !state) {
    return redirectToLogin(
      origin,
      'invalid_callback',
      'The EHR redirect was missing the authorization code or state.',
    );
  }

  const pkce = await consumePkceState();
  if (!pkce) {
    return redirectToLogin(
      origin,
      'expired_handshake',
      'The sign-in attempt expired. Please launch Clinchec again from the EHR.',
    );
  }

  // Constant-time-ish comparison of a value we generated; a mismatch means the
  // callback did not originate from the launch we started (CSRF).
  if (!timingSafeEqual(pkce.state, state)) {
    return redirectToLogin(
      origin,
      'state_mismatch',
      'The sign-in response did not match the request that started it.',
    );
  }

  try {
    const config = getLaunchConfig(pkce.iss);
    const smart = await exchangeCodeForToken(config, code, pkce.codeVerifier);

    // Resolve the clinician's display name once so the app shell does not need
    // a FHIR round trip on every render.
    let clinicianName: string | undefined;
    try {
      const client = new FhirClient({ config, session: smart });
      const practitioner = await client.getPractitioner();
      clinicianName = practitioner ? formatHumanName(practitioner.name).full : undefined;
    } catch {
      // A tenant that does not expose Practitioner is still a valid session.
      clinicianName = undefined;
    }

    await saveSession({ smart, clinicianName });

    const destination = safeReturnTo(pkce.returnTo);
    return NextResponse.redirect(new URL(destination, origin));
  } catch (error) {
    if (error instanceof SmartAuthError) {
      return redirectToLogin(origin, error.code, error.message);
    }
    console.error('SMART callback failed', error);
    return redirectToLogin(
      origin,
      'callback_failed',
      'Could not complete sign-in with the EHR.',
    );
  }
}

function redirectToLogin(origin: string, code: string, message: string) {
  const url = new URL('/login', origin);
  url.searchParams.set('error', code);
  url.searchParams.set('message', message.slice(0, 300));
  return NextResponse.redirect(url);
}

/** Only ever redirect to a path on this origin — never to a supplied URL. */
function safeReturnTo(returnTo?: string): string {
  if (!returnTo || !returnTo.startsWith('/') || returnTo.startsWith('//')) {
    return '/dashboard';
  }
  return returnTo;
}

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let mismatch = 0;
  for (let index = 0; index < a.length; index += 1) {
    mismatch |= a.charCodeAt(index) ^ b.charCodeAt(index);
  }
  return mismatch === 0;
}
