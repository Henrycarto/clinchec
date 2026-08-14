import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';
import { ArrowLeft } from 'lucide-react';

import { getSession } from '@/lib/session';
import { AppShell } from '@/components/AppShell';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export const dynamic = 'force-dynamic';

export async function generateMetadata({
  params,
}: {
  params: { id: string };
}): Promise<Metadata> {
  return { title: `PA request ${params.id.slice(0, 8)}` };
}

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * A single prior authorization request.
 *
 * The `pa_requests` read path is not wired yet — the table and the write path
 * exist, the query does not. Rather than render a fabricated record, this page
 * validates the identifier and reports honestly that the record is not
 * retrievable, so nobody mistakes a placeholder for a filed request.
 */
export default async function PaRequestDetailPage({ params }: { params: { id: string } }) {
  const session = await getSession();
  if (!session) redirect('/login');

  // A malformed id is a 404, not a lookup.
  if (!UUID_PATTERN.test(params.id)) notFound();

  return (
    <AppShell
      active="pa-requests"
      clinicianName={session.clinicianName}
      practiceName={session.practiceName}
      devSession={session.dev === true}
    >
      <Link
        href="/pa-requests"
        className="mb-6 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        All requests
      </Link>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            Request <span className="font-mono text-sm">{params.id.slice(0, 8)}</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-muted-foreground">
          <p>
            This request cannot be displayed yet. Submitted requests are persisted by
            Clinchec Forms, but the retrieval endpoint that would populate this page is
            still in progress.
          </p>
          <p>
            Until it ships, the packet returned at submission time is the record of
            what was sent.
          </p>
        </CardContent>
      </Card>
    </AppShell>
  );
}
