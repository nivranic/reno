import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import { useQuery } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { renderWithApp } from "@/test/utils";
import { decisionCardQuery, checklistQuery, downloadAttachment, downloadMarkdown, exportDecision, personalKeys, type Attachment, type CheckItem, type Checklist, type PersonalCapabilities } from "@/lib/personal-api";
import { apiGet, apiPatch } from "@/lib/api";
import { usePersonalProject } from "./use-personal-project";
import { isPersonalAbort, usePersonalTransfer } from "./use-personal-transfer";
import { cardFixture, installPersonalFixtures, projectFixture } from "./test-fixtures";
import ProjectPage from "./project-page";
import SourcesPage from "@/features/sources/sources-page";
import DecisionDetailPage from "@/features/decisions/decision-detail-page";
import CheckDetailPage from "@/features/checks/check-detail-page";

vi.mock("react-router", async (original) => ({ ...await original<typeof import("react-router")>(),
  useParams: () => ({ cardId: "22222222-2222-4222-8222-222222222222", instanceId: "mirror-list" }) }));

const version = "a".repeat(64);
const mirror: PersonalCapabilities = { library_id: "receiver", primary_epoch: 1, node_role: "cloud",
  personal_mode: "mirror", personal_read: true, personal_write: false, personal_upload: false,
  field_checks: true, field_checks_read: true, field_checks_write: false, personal_read_version: version,
  personal_mirror: { source_library_id: "source-library", source_node_id: "source-node",
    source_primary_id: "source-node", source_primary_epoch: 1, installed_revision: 3,
    snapshot_id: "snapshot", snapshot_sha256: "b".repeat(64), committed_at: "2026-10-04T01:00:00Z" } };
const response = (body: Parameters<typeof HttpResponse.json>[0], selected = version) => HttpResponse.json(body, { headers: { "X-Reno-Personal-Read-Version": selected } });

beforeEach(installPersonalFixtures);
let restoreStorageSpies = () => {};
afterEach(() => { restoreStorageSpies(); restoreStorageSpies = () => {}; vi.unstubAllGlobals(); });

function DetailHarness() {
  const access = usePersonalProject();
  const query = useQuery({ ...decisionCardQuery(cardFixture.card_id, access.read), enabled: access.readable });
  return <><output>{access.readable && query.data ? query.data.card.title : "权限尚未确认"}</output><output data-testid="scope">{access.scope}</output></>;
}

function FieldPermissionHarness() {
  const access = usePersonalProject();
  const card = useQuery({ ...decisionCardQuery(cardFixture.card_id, access.read), enabled: access.readable });
  useQuery({ ...checklistQuery("pending-field-list", access.read), enabled: access.fieldReadable });
  return <><output>{access.readable && card.data ? card.data.card.title : "权限尚未确认"}</output><output>{access.fieldReadable ? "现场已开放" : "现场未开放"}</output></>;
}

it("does not start a deep-link read before capabilities are confirmed or after permission refusal", async () => {
  let release!: (value: Response) => void; let calls = 0;
  server.use(http.get("/api/capabilities", () => new Promise<Response>((resolve) => { release = resolve; })),
    http.get("/api/decision-cards/:id", () => { calls++; return response({ card: cardFixture }); }));
  renderWithApp(<DetailHarness />);
  await waitFor(() => expect(release).toBeTypeOf("function"));
  expect(calls).toBe(0);
  await act(async () => release(HttpResponse.json({ ...mirror, personal_read: false })));
  await waitFor(() => expect(screen.getByText("权限尚未确认")).toBeVisible());
  expect(calls).toBe(0);
});

it("binds every mirror read to its version, clears old data and cancels the old scope on revocation", async () => {
  const headers: (string | null)[] = [];
  server.use(http.get("/api/capabilities", () => HttpResponse.json(mirror)),
    http.get("/api/projects/default", ({ request }) => { headers.push(request.headers.get("X-Reno-Personal-Read-Version")); return response({ enabled: true, project: projectFixture }); }),
    http.get("/api/decision-cards/:id", ({ request }) => { headers.push(request.headers.get("X-Reno-Personal-Read-Version")); return response({ card: cardFixture }); }));
  const view = renderWithApp(<DetailHarness />);
  await screen.findByText(cardFixture.title);
  expect(headers.length).toBeGreaterThan(0); expect(headers.every((value) => value === version)).toBe(true);
  await act(async () => { view.client.setQueryData(personalKeys.capabilities, { ...mirror, personal_read: false }); });
  await screen.findByText("权限尚未确认");
  await waitFor(() => expect(view.client.getQueryCache().findAll({ queryKey: personalKeys.card(cardFixture.card_id) }).every((query) => query.state.data === undefined)).toBe(true));
});

it("shows source and mirror time without mounting the project mutation form", async () => {
  server.use(http.get("/api/capabilities", () => HttpResponse.json(mirror)),
    http.get("/api/projects/default", () => response({ enabled: true, project: projectFixture })),
    http.get("/api/projects/:id/decision-cards", () => response({ cards: [], total: 0, offset: 0, limit: 30 })));
  renderWithApp(<ProjectPage />);
  expect(await screen.findByText(/云端只读镜像/)).toBeVisible();
  expect(await screen.findByText(/source-node/)).toBeVisible();
  expect(await screen.findByText(/镜像版本 3/)).toBeVisible();
  expect(screen.getByRole("heading", { name: projectFixture.name })).toBeVisible();
  expect(screen.queryByRole("button", { name: "保存项目资料" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("项目名称")).not.toBeInTheDocument();
});

it("retries the capability gate without manually starting the disabled project query", async () => {
  const user = userEvent.setup(); let denied = true; let capabilityReads = 0; let projectReads = 0;
  server.use(http.get("/api/capabilities", () => { capabilityReads++; return denied ? HttpResponse.json({ detail: "读取权限未确认" }, { status: 403 }) : HttpResponse.json(mirror); }),
    http.get("/api/projects/default", () => { projectReads++; return response({ enabled: true, project: projectFixture }); }),
    http.get("/api/projects/:id/decision-cards", () => response({ cards: [], total: 0, offset: 0, limit: 30 })));
  const view = renderWithApp(<ProjectPage />);
  await screen.findByRole("button", { name: "重试" }); expect(projectReads).toBe(0);
  await user.click(screen.getByRole("button", { name: "重试" }));
  await waitFor(() => expect(capabilityReads).toBe(2)); expect(projectReads).toBe(0);
  await waitFor(() => expect(view.client.getQueryState(personalKeys.capabilities)?.fetchStatus).toBe("idle"));
  denied = false;
  await user.click(screen.getByRole("button", { name: "重试" }));
  await screen.findByRole("heading", { name: projectFixture.name }); expect(projectReads).toBe(1);
});

it("never enables a mirror attachment from ready alone and does not mount upload or source editors", async () => {
  const upload = vi.fn();
  server.use(http.get("/api/capabilities", () => HttpResponse.json(mirror)),
    http.get("/api/projects/default", () => response({ enabled: true, project: projectFixture })),
    http.get("/api/projects/:id/sources", () => response({ sources: [{ source_id: "source", title: "镜像资料", source_type: "manual_note", revision: 1,
      verification_state: "unverified", validity: "unknown", attachments: [{ attachment_id: "file", original_name: "原PDF.pdf", state: "ready", readable: false, read_state: "missing" }] }], total: 1 })),
    http.post("/api/sources/:id/attachments", () => { upload(); return HttpResponse.json({}); }));
  renderWithApp(<SourcesPage />);
  await screen.findByRole("heading", { name: "镜像资料" });
  expect(screen.getByRole("button", { name: /原PDF.pdf/ })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "编辑记录" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/选择.*文件/)).not.toBeInTheDocument();
  expect(upload).not.toHaveBeenCalled();
});

it("does not treat an unversioned or disabled cloud capability as a local permission", async () => {
  let calls = 0;
  server.use(http.get("/api/capabilities", () => HttpResponse.json({ ...mirror, personal_read_version: null })),
    http.get("/api/decision-cards/:id", () => { calls++; return response({ card: cardFixture }); }));
  const view = renderWithApp(<DetailHarness />);
  await waitFor(() => expect(view.client.getQueryData(personalKeys.capabilities)).toBeDefined());
  expect(screen.getByText("权限尚未确认")).toBeVisible(); expect(calls).toBe(0);
  await act(async () => { view.client.setQueryData(personalKeys.capabilities, { ...mirror, personal_mode: "disabled" }); });
  expect(screen.getByText("权限尚未确认")).toBeVisible(); expect(calls).toBe(0);
});

it("withdraws a cached successful grant when its capabilities refetch fails", async () => {
  let denied = false;
  server.use(http.get("/api/capabilities", () => denied ? HttpResponse.json({ detail: "拒绝读取" }, { status: 403 }) : HttpResponse.json(mirror)),
    http.get("/api/projects/default", () => response({ enabled: true, project: projectFixture })),
    http.get("/api/decision-cards/:id", () => response({ card: cardFixture })));
  const view = renderWithApp(<DetailHarness />);
  await screen.findByText(cardFixture.title);
  denied = true;
  await act(async () => { await view.client.invalidateQueries({ queryKey: personalKeys.capabilities }); });
  await screen.findByText("权限尚未确认");
  expect(view.client.getQueryData(personalKeys.capabilities)).toMatchObject({ personal_read: true });
  await waitFor(() => expect(view.client.getQueryCache().findAll({ queryKey: personalKeys.card(cardFixture.card_id) }).every((query) => query.state.data === undefined)).toBe(true));
});

it.each([
  ["PERSONAL_MIRROR_READ_DISABLED", 403], ["PERSONAL_MIRROR_READ_NOT_INSTALLED", 403],
  ["PERSONAL_MIRROR_READ_PROOF_CHANGED", 409], ["PERSONAL_MIRROR_READ_UNAVAILABLE", 503],
  ["PERSONAL_MIRROR_READ_CONNECTION_CHANGED", 409], ["PERSONAL_MIRROR_READ_FACTORY_REQUIRED", 409],
  ["PERSONAL_MIRROR_READ_LIMIT", 507],
] as const)("withdraws all cached text immediately for an installed-mirror gate failure %s", async (code, status) => {
  let capabilityReads = 0; let release!: (value: Response) => void;
  server.use(http.get("/api/capabilities", () => {
    if (++capabilityReads === 1) return HttpResponse.json(mirror);
    return new Promise<Response>((resolve) => { release = resolve; });
  }), http.get("/api/projects/default", () => response({ enabled: true, project: projectFixture })),
    http.get("/api/decision-cards/:id", () => response({ card: cardFixture })),
    http.get("/api/sources/gate-check", () => HttpResponse.json({ detail: "镜像证明不可读取", code }, { status })));
  const view = renderWithApp(<DetailHarness />); await screen.findByText(cardFixture.title);
  await act(async () => { await expect(apiGet("/api/sources/gate-check")).rejects.toMatchObject({ code, status }); });
  await screen.findByText("权限尚未确认"); await waitFor(() => expect(release).toBeTypeOf("function"));
  expect(capabilityReads).toBe(2);
  await waitFor(() => expect(view.client.getQueryCache().findAll({ queryKey: personalKeys.card(cardFixture.card_id) }).every((query) => query.state.data === undefined)).toBe(true));
  await act(async () => { release(HttpResponse.json({ ...mirror, personal_read: false, personal_mode: "disabled" })); });
});

it.each([
  ["PERSONAL_MIRROR_READ_ATTACHMENT_CHANGED", 409], ["PERSONAL_MIRROR_READ_ATTACHMENT_UNAVAILABLE", 409],
  ["PERSONAL_MIRROR_READ_ATTACHMENT_NOT_PUBLISHED", 409], ["PERSONAL_ATTACHMENT_CHANGED", 409],
  ["PERSONAL_MIRROR_READ_NOT_FOUND", 404],
] as const)("keeps cached text and its capability for a single resource failure %s", async (code, status) => {
  let capabilityReads = 0;
  server.use(http.get("/api/capabilities", () => { capabilityReads++; return HttpResponse.json(mirror); }),
    http.get("/api/projects/default", () => response({ enabled: true, project: projectFixture })),
    http.get("/api/decision-cards/:id", () => response({ card: cardFixture })),
    http.get("/api/attachments/failed/file", () => HttpResponse.json({ detail: "此附件不可读取", code }, { status })));
  const view = renderWithApp(<DetailHarness />); await screen.findByText(cardFixture.title);
  await act(async () => { await expect(apiGet("/api/attachments/failed/file")).rejects.toMatchObject({ code, status }); });
  expect(screen.getByText(cardFixture.title)).toBeVisible(); expect(capabilityReads).toBe(1);
  expect(view.client.getQueryCache().findAll({ queryKey: personalKeys.card(cardFixture.card_id) }).some((query) => query.state.data !== undefined)).toBe(true);
});

it.each([
  ["CAPABILITY_DISABLED", 403], ["PRIMARY_CHANGED", 409],
  ["READ_ONLY_REPLICA", 403], ["PERSONAL_PRIMARY_UNAVAILABLE", 503],
] as const)("refreshes personal authority and cancels an active field read for %s", async (code, status) => {
  const initial: PersonalCapabilities = code === "CAPABILITY_DISABLED" ? mirror : { ...mirror, node_role: "home",
    personal_mode: "local", personal_read_version: null, personal_mirror: undefined, personal_write: true, personal_upload: true };
  let capabilityReads = 0; let fieldReads = 0; let fieldSignal: AbortSignal | undefined;
  let releaseCapabilities!: (value: Response) => void; let releaseField!: (value: Response) => void;
  server.use(http.get("/api/capabilities", () => {
    if (++capabilityReads === 1) return HttpResponse.json(initial);
    return new Promise<Response>((resolve) => { releaseCapabilities = resolve; });
  }), http.get("/api/projects/default", () => response({ enabled: true, project: projectFixture })),
    http.get("/api/decision-cards/:id", () => response({ card: cardFixture })),
    http.get("/api/checklists/pending-field-list", ({ request }) => {
      fieldReads++; fieldSignal = request.signal; return new Promise<Response>((resolve) => { releaseField = resolve; });
    }), http.get("/api/checklists/permission-check", () => HttpResponse.json({ detail: "现场功能已关闭", code }, { status })),
    http.patch("/api/projects/:id", () => HttpResponse.json({ detail: "个人主权限已变化", code }, { status })));
  const view = renderWithApp(<FieldPermissionHarness />);
  await screen.findByText(cardFixture.title); await waitFor(() => expect(releaseField).toBeTypeOf("function"));
  await act(async () => {
    const failure = code === "CAPABILITY_DISABLED" ? apiGet("/api/checklists/permission-check")
      : apiPatch(`/api/projects/${projectFixture.project_id}`, { operation_id: "original-operation", primary_epoch: 1, base_revision: 1, changes: { name: "原主实例提交" } });
    await expect(failure).rejects.toMatchObject({ code, status });
  });
  await screen.findByText("权限尚未确认"); await waitFor(() => expect(releaseCapabilities).toBeTypeOf("function"));
  expect(capabilityReads).toBe(2); expect(fieldSignal?.aborted).toBe(true);
  await waitFor(() => expect(view.client.getQueryCache().findAll({ queryKey: personalKeys.card(cardFixture.card_id) }).every((query) => query.state.data === undefined)).toBe(true));
  await act(async () => { releaseField(response({ checklist: mirrorChecklist })); });
  const next: PersonalCapabilities = code === "CAPABILITY_DISABLED" ? { ...initial, field_checks: false, field_checks_read: false, field_checks_write: false }
    : { ...initial, personal_read: false, personal_write: false, personal_upload: false, personal_mode: "disabled" };
  await act(async () => { releaseCapabilities(HttpResponse.json(next)); });
  if (code === "CAPABILITY_DISABLED") {
    await screen.findByText(cardFixture.title); expect(screen.getByText("现场未开放")).toBeVisible(); expect(fieldReads).toBe(1);
  } else expect(screen.getByText("权限尚未确认")).toBeVisible();
});

it.each([
  ["CAPABILITY_DISABLED", "/api/search", 403], ["PRIMARY_CHANGED", "/api/ask", 409],
  ["READ_ONLY_REPLICA", "/api/conflicts", 403], ["PERSONAL_PRIMARY_UNAVAILABLE", "/api/reports", 503],
  ["CAPABILITY_DISABLED", "/api/projects-info", 403],
  ["REVISION_CONFLICT", "/api/projects/current", 409], ["EVIDENCE_CHANGED", "/api/decision-cards/current", 409],
  ["INVALID_REQUEST", "/api/checklist-items/current", 400],
] as const)("does not withdraw personal text for %s from %s", async (code, path, status) => {
  let capabilityReads = 0;
  server.use(http.get(path, () => HttpResponse.json({ detail: "仅本次操作失败", code }, { status })),
    http.get("/api/capabilities", () => { capabilityReads++; return HttpResponse.json(mirror); }),
    http.get("/api/projects/default", () => response({ enabled: true, project: projectFixture })),
    http.get("/api/decision-cards/:id", () => response({ card: cardFixture })));
  const view = renderWithApp(<DetailHarness />); await screen.findByText(cardFixture.title);
  const originalScope = screen.getByTestId("scope").textContent;
  await act(async () => { await expect(apiGet(path)).rejects.toMatchObject({ code, status }); });
  expect(capabilityReads).toBe(1); expect(screen.getByText(cardFixture.title)).toBeVisible();
  expect(screen.getByTestId("scope").textContent).toBe(originalScope);
  expect(view.client.getQueryCache().findAll({ queryKey: personalKeys.card(cardFixture.card_id) }).some((query) => query.state.data !== undefined)).toBe(true);
});

it("cancels an old mirror request and prevents its late body from entering the next version", async () => {
  const nextVersion = "c".repeat(64); let oldSignal: AbortSignal | undefined; let release!: (value: Response) => void;
  server.use(http.get("/api/capabilities", () => HttpResponse.json(mirror)),
    http.get("/api/projects/default", ({ request }) => response({ enabled: true, project: projectFixture }, request.headers.get("X-Reno-Personal-Read-Version") ?? version)),
    http.get("/api/decision-cards/:id", ({ request }) => {
      if (request.headers.get("X-Reno-Personal-Read-Version") === version) {
        oldSignal = request.signal; return new Promise<Response>((resolve) => { release = resolve; });
      }
      return response({ card: { ...cardFixture, title: "新镜像中的决定" } }, nextVersion);
    }));
  const view = renderWithApp(<DetailHarness />);
  await waitFor(() => expect(release).toBeTypeOf("function"));
  await act(async () => { view.client.setQueryData(personalKeys.capabilities, { ...mirror, personal_read_version: nextVersion }); });
  await screen.findByText("新镜像中的决定");
  expect(oldSignal?.aborted).toBe(true);
  await act(async () => { release(response({ card: { ...cardFixture, title: "旧镜像晚返回" } })); });
  expect(screen.queryByText("旧镜像晚返回")).not.toBeInTheDocument();
  expect(view.client.getQueryCache().findAll({ queryKey: personalKeys.card(cardFixture.card_id) }).filter((query) => query.state.data !== undefined)).toHaveLength(1);
});

it("refreshes capabilities after a version conflict instead of keeping the stale grant", async () => {
  const nextVersion = "d".repeat(64); let capabilitiesReads = 0;
  server.use(http.get("/api/capabilities", () => HttpResponse.json(++capabilitiesReads === 1 ? mirror : { ...mirror, personal_read_version: nextVersion })),
    http.get("/api/projects/default", ({ request }) => response({ enabled: true, project: projectFixture }, request.headers.get("X-Reno-Personal-Read-Version") ?? version)),
    http.get("/api/decision-cards/:id", ({ request }) => request.headers.get("X-Reno-Personal-Read-Version") === version
      ? HttpResponse.json({ detail: "镜像已变化", code: "PERSONAL_MIRROR_READ_VERSION_CHANGED" }, { status: 409 })
      : response({ card: { ...cardFixture, title: "刷新后的决定" } }, nextVersion)));
  const view = renderWithApp(<DetailHarness />);
  await screen.findByText("刷新后的决定"); expect(capabilitiesReads).toBe(2);
  expect(view.client.getQueryCache().findAll({ queryKey: personalKeys.card(cardFixture.card_id) }).filter((query) => query.state.data !== undefined)).toHaveLength(1);
});

it("rejects a successful response with the wrong mirror version before caching its body", async () => {
  const nextVersion = "f".repeat(64); let capabilitiesReads = 0;
  server.use(http.get("/api/capabilities", () => HttpResponse.json(++capabilitiesReads === 1 ? mirror : { ...mirror, personal_read_version: nextVersion })),
    http.get("/api/projects/default", ({ request }) => response({ enabled: true, project: projectFixture }, request.headers.get("X-Reno-Personal-Read-Version") ?? version)),
    http.get("/api/decision-cards/:id", ({ request }) => request.headers.get("X-Reno-Personal-Read-Version") === version
      ? response({ card: { ...cardFixture, title: "错误版本的内容" } }, nextVersion)
      : response({ card: { ...cardFixture, title: "版本匹配的内容" } }, nextVersion)));
  const view = renderWithApp(<DetailHarness />); await screen.findByText("版本匹配的内容");
  expect(capabilitiesReads).toBe(2); expect(screen.queryByText("错误版本的内容")).not.toBeInTheDocument();
  const cached = view.client.getQueryCache().findAll({ queryKey: personalKeys.card(cardFixture.card_id) }).filter((query) => query.state.data !== undefined);
  expect(cached).toHaveLength(1); expect(cached[0].state.data).toMatchObject({ card: { title: "版本匹配的内容" } });
});

it("stops personal reads on an expired token without a capabilities retry loop", async () => {
  let expired = false; let deniedReads = 0;
  server.use(http.get("/api/capabilities", () => {
    if (expired) { deniedReads++; return HttpResponse.json({ detail: "需要令牌" }, { status: 401 }); }
    return HttpResponse.json(mirror);
  }), http.get("/api/projects/default", () => response({ enabled: true, project: projectFixture })),
    http.get("/api/decision-cards/:id", () => response({ card: cardFixture })));
  const view = renderWithApp(<DetailHarness />); await screen.findByText(cardFixture.title); expired = true;
  await act(async () => { await view.client.invalidateQueries({ queryKey: personalKeys.capabilities }); });
  await screen.findByText("权限尚未确认");
  await waitFor(() => expect(view.client.getQueryState(personalKeys.capabilities)?.fetchStatus).toBe("idle"));
  expect(deniedReads).toBeLessThanOrEqual(2);
});

it("separates source identities even if a cached capability supplies the same read version", async () => {
  let title = "来源甲的决定";
  server.use(http.get("/api/capabilities", () => HttpResponse.json(mirror)),
    http.get("/api/projects/default", () => response({ enabled: true, project: projectFixture })),
    http.get("/api/decision-cards/:id", () => response({ card: { ...cardFixture, title } })));
  const view = renderWithApp(<DetailHarness />); await screen.findByText(title);
  const oldScope = screen.getByTestId("scope").textContent;
  title = "来源乙的决定";
  await act(async () => { view.client.setQueryData(personalKeys.capabilities, { ...mirror,
    personal_mirror: { ...mirror.personal_mirror!, source_node_id: "source-B", source_primary_id: "source-B" } }); });
  await screen.findByText(title);
  expect(screen.getByTestId("scope").textContent).not.toBe(oldScope);
  expect(screen.queryByText("来源甲的决定")).not.toBeInTheDocument();
});

it("reads decision history in a mirror without mounting mutation or evidence editors", async () => {
  const user = userEvent.setup(); const historyHeaders: (string | null)[] = [];
  server.use(http.get("/api/capabilities", () => HttpResponse.json(mirror)),
    http.get("/api/projects/default", () => response({ enabled: true, project: projectFixture })),
    http.get("/api/decision-cards/:id", () => response({ card: cardFixture })),
    http.get("/api/decision-cards/:id/revisions", ({ request }) => { historyHeaders.push(request.headers.get("X-Reno-Personal-Read-Version")); return response({ revisions: [{ revision: 1, created_at: "昨天", snapshot: { ...cardFixture, decision_reason: "保存时的理由" } }] }); }));
  renderWithApp(<DecisionDetailPage />);
  await screen.findByRole("heading", { name: cardFixture.title });
  expect(historyHeaders).toHaveLength(0);
  expect(screen.queryByLabelText("决定理由")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "检查并更新依据" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "查看历史版本" }));
  await screen.findByText("版本 1 · 昨天"); expect(historyHeaders).toEqual([version]);
  expect(screen.getByText(/保存时的理由/)).toBeInTheDocument();
});

const mirrorItem: CheckItem = { item_id: "mirror-item", instance_id: "mirror-list", title: "卫生间观察", revision: 1,
  conditions: {}, method_text: "观察表面", basis_kind: "observation_only", criteria_state: "needs_verification",
  criteria: { text: "", conditions_confirmed: false, requirement_confirmed: false, required_observations: [] },
  refs: [], current_result: "unchecked", needs_recheck: false, criteria_snapshot_hash: "criteria", pass_eligibility: { allowed: false, reasons: ["尚无明确判据"] },
  checks: [{ check_id: "old-check", record_kind: "inspection", item_revision: 1, commit_seq: 1, result: "unable_to_verify", measurements: [], note: "过去的现场观察", attachment_refs: [], checked_at: "昨天", created_at: "昨天", criteria_snapshot_hash: "criteria" }] };
const mirrorChecklist: Checklist = { instance_id: "mirror-list", project_id: projectFixture.project_id, title: "只读现场清单", space: "卫生间", stage: "", revision: 1,
  state: "active", lifecycle_state: "active", items: [mirrorItem], progress: { total: 1, complete: 0 }, created_at: "昨天", updated_at: "昨天" };

it("shows field history without reading or writing session drafts or recovering an old receipt", async () => {
  let receiptReads = 0;
  const oldKey = `reno-reading:v1:${encodeURIComponent(window.location.origin)}:receiver:field:mirror-item`;
  sessionStorage.setItem(oldKey, JSON.stringify({ updated_at: Date.now(), draft: { note: "旧主实例草稿", pendingRequest: { operation_id: "old-operation" } } }));
  const readStorage = vi.spyOn(Storage.prototype, "getItem"); const writeStorage = vi.spyOn(Storage.prototype, "setItem");
  restoreStorageSpies = () => { readStorage.mockRestore(); writeStorage.mockRestore(); };
  server.use(http.get("/api/capabilities", () => HttpResponse.json(mirror)),
    http.get("/api/projects/default", () => response({ enabled: true, project: projectFixture })),
    http.get("/api/checklists/:id", () => response({ checklist: mirrorChecklist })),
    http.get("/api/operations/:id", () => { receiptReads++; return HttpResponse.json({}); }));
  renderWithApp(<CheckDetailPage />);
  await screen.findByRole("heading", { name: mirrorChecklist.title });
  expect(screen.getByText("过去的现场观察")).toBeInTheDocument();
  expect(screen.queryByText("填写核对、整改或复检记录")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "查询提交结果" })).not.toBeInTheDocument();
  expect(readStorage.mock.calls.some(([key]) => key.startsWith("reno-reading:"))).toBe(false);
  expect(writeStorage.mock.calls.some(([key]) => key.startsWith("reno-reading:"))).toBe(false);
  expect(receiptReads).toBe(0);
});

const readableAttachment: Attachment = { attachment_id: "mirror-file", source_id: "source", project_id: projectFixture.project_id,
  sha256: "e".repeat(64), byte_size: 4, media_type: "application/pdf", original_name: "说明.pdf", state: "ready", created_at: "昨天", target_fingerprint: "attachment", readable: true, read_state: "ready" };
function TransferHarness({ attachment = false }: { attachment?: boolean }) {
  const access = usePersonalProject(); const transfer = usePersonalTransfer(access.read);
  return <button disabled={!access.readable} onClick={() => { void transfer(async (signal) => {
    if (attachment) await downloadAttachment(readableAttachment, access.read, signal);
    else { const text = await exportDecision(cardFixture.card_id, access.read, signal); signal.throwIfAborted(); downloadMarkdown(text, "决定.md"); }
  }).catch((error: unknown) => { if (!isPersonalAbort(error)) throw error; }); }}>读取文件</button>;
}

it.each([false, true])("binds export/download to the current mirror and prevents a revoked late artifact (attachment=%s)", async (attachment) => {
  const user = userEvent.setup(); let release!: (value: Response) => void; let transferSignal: AbortSignal | undefined; let header: string | null = null;
  const createObjectURL = vi.fn(() => "blob:personal-test");
  vi.stubGlobal("URL", class extends URL { static createObjectURL = createObjectURL; static revokeObjectURL = vi.fn(); });
  server.use(http.get("/api/capabilities", () => HttpResponse.json(mirror)),
    http.get("/api/projects/default", () => response({ enabled: true, project: projectFixture })),
    http.get(attachment ? "/api/attachments/:id/file" : "/api/decision-cards/:id/export", ({ request }) => {
      header = request.headers.get("X-Reno-Personal-Read-Version"); transferSignal = request.signal;
      return new Promise<Response>((resolve) => { release = resolve; });
    }));
  const view = renderWithApp(<TransferHarness attachment={attachment} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "读取文件" })).toBeEnabled());
  await user.click(screen.getByRole("button", { name: "读取文件" }));
  await waitFor(() => expect(release).toBeTypeOf("function")); expect(header).toBe(version);
  await act(async () => { view.client.setQueryData(personalKeys.capabilities, { ...mirror, personal_read: false }); });
  await waitFor(() => expect(transferSignal?.aborted).toBe(true));
  await act(async () => { release(new HttpResponse("content", { headers: { "X-Reno-Personal-Read-Version": version } })); });
  expect(createObjectURL).not.toHaveBeenCalled();
});
