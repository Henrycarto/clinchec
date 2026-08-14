import type { Metadata } from 'next';
import { redirect } from 'next/navigation';

import { getFhirClient, loadLaunchContext } from '@/lib/fhir';
import { getSession } from '@/lib/session';
import { AppShell } from '@/components/AppShell';
import { ScanWorkspace } from '@/components/scan/ScanWorkspace';

export const metadata: Metadata = { title: 'Scan' };

// Session state and PHI make this uncacheable by construction.
export const dynamic = 'force-dynamic';

export default async function DashboardPage() {
  const session = await getSession();
  if (!session) redirect('/login');

  // When the app was launched from the EHR we can pre-fill the note and payer
  // from the patient in context. A tenant that has not granted those scopes
  // still gets a working blank workspace rather than an error.
  let initialNote: string | undefined;
  let initialPayerSlug: string | undefined;
  let noteSourceLabel: string | undefined;

  const client = await getFhirClient();
  if (client?.patientId) {
    try {
      const context = await loadLaunchContext(client);
      initialNote = context.latestNote?.text;
      initialPayerSlug = context.payerSlug;
      noteSourceLabel = context.latestNote
        ? [context.latestNote.title ?? 'Clinical note', context.latestNote.date?.slice(0, 10)]
            .filter(Boolean)
            .join(' · ')
        : undefined;
    } catch (error) {
      console.error('Could not load EHR launch context', error);
    }
  }

  return (
    <AppShell
      active="dashboard"
      clinicianName={session.clinicianName}
      practiceName={session.practiceName}
      devSession={session.dev === true}
    >
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Pre-screen a prior authorization</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Paste the note. Clinchec extracts the codes, checks the payer&rsquo;s current
          criteria, and tells you what is missing before the payer does.
        </p>
      </div>

      <ScanWorkspace
        initialNote={initialNote}
        initialPayerSlug={initialPayerSlug}
        noteSourceLabel={noteSourceLabel}
      />
    </AppShell>
  );
}
