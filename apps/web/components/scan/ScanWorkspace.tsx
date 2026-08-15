'use client';

import * as React from 'react';

import type { ScanResult } from '@clinchec/shared-types';

import { Card, CardContent } from '@/components/ui/card';
import { ExtractionResult } from '@/components/scan/ExtractionResult';
import { SoapNoteInput } from '@/components/scan/SoapNoteInput';

/**
 * Composes the note input with its result.
 *
 * The scanned note is held alongside the result rather than read back from the
 * textarea, so the highlight offsets always refer to the exact text that was
 * scanned even if the clinician keeps editing afterwards.
 */
export function ScanWorkspace({
  initialNote,
  initialPayerSlug,
  noteSourceLabel,
}: {
  initialNote?: string;
  initialPayerSlug?: string;
  noteSourceLabel?: string;
}) {
  const [scan, setScan] = React.useState<{ result: ScanResult; note: string } | null>(null);
  const resultRef = React.useRef<HTMLDivElement>(null);

  const handleResult = React.useCallback((result: ScanResult, note: string) => {
    setScan({ result, note });
    // Move focus to the result so keyboard and screen-reader users are not
    // left at the bottom of a textarea wondering whether anything happened.
    requestAnimationFrame(() => {
      resultRef.current?.focus({ preventScroll: false });
      resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }, []);

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      <div className="space-y-3">
        {noteSourceLabel ? (
          <p className="text-xs text-muted-foreground">
            Pre-filled from the EHR: <span className="font-medium">{noteSourceLabel}</span>
          </p>
        ) : null}
        <SoapNoteInput
          initialNote={initialNote}
          initialPayerSlug={initialPayerSlug}
          onResult={handleResult}
          onReset={() => setScan(null)}
        />
      </div>

      <div ref={resultRef} tabIndex={-1} className="focus:outline-none" aria-live="polite">
        {scan ? (
          <ExtractionResult result={scan.result} note={scan.note} />
        ) : (
          <EmptyResultState />
        )}
      </div>
    </div>
  );
}

/**
 * The right-hand pane before anything has been scanned.
 *
 * Not a centred icon in a circle. This pane is read by a clinician mid-consult
 * with the patient watching, and it has one job: say what will appear here and
 * how to make it appear. Left-aligned in the position the results will occupy,
 * so the eye does not have to move when they arrive.
 */
function EmptyResultState() {
  return (
    <Card className="h-full border-dashed">
      <CardContent className="flex h-full min-h-[420px] flex-col justify-center gap-5 py-10">
        <div className="max-w-md space-y-2">
          <p className="text-sm font-medium">Results appear here</p>
          <p className="text-sm leading-relaxed text-muted-foreground">
            Extracted diagnosis and procedure codes, the payer&rsquo;s current
            criteria for that procedure, and what the note is missing against
            them.
          </p>
        </div>
        <p className="text-xs text-muted-foreground">
          Paste a note and press{' '}
          <kbd className="rounded border bg-muted px-1.5 py-0.5 font-mono text-[10px]">
            Ctrl
          </kbd>
          {' + '}
          <kbd className="rounded border bg-muted px-1.5 py-0.5 font-mono text-[10px]">
            Enter
          </kbd>
        </p>
      </CardContent>
    </Card>
  );
}
