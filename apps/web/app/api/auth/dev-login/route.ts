import { NextResponse, type NextRequest } from 'next/server';

import {
  buildDevSession,
  DevBypassForbiddenError,
  isDevBypassEnabled,
} from '@/lib/dev-auth';
import { saveSession } from '@/lib/session';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

/**
 * Development-only sign-in.
 *
 * Mints a session that never touched an EHR, so the Scan and Forms UI can be
 * exercised without a registered SMART on FHIR client.
 *
 * When the bypass is disabled this returns **404, not 403**: a production
 * deployment should give no indication that the endpoint exists at all.
 *
 * POST is the real entry point (it is state-changing and CSRF-protected by the
 * form's same-origin submission). GET exists only so the flow is reachable by
 * typing the URL during local debugging, and is subject to the identical guard.
 */
export async function POST(request: NextRequest) {
  return devLogin(request);
}

export async function GET(request: NextRequest) {
  return devLogin(request);
}

async function devLogin(request: NextRequest) {
  if (!isDevBypassEnabled()) {
    return new NextResponse(null, { status: 404 });
  }

  try {
    const session = buildDevSession();
    await saveSession(session);

    // Loud and unconditional: every use of the bypass leaves a trace.
    console.warn(
      '[SECURITY] Development auth bypass used — a session was created without ' +
        'EHR authentication. This must never appear in a production log.',
    );

    return NextResponse.redirect(new URL('/dashboard', request.nextUrl.origin), {
      status: 303,
    });
  } catch (error) {
    if (error instanceof DevBypassForbiddenError) {
      console.error('[SECURITY] Dev auth bypass refused:', error.message);
      return new NextResponse(null, { status: 404 });
    }
    throw error;
  }
}
