/** Shared query-layer helpers (error classification without importing React). */
export function isAxiosLike(
  error: unknown,
): error is { status: number; message: string; url: string } {
  return (
    typeof error === "object" &&
    error !== null &&
    "status" in error &&
    typeof (error as { status: unknown }).status === "number"
  );
}

/** Human one-liner for a failed request (used by toasts / error states). */
export function errMessage(error: unknown): string {
  if (isAxiosLike(error)) return error.message || `HTTP ${error.status}`;
  if (error instanceof Error) return error.message;
  return String(error);
}
