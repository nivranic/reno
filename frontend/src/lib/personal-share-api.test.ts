import { afterEach, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { ApiError, setToken } from "./api";
import { previewDecisionShare, type PersonalRead } from "./personal-api";
import { cardFixture, projectFixture, decisionShareFixture } from "@/features/project/test-fixtures";

const path = "/api/projects/:id/decision-cards/share-preview";
const local: PersonalRead = { mode: "local", version: null, scope: "api-test-local" };
const version = "b".repeat(64);
const mirror: PersonalRead = { mode: "mirror", version, scope: "api-test-mirror" };
const selection = [{ card_id: cardFixture.card_id, revision: 3 }];
const summary = { ...cardFixture, revision: 3, decision_text: "服务端冻结结论" };
const serverRow = { ...summary, context_snapshot: { 私人资料: "excluded" } };
afterEach(() => { setToken(""); vi.restoreAllMocks(); });

it("sends only selected IDs/revisions with token, read version and caller signal and retains seven server values", async () => {
  setToken("synthetic-preview-token"); const controller = new AbortController();
  const fetchSpy = vi.spyOn(globalThis, "fetch"); let body: unknown;
  server.use(http.post(path, async ({ request }) => {
    body = await request.json();
    expect(request.headers.get("X-Reno-Token")).toBe("synthetic-preview-token");
    expect(request.headers.get("X-Reno-Personal-Read-Version")).toBe(version);
    expect(request.headers.get("Content-Type")).toBe("application/json");
    expect(request.url).not.toContain("token=");
    return HttpResponse.json(decisionShareFixture([serverRow], version),
      { headers: { "X-Reno-Personal-Read-Version": version } });
  }));
  const originalSelection = [{ ...selection[0], private_value: "must-not-be-sent" }];
  const result = await previewDecisionShare(projectFixture.project_id, originalSelection, mirror, controller.signal);
  expect(body).toEqual({ selection });
  expect(fetchSpy.mock.calls[0][1]?.signal).toBe(controller.signal);
  expect(result.snapshot.decisions[0]).toEqual({ title: summary.title, decision_text: summary.decision_text,
    decision_reason: summary.decision_reason, state: summary.state, revision: 3,
    updated_at: summary.updated_at, needs_recheck: summary.needs_recheck });
  expect(JSON.stringify(result)).not.toContain("excluded");
});

it.each(["header", "body"])("rejects a changed mirror %s version and emits the existing scope invalidation", async (which) => {
  const changed = vi.fn(); window.addEventListener("reno-personal-version-changed", changed);
  server.use(http.post(path, () => HttpResponse.json(decisionShareFixture([summary], which === "body" ? "c".repeat(64) : version),
    { headers: { "X-Reno-Personal-Read-Version": which === "header" ? "c".repeat(64) : version } })));
  try {
    await expect(previewDecisionShare(projectFixture.project_id, selection, mirror)).rejects.toMatchObject({
      status: 409, code: "PERSONAL_MIRROR_READ_VERSION_CHANGED",
    });
    expect(changed).toHaveBeenCalledOnce();
  } finally { window.removeEventListener("reno-personal-version-changed", changed); }
});

it("keeps the controlled 409 selection conflict and never changes the requested revision", async () => {
  server.use(http.post(path, async ({ request }) => {
    expect(await request.json()).toEqual({ selection });
    return HttpResponse.json({ detail: "所选决定已变化", code: "SHARE_SELECTION_CHANGED" }, { status: 409 });
  }));
  await expect(previewDecisionShare(projectFixture.project_id, selection, local)).rejects.toMatchObject({
    status: 409, code: "SHARE_SELECTION_CHANGED", message: "所选决定已变化",
  });
});

it("rejects a successful response that silently substitutes a different selected revision", async () => {
  server.use(http.post(path, () => HttpResponse.json(decisionShareFixture([{ ...summary, revision: 4 }]))));
  await expect(previewDecisionShare(projectFixture.project_id, selection, local)).rejects.toSatisfy(
    (error: unknown) => error instanceof ApiError && error.status === 0 && error.message === "分享摘要响应无效，请重试。",
  );
});

it.each(["network", "json"])("maps %s failure to a controlled read error without leaking the raw response", async (kind) => {
  server.use(http.post(path, () => kind === "network" ? HttpResponse.error()
    : new HttpResponse("private malformed response", { headers: { "Content-Type": "application/json" } })));
  await expect(previewDecisionShare(projectFixture.project_id, selection, local)).rejects.toSatisfy(
    (error: unknown) => error instanceof ApiError && error.status === 0 && !error.message.includes("private"),
  );
});

it("refuses an unconfirmed mirror before sending any request", async () => {
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  await expect(previewDecisionShare(projectFixture.project_id, selection, { ...mirror, version: null })).rejects.toMatchObject({ status: 403 });
  expect(fetchSpy).not.toHaveBeenCalled();
});

it("preserves cancellation before the request rather than turning it into a read error", async () => {
  const controller = new AbortController(); controller.abort(); const fetchSpy = vi.spyOn(globalThis, "fetch");
  await expect(previewDecisionShare(projectFixture.project_id, selection, local, controller.signal)).rejects.toMatchObject({ name: "AbortError" });
  expect(fetchSpy).not.toHaveBeenCalled();
});
