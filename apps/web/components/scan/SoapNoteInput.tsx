'use client';

import * as React from 'react';
import { FileText, Loader2, RotateCcw, ScanLine, Sparkles } from 'lucide-react';

import { ClinchecApiError, type ScanResult } from '@clinchec/shared-types';

import { extractSoapNote } from '@/lib/api';
import { cn } from '@/lib/utils';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';

/**
 * The SOAP note entry surface.
 *
 * The physician pastes or types a note and presses Scan. Two constraints shape
 * this component:
 *
 * 1. **The note is PHI.** It is held in component state and posted to Scan; it
 *    is never written to localStorage, never put in the URL, and never logged.
 * 2. **This sits inside an EHR workflow.** Ctrl/Cmd+Enter scans, because a
 *    clinician between patients is not reaching for a mouse.
 *
 * A scan that is superseded by another is aborted rather than left to race —
 * otherwise a slow first request can overwrite the result of a faster second.
 */

const MIN_CHARS = 20;
const SOFT_MAX_CHARS = 30_000;

/**
 * Lines of business, because the same payer adjudicates the same CPT
 * differently across them. UnitedHealthcare states its own criteria for a total
 * knee replacement under a commercial plan and routes the Medicare Advantage
 * version to a CMS coverage determination — genuinely different answers.
 *
 * Without this the rules service returns whichever record it holds by default,
 * which is a recency tiebreak: a knee scan silently landed on Medicare
 * Advantage criteria because that rule happened to be crawled last.
 */
const PLAN_TYPES = [
  { value: 'commercial', label: 'Commercial' },
  { value: 'medicare_advantage', label: 'Medicare Advantage' },
  { value: 'medicaid', label: 'Medicaid' },
  { value: 'exchange', label: 'Exchange' },
] as const;

const PAYERS = [
  { slug: '', label: 'National baseline' },
  { slug: 'aetna', label: 'Aetna' },
  { slug: 'bcbs', label: 'Blue Cross Blue Shield' },
  { slug: 'uhc', label: 'UnitedHealthcare' },
] as const;

const SAMPLE_NOTE = `S: 62 y/o female presents with right knee pain for 14 months. She has completed
12 weeks of physical therapy, a corticosteroid injection, and activity modification
without lasting relief. She is unable to climb stairs and reports difficulty walking
more than one block. Denies fever, night sweats, or recent trauma.

O: Weight-bearing radiographs show bone-on-bone medial compartment narrowing.
Antalgic gait. Range of motion 5-110 degrees. No effusion.

A: Right knee osteoarthritis, tricompartmental, end stage.

P: Proceed with total knee arthroplasty, right. Pre-operative clearance ordered.`;

export interface SoapNoteInputProps {
  /** Pre-filled note, e.g. the latest DocumentReference from the EHR launch. */
  initialNote?: string;
  initialPayerSlug?: string;
  onResult?: (result: ScanResult, note: string) => void;
  onReset?: () => void;
  className?: string;
}

export function SoapNoteInput({
  initialNote = '',
  initialPayerSlug = '',
  onResult,
  onReset,
  className,
}: SoapNoteInputProps) {
  const [note, setNote] = React.useState(initialNote);
  const [payerSlug, setPayerSlug] = React.useState<string>(initialPayerSlug);
  const [planType, setPlanType] = React.useState<string>('commercial');
  const [draftJustification, setDraftJustification] = React.useState(false);
  const [isScanning, setIsScanning] = React.useState(false);
  const [error, setError] = React.useState<{ title: string; message: string } | null>(null);

  const abortRef = React.useRef<AbortController | null>(null);
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);

  // Abandon any request still in flight when the component goes away.
  React.useEffect(() => () => abortRef.current?.abort(), []);

  const trimmedLength = note.trim().length;
  const tooShort = trimmedLength > 0 && trimmedLength < MIN_CHARS;
  const canScan = trimmedLength >= MIN_CHARS && !isScanning;

  const handleScan = React.useCallback(async () => {
    if (note.trim().length < MIN_CHARS) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setIsScanning(true);
    setError(null);

    try {
      const result = await extractSoapNote(
        {
          note,
          payer_slug: payerSlug || null,
          // Only meaningful alongside a payer; the national baseline has no
          // lines of business, and sending one would imply a distinction the
          // rule engine does not make.
          plan_type: payerSlug ? planType : null,
          draft_justification: draftJustification,
        },
        { signal: controller.signal },
      );
      if (!controller.signal.aborted) {
        onResult?.(result, note);
      }
    } catch (caught) {
      if (controller.signal.aborted) return;
      if (caught instanceof ClinchecApiError) {
        setError({ title: errorTitle(caught.code), message: caught.message });
      } else {
        setError({
          title: 'Scan failed',
          message: 'Something went wrong reading this note. Please try again.',
        });
      }
    } finally {
      if (!controller.signal.aborted) setIsScanning(false);
    }
  }, [note, payerSlug, planType, draftJustification, onResult]);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault();
      void handleScan();
    }
  };

  const handleClear = () => {
    abortRef.current?.abort();
    setNote('');
    setError(null);
    setIsScanning(false);
    onReset?.();
    textareaRef.current?.focus();
  };

  return (
    <Card className={cn('overflow-hidden', className)}>
      <CardHeader className="flex-row items-center justify-between space-y-0 border-b bg-muted/30 py-4">
        <CardTitle className="flex items-center gap-2 text-base">
          <FileText className="size-4 text-muted-foreground" aria-hidden="true" />
          SOAP note
        </CardTitle>
        <div className="flex items-center gap-2">
          {note.length === 0 ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setNote(SAMPLE_NOTE)}
              className="text-muted-foreground"
            >
              <Sparkles className="size-3.5" aria-hidden="true" />
              Load sample
            </Button>
          ) : (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={handleClear}
              className="text-muted-foreground"
            >
              <RotateCcw className="size-3.5" aria-hidden="true" />
              Clear
            </Button>
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-4 p-0">
        <div className="relative">
          <Label htmlFor="soap-note" className="sr-only">
            Paste or type the SOAP note
          </Label>
          <Textarea
            id="soap-note"
            ref={textareaRef}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            onKeyDown={handleKeyDown}
            spellCheck={false}
            // Browser autofill and grammar services would ship PHI off-device.
            autoComplete="off"
            data-gramm="false"
            data-1p-ignore
            placeholder={
              'Paste the note, or start typing:\n\n' +
              'S: 54 y/o male with low back pain for 3 months…\n' +
              'O: Positive straight leg raise on the right…\n' +
              'A: Lumbar radiculopathy…\n' +
              'P: Order MRI lumbar spine…'
            }
            aria-describedby="note-help note-count"
            aria-invalid={tooShort || undefined}
            className={cn(
              'min-h-[380px] resize-y rounded-none border-0 font-mono text-[13px] leading-relaxed',
              'focus-visible:ring-0 focus-visible:ring-offset-0',
            )}
          />

          {isScanning ? (
            <div
              className="pointer-events-none absolute inset-0 flex items-center justify-center bg-background/70 backdrop-blur-[1px]"
              role="status"
              aria-live="polite"
            >
              <span className="flex items-center gap-2 rounded-full border bg-card px-4 py-2 text-sm shadow-sm">
                <Loader2 className="size-4 animate-spin text-primary" aria-hidden="true" />
                Reading the note…
              </span>
            </div>
          ) : null}
        </div>

        {error ? (
          <div className="px-6">
            <Alert variant="destructive">
              <AlertTitle>{error.title}</AlertTitle>
              <AlertDescription>{error.message}</AlertDescription>
            </Alert>
          </div>
        ) : null}

        <div className="flex flex-col gap-4 border-t bg-muted/20 px-6 py-4 sm:flex-row sm:items-end sm:justify-between">
          <div className="flex flex-wrap items-end gap-5">
            <div className="space-y-1.5">
              <Label htmlFor="payer" className="text-xs text-muted-foreground">
                Score against
              </Label>
              <select
                id="payer"
                value={payerSlug}
                onChange={(event) => setPayerSlug(event.target.value)}
                className={cn(
                  'h-9 rounded-md border border-input bg-background px-3 text-sm',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                )}
              >
                {PAYERS.map((payer) => (
                  <option key={payer.slug || 'baseline'} value={payer.slug}>
                    {payer.label}
                  </option>
                ))}
              </select>
            </div>

            {payerSlug ? (
              <div className="space-y-1.5">
                <Label htmlFor="plan-type" className="text-xs text-muted-foreground">
                  Plan
                </Label>
                <select
                  id="plan-type"
                  value={planType}
                  onChange={(event) => setPlanType(event.target.value)}
                  className={cn(
                    'h-9 rounded-md border border-input bg-background px-3 text-sm',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  )}
                >
                  {PLAN_TYPES.map((plan) => (
                    <option key={plan.value} value={plan.value}>
                      {plan.label}
                    </option>
                  ))}
                </select>
              </div>
            ) : null}

            <div className="flex items-center gap-2 pb-2">
              <Checkbox
                id="draft-justification"
                checked={draftJustification}
                onCheckedChange={(checked) => setDraftJustification(checked === true)}
              />
              <Label htmlFor="draft-justification" className="text-sm font-normal">
                Draft justification
              </Label>
            </div>
          </div>

          <div className="flex items-center gap-4">
            <p id="note-count" className="text-xs tabular-nums text-muted-foreground">
              {tooShort ? (
                <span className="text-destructive">
                  {MIN_CHARS - trimmedLength} more characters needed
                </span>
              ) : (
                <>
                  {note.length.toLocaleString()}
                  {note.length > SOFT_MAX_CHARS ? (
                    <span className="text-caution-foreground">
                      {' '}
                      · will be truncated at {SOFT_MAX_CHARS.toLocaleString()}
                    </span>
                  ) : (
                    ' characters'
                  )}
                </>
              )}
            </p>

            <Button type="button" onClick={handleScan} disabled={!canScan} size="lg">
              {isScanning ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : (
                <ScanLine aria-hidden="true" />
              )}
              {isScanning ? 'Scanning' : 'Scan'}
            </Button>
          </div>
        </div>

        <p id="note-help" className="sr-only">
          Press Control or Command plus Enter to scan the note.
        </p>
      </CardContent>
    </Card>
  );
}

function errorTitle(code: string): string {
  switch (code) {
    case 'note_too_short':
      return 'Note is too short';
    case 'unknown_cpt':
      return 'Unrecognised procedure code';
    case 'network_error':
      return 'Cannot reach Clinchec Scan';
    case 'schema_mismatch':
      return 'Version mismatch';
    default:
      return 'Scan failed';
  }
}
