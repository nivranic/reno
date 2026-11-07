import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { renderWithApp } from "@/test/utils";
import { server } from "@/test/server";
import { taskPollInterval, type MaintenanceTask } from "@/lib/task-api";
import { TaskStatusPanel } from "./task-status-panel";

const task = (changes: Partial<MaintenanceTask> = {}): MaintenanceTask => ({ task_id: 7, job_id: 7, kind: "process",
  video_id: null, status: "pending", attempts: 1, request_id: "original", payload: {}, error: null,
  created_at: "2026-10-02T00:00:00Z", updated_at: "2026-10-02T00:00:00Z", completed_at: null, can_retry: false,
  not_before: null, blocked_reason: "indeterminate", wait_mode: "blocked", manual_paused: false,
  budget_operation_id: "original", can_pause: true, can_resume: true, ...changes });
const capabilities = (enabled = true) => server.use(http.get("/api/capabilities", () => HttpResponse.json({ tasks: enabled, node_role: enabled ? "all" : "cloud" })));

describe("普通处理任务等待控制", () => {
  it("shows unknown result and no invented retry time, without a generic retry button", async () => {
    capabilities();
    server.use(http.get("/api/tasks", () => HttpResponse.json({ tasks: [task()] })));
    renderWithApp(<TaskStatusPanel />);
    expect(await screen.findByText("等待条件允许")).toBeInTheDocument();
    expect(screen.getByText(/结果未知，原请求不会自动重发/)).toBeInTheDocument();
    expect(screen.getByText(/尚无计划继续时间/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "请求继续任务" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试任务" })).not.toBeInTheDocument();
  });

  it("pause uses the existing original job API with an empty closed body", async () => {
    capabilities();
    let current = task();
    const bodies: unknown[] = [];
    server.use(http.get("/api/tasks", () => HttpResponse.json({ tasks: [current] })),
      http.post("/api/jobs/7/pause", async ({ request }) => {
        bodies.push(await request.json());
        current = { ...current, manual_paused: true, can_pause: false };
        return HttpResponse.json(current, { status: 202 });
      }));
    renderWithApp(<TaskStatusPanel />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "暂停任务" }));
    await waitFor(() => expect(bodies).toEqual([{}]));
    expect(await screen.findByText("已手动暂停")).toBeInTheDocument();
  });

  it("capabilities disable controls on cloud even if an old task row has flags", async () => {
    capabilities(false);
    server.use(http.get("/api/tasks", () => HttpResponse.json({ tasks: [task()] })));
    renderWithApp(<TaskStatusPanel />);
    await screen.findByText("等待条件允许");
    expect(screen.queryByRole("button", { name: "暂停任务" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "请求继续任务" })).not.toBeInTheDocument();
  });

  it("indefinite waits use slow polling and in-flight work can still settle while paused", () => {
    expect(taskPollInterval([task()])).toBe(15_000);
    expect(taskPollInterval([task({ status: "running", manual_paused: true })])).toBe(2000);
    expect(taskPollInterval([task({ status: "done" })])).toBe(false);
  });
});
