'use client';

import * as React from 'react';
import { ArrowRight, ScanLine } from 'lucide-react';

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

function EmptyResultState() {
  return (
    <Card className="h-full border-dashed">
      <CardContent className="flex h-full min-h-[420px] flex-col items-center justify-center gap-4 text-center">
        <span className="flex size-12 items-center justify-center rounded-full bg-muted">
          <ScanLine className="size-5 text-muted-foreground" aria-hidden="true" />
        </span>
        <div className="max-w-sm space-y-1.5">
          <p className="font-medium">Nothing scanned yet</p>
          <p className="text-sm text-muted-foreground">
            Paste a SOAP note and press Scan. Clinchec extracts the diagnosis and
            procedure codes, checks them against the payer&rsquo;s current criteria, and
            estimates the approval likelihood before you submit.
          </p>
        </div>
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <kbd className="rounded border bg-muted px-1.5 py-0.5 font-mono text-[10px]">
            Ctrl
          </kbd>
          <span>+</span>
          <kbd className="rounded border bg-muted px-1.5 py-0.5 font-mono text-[10px]">
            Enter
          </kbd>
          <span>to scan</span>
          <ArrowRight className="size-3" aria-hidden="true" />
        </div>
      </CardContent>
    </Card>
  );
}
