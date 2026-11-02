export type ApiErrorCode =
  | "not_found"
  | "provenance_unavailable"
  | "rights_blocked"
  | "dependency_unavailable"
  | "invalid_payload"
  | "network_error";

export type ApiResult<T> =
  | { data: T; error: null }
  | { data: null; error: { code: ApiErrorCode; status: number | null } };

export function apiErrorCode(status: number): ApiErrorCode {
  if (status === 404) {
    return "not_found";
  }
  if (status === 412) {
    return "provenance_unavailable";
  }
  if (status === 401 || status === 403) {
    return "rights_blocked";
  }
  return "dependency_unavailable";
}
