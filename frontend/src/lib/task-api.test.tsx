import { act, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { http } from "msw";
import { server } from "@/test/server";
import { renderWithApp } from "@/test/utils";
import { makeAsk } from "@/mocks/fixtures";
import { getReadingState, resetReadingSessions, submitAsk, submitCompare } from "./reading-session";
import { taskKeys, useMaintenanceTasks, type MaintenanceTask } from "./task-api";

function Tasks() {
  const tasks = useMaintenanceTasks();
  return <p>{tasks.isSuccess ? "任务已读取" : "读取中"}</p>;
}

describe("judge completion cache boundaries", () => {
  beforeEach(() => {
    resetReadingSessions();
    server.use(http.get("/api/tasks", () => Response.json({ tasks: [] })));
  });

  it("invalidates ordinary read caches only after success and preserves saved answer/comparison snapshots", async () => {
    let modelRequests = 0;
    server.use(http.post("/api/ask", () => { modelRequests++; return Response.json(makeAsk("防水")); }));
    await submitAsk("防水");
    await submitCompare("瓷砖 地板");
    const saved = getReadingState();
    const { client } = renderWithApp(<Tasks />);
    await screen.findByText("任务已读取");
    client.setQueryDefaults([], { gcTime: Infinity });
    const keys = [["video", "v1", "events"], ["video", "v1", "meta"], ["search", "防水"], ["compare", "瓷砖,地板"]];
    keys.forEach((key) => client.setQueryData(key, { original: true }));
    client.setQueryData(["ask", "防水"], { original: true });
    const task: MaintenanceTask = { task_id: 1, job_id: 1, kind: "judge", video_id: null,
      status: "running", attempts: 1, request_id: "manual", payload: {}, error: null,
      created_at: "2026-10-01T04:00:00Z", updated_at: "2026-10-01T04:00:01Z", completed_at: null, can_retry: false };
    await act(async () => { client.setQueryData(taskKeys.tasks, { tasks: [task] }); });
    expect(keys.every((key) => client.getQueryState(key)?.isInvalidated === false)).toBe(true);
    await act(async () => { client.setQueryData(taskKeys.tasks, { tasks: [{ ...task, status: "done", updated_at: "2026-10-01T04:00:02Z" }] }); });
    await waitFor(() => expect(keys.every((key) => client.getQueryState(key)?.isInvalidated)).toBe(true));
    expect(client.getQueryState(["ask", "防水"])?.isInvalidated).toBe(false);
    expect(getReadingState()).toBe(saved);
    expect(modelRequests).toBe(1);
  });
});
