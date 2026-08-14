import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';
import { AlertCircle, Inbox } from 'lucide-react';

import type { PayerSummary } from '@clinchec/shared-types';

import { listPayers } from '@/lib/api';
import { getSession } from '@/lib/session';
import { formatRelativeHours } from '@/lib/utils';
import { AppShell } from '@/components/AppShell';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export const metadata: Metadata = { title: 'PA requests' };
export const dynamic = 'force-dynamic';

/**
 * Prior authorization worklist.
 *
 * The request history itself is served from `pa_requests` once submission is
 * wired end to end. Until then this page shows what Clinchec Live actually
 * knows — which payers are tracked and how fresh their criteria are — because
 * an empty worklist should still tell the practice something true rather than
 * render a placeholder table of invented rows.
 */
export default async function PaRequestsPage() {
  const session = await getSession();
  if (!session) redirect('/login');

  let payers: PayerSummary[] = [];
  let liveError: string | null = null;

  try {
    payers = await listPayers();
  } catch {
    liveError = 'Clinchec Live is not reachable, so payer rule freshness is unavailable.';
  }

  return (
    <AppShell
      active="pa-requests"
      clinicianName={session.clinicianName}
      practiceName={session.practiceName}
      devSession={session.dev === true}
    >
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Prior authorization requests</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Everything scanned, drafted and submitted from this practice.
        </p>
      </div>

      <Card className="mb-6">
        <CardContent className="flex flex-col items-center gap-3 py-14 text-center">
          <span className="flex size-11 items-center justify-center rounded-full bg-muted">
            <Inbox className="size-5 text-muted-foreground" aria-hidden="true" />
          </span>
          <div className="max-w-md space-y-1.5">
            <p className="font-medium">No requests yet</p>
            <p className="text-sm text-muted-foreground">
              Scan a SOAP note to pre-screen a request. Anything you draft or submit
              will appear here with its approval score and payer response.
            </p>
          </div>
          <Link
            href="/dashboard"
            className="text-sm font-medium text-primary underline-offset-4 hover:underline"
          >
            Scan a note
          </Link>
        </CardContent>
      </Card>

      <section aria-labelledby="payer-coverage">
        <h2 id="payer-coverage" className="mb-3 text-sm font-semibold">
          Payer rule coverage
        </h2>

        {liveError ? (
          <Alert variant="caution">
            <AlertCircle aria-hidden="true" />
            <AlertTitle>Rule freshness unavailable</AlertTitle>
            <AlertDescription>{liveError}</AlertDescription>
          </Alert>
        ) : (
          <div className="grid gap-4 md:grid-cols-3">
            {payers.map((payer) => (
              <Card key={payer.slug}>
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center justify-between text-base">
                    {payer.display_name}
                    <Badge variant={payer.rule_count > 0 ? 'muted' : 'outline'}>
                      {payer.rule_count} rule{payer.rule_count === 1 ? '' : 's'}
                    </Badge>
                  </CardTitle>
                </CardHeader>
                <CardContent className="text-sm text-muted-foreground">
                  {payer.last_crawled_at ? (
                    <p>
                      Last synced{' '}
                      {formatRelativeHours(
                        (Date.now() - new Date(payer.last_crawled_at).getTime()) / 3_600_000,
                      )}
                      {payer.last_crawl_status === 'failed' ? (
                        <Badge variant="deny" className="ml-2">
                          last crawl failed
                        </Badge>
                      ) : null}
                    </p>
                  ) : (
                    <p>Not yet crawled.</p>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </section>
    </AppShell>
  );
}
