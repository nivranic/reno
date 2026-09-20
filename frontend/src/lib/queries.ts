/** TanStack Query definitions — the single cache for server state.
 * Pages consume these; nothing duplicates server data into other stores. */
import { queryOptions } from "@tanstack/react-query";
import {
  getConflicts,
  getEvents,
  getFacets,
  getHealth,
  getReports,
  getVideoMeta,
  getVideos,
  searchAtoms,
} from "./api";

export const qk = {
  videos: ["videos"] as const,
  videoMeta: (id: string) => ["video", id, "meta"] as const,
  events: (id: string) => ["video", id, "events"] as const,
  conflicts: ["conflicts"] as const,
  facets: ["facets"] as const,
  reports: ["reports"] as const,
  health: ["health"] as const,
  search: (q: string, category: string, space: string) =>
    ["search", q, category, space] as const,
};

/** Inbox: adaptive polling — 3s while jobs are active, off otherwise
 * (TanStack pauses interval polling in background tabs by default). */
export const videosQuery = queryOptions({
  queryKey: qk.videos,
  queryFn: ({ signal }) => getVideos(signal),
  refetchInterval: (query) => {
    const data = query.state.data;
    if (!data) return false;
    const activeJobs =
      (data.stats.jobs["pending"] ?? 0) + (data.stats.jobs["running"] ?? 0);
    const working = data.videos.some(
      (v) => v.status !== "processed" && v.status !== "error",
    );
    return activeJobs > 0 || working ? 3000 : false;
  },
});

export const videoMetaQuery = (videoId: string) =>
  queryOptions({
    queryKey: qk.videoMeta(videoId),
    queryFn: ({ signal }) => getVideoMeta(videoId, signal),
    staleTime: 60_000,
  });

export const eventsQuery = (videoId: string) =>
  queryOptions({
    queryKey: qk.events(videoId),
    queryFn: ({ signal }) => getEvents(videoId, signal),
    staleTime: 5 * 60_000,
  });

export const conflictsQuery = queryOptions({
  queryKey: qk.conflicts,
  queryFn: ({ signal }) => getConflicts(signal),
});

export const facetsQuery = queryOptions({
  queryKey: qk.facets,
  queryFn: ({ signal }) => getFacets(signal),
  staleTime: 10 * 60_000,
});

export const reportsQuery = queryOptions({
  queryKey: qk.reports,
  queryFn: ({ signal }) => getReports(signal),
  staleTime: 5 * 60_000,
});

export const healthQuery = queryOptions({
  queryKey: qk.health,
  queryFn: ({ signal }) => getHealth(signal),
  refetchOnWindowFocus: true,
});

export const searchQuery = (q: string, category: string, space: string) =>
  queryOptions({
    queryKey: qk.search(q, category, space),
    queryFn: ({ signal }) => searchAtoms(q, category, space, signal),
    enabled: q.trim().length > 0,
    retry: 1,
  });
