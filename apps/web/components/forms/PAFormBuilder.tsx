'use client';

import * as React from 'react';
import {
  CheckCircle2,
  Download,
  Loader2,
  Send,
  ShieldAlert,
  Sparkles,
} from 'lucide-react';

import {
  ClinchecApiError,
  type FormDefinition,
  type MappingResult,
  type PaPayload,
  type SubmissionResult,
} from '@clinchec/shared-types';

import { populateForm, submitForm } from '@/lib/api';
import { cn } from '@/lib/utils';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Separator } from '@/components/ui/separator';
import { FieldMapper, isBlank } from '@/components/forms/FieldMapper';

/**
 * The one-click PA form.
 *
 * Population happens server-side in Clinchec Forms so the mapping rules stay in
 * one place. This component owns the clinician's edits, keeps them separate
 * from the auto-populated values, and sends them as `overrides` on submit —
 * the server re-maps from the source payload each time and layers the edits on
 * top, so a stale client-side value can never silently become what is filed.
 */

export interface PAFormBuilderProps {
  payload: PaPayload;
  form?: FormDefinition;
  className?: string;
  onSubmitted?: (result: SubmissionResult) => void;
}

export function PAFormBuilder({ payload, form, className, onSubmitted }: PAFormBuilderProps) {
  const [mapping, setMapping] = React.useState<MappingResult | null>(null);
  const [overrides, setOverrides] = React.useState<Record<string, unknown>>({});
  const [isLoading, setIsLoading] = React.useState(true);
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const [submission, setSubmission] = React.useState<SubmissionResult | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const formKey = form?.form_key;

  React.useEffect(() => {
    const controller = new AbortController();

    setIsLoading(true);
    setError(null);

    populateForm(payload, formKey, { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) setMapping(result);
      })
      .catch((caught) => {
        if (controller.signal.aborted) return;
        setError(
          caught instanceof ClinchecApiError
            ? caught.message
            : 'Could not load the prior authorization form.',
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false);
      });

    return () => controller.abort();
  }, [payload, formKey]);

  const handleChange = React.useCallback((key: string, value: unknown) => {
    setOverrides((current) => ({ ...current, [key]: value }));
  }, []);

  const effectiveValue = React.useCallback(
    (key: string, mapped: unknown) => (key in overrides ? overrides[key] : mapped),
    [overrides],
  );

  // Recompute completeness locally so the clinician sees edits take effect
  // immediately rather than after a round trip.
  const { missingRequired, needsReview, completeness } = React.useMemo(() => {
    if (!mapping) return { missingRequired: [], needsReview: [], completeness: 0 };

    const missing = mapping.fields
      .filter((field) => field.required && isBlank(effectiveValue(field.key, field.value)))
      .map((field) => field.key);

    const review = mapping.fields
      .filter(
        (field) =>
          field.confidence === 'inferred' &&
          !(field.key in overrides) &&
          !isBlank(field.value),
      )
      .map((field) => field.key);

    const fillable = mapping.fields.filter((field) => field.source || field.key in overrides);
    const filled = fillable.filter(
      (field) => !isBlank(effectiveValue(field.key, field.value)),
    ).length;

    return {
      missingRequired: missing,
      needsReview: review,
      completeness: fillable.length ? filled / fillable.length : 0,
    };
  }, [mapping, overrides, effectiveValue]);

  const handleSubmit = async (allowIncomplete = false) => {
    if (!mapping) return;
    setIsSubmitting(true);
    setError(null);

    try {
      const result = await submitForm(payload, {
        formKey: mapping.form_key,
        overrides,
        allowIncomplete,
      });
      setSubmission(result);
      onSubmitted?.(result);
    } catch (caught) {
      setError(
        caught instanceof ClinchecApiError
          ? caught.message
          : 'Could not submit the prior authorization request.',
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isLoading) return <FormSkeleton className={className} />;

  if (error && !mapping) {
    return (
      <Alert variant="destructive" className={className}>
        <ShieldAlert aria-hidden="true" />
        <AlertTitle>Form unavailable</AlertTitle>
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }

  if (!mapping) return null;

  if (submission) {
    return <SubmissionSummary result={submission} className={className} />;
  }

  const sections = groupBySection(mapping);

  return (
    <div className={cn('space-y-5', className)}>
      <Card>
        <CardHeader className="pb-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle className="text-base">{mapping.display_name}</CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                {mapping.payer_slug === '*' ? 'Universal form' : mapping.payer_slug.toUpperCase()}
                <span className="mx-1.5 opacity-40">·</span>
                submits by {channelLabel(mapping.channel)}
              </p>
            </div>
            <Badge variant={missingRequired.length === 0 ? 'approve' : 'caution'}>
              {Math.round(completeness * 100)}% complete
            </Badge>
          </div>

          <Progress
            value={completeness * 100}
            className="mt-3"
            indicatorClassName={missingRequired.length === 0 ? 'bg-approve' : 'bg-caution'}
            aria-label={`Form ${Math.round(completeness * 100)} percent complete`}
          />
        </CardHeader>

        <CardContent className="space-y-2">
          {missingRequired.length > 0 ? (
            <Alert variant="deny">
              <ShieldAlert aria-hidden="true" />
              <AlertTitle>
                {missingRequired.length} required field
                {missingRequired.length === 1 ? '' : 's'} still needed
              </AlertTitle>
              <AlertDescription>
                Submitting without these produces an administrative denial rather than a
                clinical review.
              </AlertDescription>
            </Alert>
          ) : null}

          {needsReview.length > 0 ? (
            <Alert variant="caution">
              <Sparkles aria-hidden="true" />
              <AlertTitle>
                {needsReview.length} field{needsReview.length === 1 ? '' : 's'} extracted from
                the note
              </AlertTitle>
              <AlertDescription>
                These were read from your prose rather than a structured record. Check them
                before submitting; editing a field clears its flag.
              </AlertDescription>
            </Alert>
          ) : null}
        </CardContent>
      </Card>

      {sections.map(([section, fields]) => (
        <Card key={section}>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              {section}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-2">
            {fields.map((field) => {
              const definition = form?.fields.find((entry) => entry.key === field.key);
              return (
                <FieldMapper
                  key={field.key}
                  field={field}
                  value={effectiveValue(field.key, field.value)}
                  edited={field.key in overrides}
                  onChange={handleChange}
                  options={definition?.options ?? []}
                  maxLength={definition?.max_length ?? null}
                  className={field.type === 'textarea' ? 'md:col-span-2' : undefined}
                />
              );
            })}
          </CardContent>
        </Card>
      ))}

      {error ? (
        <Alert variant="destructive">
          <ShieldAlert aria-hidden="true" />
          <AlertTitle>Submission failed</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      <div className="flex flex-wrap items-center justify-end gap-3">
        <Button
          type="button"
          variant="outline"
          disabled={isSubmitting}
          onClick={() => handleSubmit(true)}
        >
          <Download aria-hidden="true" />
          Export packet
        </Button>
        <Button
          type="button"
          size="lg"
          disabled={isSubmitting || missingRequired.length > 0}
          onClick={() => handleSubmit(false)}
        >
          {isSubmitting ? (
            <Loader2 className="animate-spin" aria-hidden="true" />
          ) : (
            <Send aria-hidden="true" />
          )}
          {isSubmitting ? 'Submitting' : 'Submit to payer'}
        </Button>
      </div>
    </div>
  );
}

// --- Helpers ----------------------------------------------------------------

function groupBySection(mapping: MappingResult) {
  const map = new Map<string, MappingResult['fields']>();
  for (const field of mapping.fields) {
    const bucket = map.get(field.section) ?? [];
    bucket.push(field);
    map.set(field.section, bucket);
  }
  return Array.from(map.entries());
}

function channelLabel(channel: string): string {
  return (
    {
      portal: 'payer portal',
      fax: 'fax',
      x12_278: 'EDI 278',
      fhir_crd: 'FHIR CRD',
      export: 'download',
    }[channel] ?? channel
  );
}

function SubmissionSummary({
  result,
  className,
}: {
  result: SubmissionResult;
  className?: string;
}) {
  const transmitted = result.status === 'submitted';
  return (
    <Card
      className={cn(
        transmitted ? 'border-approve-border bg-approve-surface/40' : 'border-caution-border',
        className,
      )}
    >
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <CheckCircle2
            className={cn('size-5', transmitted ? 'text-approve-foreground' : 'text-caution-foreground')}
            aria-hidden="true"
          />
          {transmitted ? 'Submitted' : 'Packet ready'}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p>{result.message}</p>
        <Separator />
        <dl className="grid grid-cols-2 gap-2 text-xs">
          <dt className="text-muted-foreground">Request ID</dt>
          <dd className="font-mono">{result.pa_request_id.slice(0, 8)}</dd>
          <dt className="text-muted-foreground">Form</dt>
          <dd>{result.form_key}</dd>
          {result.submission_ref ? (
            <>
              <dt className="text-muted-foreground">Payer reference</dt>
              <dd className="font-mono">{result.submission_ref}</dd>
            </>
          ) : null}
        </dl>
        {result.export_url ? (
          <Button asChild variant="outline" size="sm">
            <a href={result.export_url} download>
              <Download aria-hidden="true" />
              Download packet
            </a>
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}

function FormSkeleton({ className }: { className?: string }) {
  return (
    <div className={cn('space-y-4', className)} aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading the prior authorization form…</span>
      <div className="skeleton h-28 w-full" />
      <div className="skeleton h-64 w-full" />
      <div className="skeleton h-64 w-full" />
    </div>
  );
}
