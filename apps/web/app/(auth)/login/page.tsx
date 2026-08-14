import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';
import { Activity, AlertCircle, FlaskConical, Hospital, ShieldCheck, TriangleAlert } from 'lucide-react';

import { isDevBypassEnabled } from '@/lib/dev-auth';
import { getSession } from '@/lib/session';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

export const metadata: Metadata = { title: 'Sign in' };
export const dynamic = 'force-dynamic';

/**
 * Sign-in.
 *
 * There is no password here by design. Clinchec never holds a clinician
 * credential — identity comes from the EHR over SMART on FHIR, so access is
 * governed by the hospital's own directory, MFA and offboarding.
 */
export default async function LoginPage({
  searchParams,
}: {
  searchParams: { error?: string; message?: string; iss?: string };
}) {
  const session = await getSession();
  if (session) redirect('/dashboard');

  const configuredIssuer = process.env.FHIR_ISSUER;
  const issuer = searchParams.iss ?? configuredIssuer;
  const canLaunch = Boolean(issuer && process.env.FHIR_CLIENT_ID);
  const devBypassAvailable = isDevBypassEnabled();

  return (
    <div className="flex min-h-dvh items-center justify-center bg-muted/30 p-6">
      <div className="w-full max-w-md space-y-6">
        <div className="flex flex-col items-center gap-3 text-center">
          <span className="flex size-11 items-center justify-center rounded-xl bg-primary text-primary-foreground">
            <Activity className="size-6" aria-hidden="true" />
          </span>
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Clinchec</h1>
            <p className="text-sm text-muted-foreground">
              Prior authorization pre-screening
            </p>
          </div>
        </div>

        {searchParams.error ? (
          <Alert variant="destructive">
            <AlertCircle aria-hidden="true" />
            <AlertTitle>Sign-in did not complete</AlertTitle>
            <AlertDescription>
              {searchParams.message ?? 'The EHR returned an error. Please try again.'}
            </AlertDescription>
          </Alert>
        ) : null}

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Sign in with your EHR</CardTitle>
            <CardDescription>
              Clinchec uses SMART on FHIR. You authenticate with your hospital
              credentials — Clinchec never sees or stores your password.
            </CardDescription>
          </CardHeader>

          <CardContent className="space-y-4">
            {canLaunch ? (
              <Button asChild size="lg" className="w-full">
                <a href={`/api/auth/launch?iss=${encodeURIComponent(issuer!)}`}>
                  <Hospital aria-hidden="true" />
                  Continue with your EHR
                </a>
              </Button>
            ) : (
              <Alert variant="caution">
                <AlertCircle aria-hidden="true" />
                <AlertTitle>SMART on FHIR is not configured</AlertTitle>
                <AlertDescription className="space-y-2">
                  <p>
                    Set <code className="font-mono text-xs">FHIR_ISSUER</code>,{' '}
                    <code className="font-mono text-xs">FHIR_CLIENT_ID</code> and{' '}
                    <code className="font-mono text-xs">FHIR_REDIRECT_URI</code> in{' '}
                    <code className="font-mono text-xs">.env</code> to enable sign-in.
                  </p>
                  <p className="text-xs">
                    For local development, register a client against the{' '}
                    <a
                      className="underline underline-offset-2"
                      href="https://fhir.epic.com/"
                      target="_blank"
                      rel="noreferrer"
                    >
                      Epic on FHIR sandbox
                    </a>
                    .
                  </p>
                </AlertDescription>
              </Alert>
            )}

            <div className="flex items-start gap-2 rounded-md bg-muted/50 p-3 text-xs text-muted-foreground">
              <ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <p>
                Clinchec requests read-only access to the patient in context, their
                active problems, coverage and clinical notes. It never writes to the
                chart.
              </p>
            </div>

            {devBypassAvailable ? (
              <div className="space-y-2 rounded-md border border-caution-border bg-caution-surface p-3">
                <div className="flex items-start gap-2 text-xs text-caution-foreground">
                  <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                  <p>
                    <span className="font-semibold">Development bypass is enabled.</span>{' '}
                    This creates a session without authenticating against any EHR. It is
                    refused when <code className="font-mono">NODE_ENV=production</code>.
                  </p>
                </div>
                <form action="/api/auth/dev-login" method="post">
                  <Button type="submit" variant="outline" size="sm" className="w-full">
                    <FlaskConical aria-hidden="true" />
                    Continue without an EHR
                  </Button>
                </form>
              </div>
            ) : null}
          </CardContent>
        </Card>

        <p className="text-center text-xs text-muted-foreground">
          Launching from inside your EHR?{' '}
          <Link href="/" className="underline underline-offset-2">
            Learn how it works
          </Link>
        </p>
      </div>
    </div>
  );
}
