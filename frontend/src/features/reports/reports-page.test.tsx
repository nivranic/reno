import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { server } from "@/test/server";
import { renderWithApp } from "@/test/utils";
import ReportsPage from "./reports-page";

const meta = { report_type: "checklist", status: "stale", has_content: true, stale: true,
  generated_at: "2026-09-30T01:00:00Z", kb_revision: 1, policy_version: "reports-v2" };

describe("ReportsPage maintenance", () => {
  beforeEach(() => server.use(
    http.get("/api/tasks", () => Response.json({ tasks: [] })),
    http.get("/api/capabilities", () => Response.json({ tasks: true, node_role: "all" })),
    http.get("/api/reports", () => Response.json({ reports: { checklist: "# 上次的核对内容" } })),
    http.get("/api/reports/meta", () => Response.json({ reports: { checklist: meta } })),
  ));

  it("submits static and AI summary as separate explicit actions", async () => {
    const requests: Record<string, unknown>[] = [];
    server.use(http.post("/api/tasks", async ({ request }) => {
      const body = await request.json() as Record<string, unknown>;
      requests.push(body);
      return Response.json({ task_id: requests.length, job_id: requests.length, kind: "report",
        status: "done", payload: body.payload, updated_at: "now", can_retry: false }, { status: 202 });
    }));
    renderWithApp(<ReportsPage />);
    const user = userEvent.setup();
    const staticButton = await screen.findByRole("button", { name: "更新静态报告" });
    await waitFor(() => expect(staticButton).toBeEnabled());
    expect(requests).toHaveLength(0);
    await user.click(staticButton);
    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toMatchObject({ kind: "report", payload: { mode: "static" } });
    await user.click(screen.getByRole("button", { name: "生成 AI 总结" }));
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(requests[1]).toMatchObject({ kind: "report", payload: { mode: "summary" } });
    expect(screen.getByRole("heading", { name: "上次的核对内容" })).toBeInTheDocument();
  });

  it("keeps the same request id when explicitly retrying an unknown result", async () => {
    const ids: unknown[] = [];
    server.use(http.post("/api/tasks", async ({ request }) => {
      const body = await request.json() as Record<string, unknown>;
      ids.push(body.request_id);
      return Response.json({ detail: "暂时无法确认提交", code: "UNAVAILABLE" }, { status: 503 });
    }));
    renderWithApp(<ReportsPage />);
    const user = userEvent.setup();
    const button = screen.getByRole("button", { name: "更新静态报告" });
    await waitFor(() => expect(button).toBeEnabled());
    await user.click(button);
    await screen.findByRole("alert");
    await user.click(button);
    await waitFor(() => expect(ids).toHaveLength(2));
    expect(ids[0]).toEqual(ids[1]);
    expect(screen.getByRole("heading", { name: "上次的核对内容" })).toBeInTheDocument();
  });

  it("makes cloud generation unavailable and retains readable old reports", async () => {
    server.use(http.get("/api/capabilities", () => Response.json({ tasks: false, node_role: "cloud" })));
    renderWithApp(<ReportsPage />);
    expect(await screen.findByText(/本节点暂不能执行报告任务/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "更新静态报告" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "生成 AI 总结" })).toBeDisabled();
    expect(screen.getByRole("heading", { name: "上次的核对内容" })).toBeInTheDocument();
  });

  it("keeps latest report status independent from pending relation inputs", async () => {
    server.use(http.get("/api/reports/meta", () => Response.json({ reports: { checklist: { ...meta, status: "latest", stale: false,
      derivation_summary: { clusters: { active: 1, stale: 2, missing: 0, unknown: 0 }, conflicts: { active: 0, stale: 0, missing: 1, unknown: 1 } },
    } } })));
    renderWithApp(<ReportsPage />);
    const notice = await screen.findByRole("region", { name: "报告关联状态" });
    await waitFor(() => expect(notice).toHaveTextContent("关联待更新：4 项"));
    expect(screen.getAllByText("最新").length).toBeGreaterThan(0);
    expect(notice).toHaveTextContent("重新生成报告不会更新争议的判定依据");
    expect(screen.getByRole("link", { name: "查看关联并更新争议" })).toHaveAttribute("href", "/conflicts");
  });

  it("does not infer matching relation inputs from old report metadata", async () => {
    renderWithApp(<ReportsPage />);
    expect(await screen.findByText("关联判定依据尚未确认")).toBeInTheDocument();
    expect(screen.queryByText(/当前可见关联无待更新项/)).not.toBeInTheDocument();
  });

  it("does not present a cached matching summary as confirmed after metadata refresh fails", async () => {
    server.use(http.get("/api/reports/meta", () => Response.json({ reports: { checklist: { ...meta,
      derivation_summary: { clusters: { active: 1, stale: 0, missing: 0, unknown: 0 }, conflicts: { active: 1, stale: 0, missing: 0, unknown: 0 } },
    } } })));
    const { client } = renderWithApp(<ReportsPage />);
    await screen.findByText(/当前可见关联无待更新项/);
    server.use(http.get("/api/reports/meta", () => new Response(null, { status: 503 })));
    await client.invalidateQueries({ queryKey: ["report-metadata"] });
    expect(await screen.findByText("关联判定依据尚未确认")).toBeInTheDocument();
    expect(screen.queryByText(/当前可见关联无待更新项/)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "上次的核对内容" })).toBeInTheDocument();
  });
});
