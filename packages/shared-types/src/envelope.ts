import { z } from 'zod';

/**
 * The `{ data, error, meta }` envelope every Clinchec service returns.
 *
 * Validating on the client is not ceremony: the Python services are the source
 * of truth for these shapes, and a schema drift between a deployed service and
 * a deployed frontend should surface as a named parse error at the boundary
 * rather than as `undefined` rendering somewhere deep in a component tree.
 */

export const errorBodySchema = z.object({
  code: z.string(),
  message: z.string(),
  details: z.record(z.unknown()).nullish(),
});

export const metaSchema = z.object({
  request_id: z.string(),
  timestamp: z.string(),
  service: z.string(),
  version: z.string(),
  duration_ms: z.number().nullish(),
  extra: z.record(z.unknown()).default({}),
});

export type ErrorBody = z.infer<typeof errorBodySchema>;
export type Meta = z.infer<typeof metaSchema>;

export const envelopeSchema = <T extends z.ZodTypeAny>(data: T) =>
  z.object({
    data: data.nullable(),
    error: errorBodySchema.nullable(),
    meta: metaSchema,
  });

export type Envelope<T> = {
  data: T | null;
  error: ErrorBody | null;
  meta: Meta;
};

/** Thrown when a service returns an enveloped error, carrying the machine code. */
export class ClinchecApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly details?: Record<string, unknown> | null,
    readonly status?: number,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = 'ClinchecApiError';
  }
}
