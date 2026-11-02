// Generated from canonical Pydantic JSON Schemas. DO NOT EDIT.
export type PublicV12FormatValidator = (
  value: string,
) => boolean;

declare const publicV12Formats: Readonly<{
  "date-time": PublicV12FormatValidator;
  uri: PublicV12FormatValidator;
}>;

export = publicV12Formats;
