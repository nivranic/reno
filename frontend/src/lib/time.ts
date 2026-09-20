/** Time helpers. App-internal time is INTEGER MILLISECONDS everywhere;
 * seconds conversion happens only at the <video> boundary. */

export function clampMs(ms: number, min: number, max: number): number {
  return Math.min(Math.max(Math.round(ms), min), max);
}

/** 123456 -> "02:03.4" (mm:ss.d) */
export function fmtMs(ms: number): string {
  if (!Number.isFinite(ms)) return "--:--.-";
  const clamped = Math.max(0, ms);
  const m = Math.floor(clamped / 60_000);
  const s = Math.floor((clamped % 60_000) / 1000);
  const d = Math.floor((clamped % 1000) / 100);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${d}`;
}

/** 123456 -> "2′03″" compact duration for table cells */
export function fmtDur(ms: number | null | undefined): string {
  if (!ms || ms <= 0) return "—";
  const m = Math.floor(ms / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  return m > 0 ? `${m}′${String(s).padStart(2, "0")}″` : `${s}″`;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(iso);
  return m ? `${m[1]} ${m[2]}` : iso;
}

/** Parse a ?t= deep-link value. Returns null for garbage/negative. */
export function parseDeepLinkMs(raw: string | null): number | null {
  if (raw == null) return null;
  const n = Number(raw);
  if (!Number.isFinite(n) || n < 0) return null;
  return Math.round(n);
}

/** Binary search: index of the LAST event whose ms <= t (events sorted by ms).
 * Returns -1 when t is before the first event. */
export function findEventIndex<T extends { ms: number }>(events: T[], t: number): number {
  let lo = 0;
  let hi = events.length - 1;
  let res = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (events[mid].ms <= t) {
      res = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return res;
}

/** All events active at time t (real ranges; overlaps allowed; point events
 * are NOT extended). Scan window is bounded by looking back from the binary-
 * search index while start <= t. Output is chronological. */
export function activeEvents<T extends { ms: number; end: number }>(
  events: T[],
  t: number,
): T[] {
  const idx = findEventIndex(events, t);
  const out: T[] = [];
  for (let i = idx; i >= 0; i--) {
    const e = events[i];
    if (e.ms > t) continue;
    if (t <= (Number.isFinite(e.end) ? e.end : e.ms)) out.push(e);
  }
  return out.reverse();
}
