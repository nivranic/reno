/** Per-video workbench store: ONLY cross-panel linkage + UI state.
 * Playback time deliberately stays OUT of the store (it lives in the page at
 * ~4Hz + rAF) so high-frequency updates never re-render the atom panel etc.
 *
 * The single "locate" protocol drives every navigation source (atom click,
 * evidence chip, timeline click, deep link) — only explicit locate commands
 * seek the player; playback time reporting is observation-only (no loop). */
import { create, type StoreApi, type UseBoundStore } from "zustand";
import { useContext, createContext } from "react";
import type { Modality } from "@/lib/api-types";

export interface LocateOptions {
  atomId?: string;
  evId?: string;
  /** write ?t=ms into the address bar (default true for user actions) */
  updateUrl?: boolean;
}

export interface WorkbenchState {
  videoId: string;
  /** target of the latest explicit locate command (null = none yet) */
  requestedMs: number | null;
  selectedAtomId: string | null;
  /** specific evidence within the selected atom (null → its first evidence) */
  selectedEvId: string | null;
  follow: boolean;
  modFilter: Record<Modality, boolean>;
  /** set once the user performs any explicit action */
  userActed: boolean;
  locate: (ms: number, opts?: LocateOptions) => void;
  selectEvidence: (atomId: string, evId: string | null) => void;
  setFollow: (v: boolean) => void;
  toggleMod: (m: Modality) => void;
}

export function createWorkbenchStore(videoId: string) {
  return create<WorkbenchState>((set) => ({
    videoId,
    requestedMs: null,
    selectedAtomId: null,
    selectedEvId: null,
    follow: true,
    modFilter: { ASR: true, OCR: true, VIS: true },
    userActed: false,
    locate: (ms, opts) => {
      set((s) => ({
        requestedMs: Math.max(0, Math.round(ms)),
        userActed: true,
        selectedAtomId: opts?.atomId ?? s.selectedAtomId,
        selectedEvId: opts?.evId ?? s.selectedEvId,
      }));
    },
    selectEvidence: (atomId, evId) =>
      set({ selectedAtomId: atomId, selectedEvId: evId, userActed: true }),
    setFollow: (v) => set({ follow: v }),
    toggleMod: (m) =>
      set((s) => {
        const next = { ...s.modFilter, [m]: !s.modFilter[m] };
        // refuse to filter everything out — an empty stream hides too much
        if (!next.ASR && !next.OCR && !next.VIS) return s;
        return { modFilter: next };
      }),
  }));
}

export type WorkbenchStore = UseBoundStore<StoreApi<WorkbenchState>>;

const StoreCtx = createContext<WorkbenchStore | null>(null);

export function WorkbenchStoreProvider({
  store,
  children,
}: {
  store: WorkbenchStore;
  children: React.ReactNode;
}) {
  return <StoreCtx.Provider value={store}>{children}</StoreCtx.Provider>;
}

export function useWorkbench(): WorkbenchStore {
  const s = useContext(StoreCtx);
  if (!s) throw new Error("useWorkbench must be used inside WorkbenchStoreProvider");
  return s;
}
