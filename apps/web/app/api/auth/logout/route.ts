import { NextResponse, type NextRequest } from 'next/server';

import { clearSession } from '@/lib/session';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

/**
 * Ends the local session.
 *
 * Note this does not revoke the token at the EHR. Where the authorization
 * server advertises a `revocation_endpoint`, revoking on sign-out is the
 * correct behaviour and is tracked as follow-up work; clearing the cookie
 * removes this app's ability to use the token either way.
 */
export async function POST(request: NextRequest) {
  clearSession();
  return NextResponse.redirect(new URL('/login', request.nextUrl.origin), { status: 303 });
}

export async function GET(request: NextRequest) {
  clearSession();
  return NextResponse.redirect(new URL('/login', request.nextUrl.origin));
}
