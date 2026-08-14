'use client';

import * as React from 'react';
import { CircleAlert, Pencil, Sparkles, Wand2 } from 'lucide-react';

import type { MappedField, MappingConfidence } from '@clinchec/shared-types';

import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';

/**
 * Renders one auto-populated form field, with its provenance.
 *
 * The provenance badge is the point of this component. Auto-population is only
 * trustworthy if the clinician can see at a glance which values were copied
 * from a system of record and which were inferred from prose by the NLP
 * pipeline — the second kind is what they need to read before signing.
 *
 * Editing any field marks it as clinician-entered, which both removes the
 * review flag and takes precedence server-side on submit.
 */

const CONFIDENCE_PRESENTATION: Record<
  MappingConfidence,
  { label: string; badge: 'muted' | 'secondary' | 'caution' | 'outline'; explain: string }
> = {
  exact: {
    label: 'From record',
    badge: 'muted',
    explain: 'Copied verbatim from the EHR or entered by you.',
  },
  derived: {
    label: 'Reformatted',
    badge: 'secondary',
    explain: 'Computed or reformatted from structured data — e.g. a date or phone number.',
  },
  inferred: {
    label: 'From note',
    badge: 'caution',
    explain: 'Extracted from the note text by Clinchec Scan. Please verify before submitting.',
  },
  missing: {
    label: 'Not found',
    badge: 'outline',
    explain: 'No source was available. Complete this manually.',
  },
};

export interface FieldMapperProps {
  field: MappedField;
  value: unknown;
  onChange: (key: string, value: unknown) => void;
  edited?: boolean;
  options?: string[];
  maxLength?: number | null;
  className?: string;
}

export function FieldMapper({
  field,
  value,
  onChange,
  edited = false,
  options = [],
  maxLength,
  className,
}: FieldMapperProps) {
  const presentation = CONFIDENCE_PRESENTATION[edited ? 'exact' : field.confidence];
  const isMissingRequired = field.required && isBlank(value);
  const inputId = `field-${field.key}`;

  return (
    <div
      className={cn(
        'space-y-1.5 rounded-md border p-3 transition-colors',
        isMissingRequired
          ? 'border-deny-border bg-deny-surface/40'
          : field.confidence === 'inferred' && !edited
            ? 'border-caution-border bg-caution-surface/30'
            : 'border-transparent',
        className,
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Label htmlFor={inputId} className="flex items-center gap-1.5">
          {field.label}
          {field.required ? (
            <span className="text-destructive" aria-label="required">
              *
            </span>
          ) : null}
        </Label>

        <TooltipProvider delayDuration={150}>
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge variant={presentation.badge} className="cursor-help">
                {edited ? (
                  <Pencil className="size-2.5" aria-hidden="true" />
                ) : field.confidence === 'inferred' ? (
                  <Sparkles className="size-2.5" aria-hidden="true" />
                ) : field.confidence === 'derived' ? (
                  <Wand2 className="size-2.5" aria-hidden="true" />
                ) : null}
                {edited ? 'Edited' : presentation.label}
              </Badge>
            </TooltipTrigger>
            <TooltipContent>
              {edited ? 'You entered this value.' : presentation.explain}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>

      {field.type === 'checkbox' ? (
        <div className="flex items-center gap-2 pt-1">
          <Checkbox
            id={inputId}
            checked={value === true}
            onCheckedChange={(checked) => onChange(field.key, checked === true)}
          />
          <Label htmlFor={inputId} className="text-sm font-normal text-muted-foreground">
            {value === true ? 'Attested' : 'Not attested'}
          </Label>
        </div>
      ) : field.type === 'select' && options.length > 0 ? (
        <select
          id={inputId}
          value={typeof value === 'string' ? value : ''}
          onChange={(event) => onChange(field.key, event.target.value)}
          className={cn(
            'h-10 w-full rounded-md border border-input bg-background px-3 text-sm',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          )}
        >
          <option value="">—</option>
          {options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      ) : field.type === 'textarea' ? (
        <Textarea
          id={inputId}
          value={typeof value === 'string' ? value : ''}
          maxLength={maxLength ?? undefined}
          rows={5}
          onChange={(event) => onChange(field.key, event.target.value)}
          aria-invalid={isMissingRequired || undefined}
        />
      ) : (
        <Input
          id={inputId}
          type={field.type === 'number' ? 'number' : field.type === 'date' ? 'text' : 'text'}
          inputMode={field.type === 'number' ? 'numeric' : undefined}
          value={value === null || value === undefined ? '' : String(value)}
          maxLength={maxLength ?? undefined}
          onChange={(event) =>
            onChange(
              field.key,
              field.type === 'number' && event.target.value !== ''
                ? Number(event.target.value)
                : event.target.value,
            )
          }
          aria-invalid={isMissingRequired || undefined}
        />
      )}

      {isMissingRequired ? (
        <p className="flex items-center gap-1.5 text-xs text-deny-foreground">
          <CircleAlert className="size-3" aria-hidden="true" />
          {field.type === 'checkbox'
            ? 'This payer requires the attestation. The note does not support it yet.'
            : 'Required by this payer.'}
        </p>
      ) : field.note && !edited ? (
        <p className="text-xs text-muted-foreground">{field.note}</p>
      ) : null}
    </div>
  );
}

function isBlank(value: unknown): boolean {
  if (value === null || value === undefined || value === false) return true;
  if (typeof value === 'string') return value.trim() === '';
  if (Array.isArray(value)) return value.length === 0;
  return false;
}

export { isBlank };
