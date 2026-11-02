// Generated from canonical Pydantic JSON Schemas. DO NOT EDIT.
import type { components } from "../openapi.generated";

export type PublicV12SchemaValidationError = Readonly<{
  instancePath: string;
  schemaPath: string;
  keyword: string;
  params: Readonly<Record<string, unknown>>;
  message?: string;
}>;

export interface PublicV12SchemaValidator<T> {
  (value: unknown): value is T;
  errors?: readonly PublicV12SchemaValidationError[] | null;
}

export const validatePremarketBriefingV1_2: PublicV12SchemaValidator<
  components["schemas"]["PremarketBriefingViewV1_2"]
>;
export const validateEventEvidenceV1_2: PublicV12SchemaValidator<
  components["schemas"]["EventEvidenceViewV1_2"]
>;
