import { expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { apiText, ApiError } from "@/lib/api";
import { exportReconciliation } from "@/lib/reconciliation-api";
import { server } from "@/test/server";

it("文本导出沿用401令牌门，不把错误JSON当Markdown下载", async () => {
  const unauthorized = vi.fn();
  window.addEventListener("reno-unauthorized", unauthorized);
  server.use(http.get("/api/reconciliation/export", () => HttpResponse.json({ detail: "需要访问令牌", code: "UNAUTHORIZED" }, { status: 401 })));
  try {
    await expect(exportReconciliation({ reason: "all", scope: "attention" })).rejects.toMatchObject({ status: 401, code: "UNAUTHORIZED" });
    expect(unauthorized).toHaveBeenCalledOnce();
  } finally { window.removeEventListener("reno-unauthorized", unauthorized); }
});

it("文本导出网络故障转为既有ApiError", async () => {
  server.use(http.get("/api/reconciliation/export", () => HttpResponse.error()));
  await expect(exportReconciliation({ reason: "missing", scope: "history" })).rejects.toBeInstanceOf(ApiError);
});

it("传递AbortSignal并原样抛出请求取消，不触发令牌门", async () => {
  const aborted = new DOMException("Synthetic cancellation", "AbortError");
  const controller = new AbortController();
  controller.abort();
  const fetch = vi.spyOn(globalThis, "fetch").mockRejectedValue(aborted);
  const unauthorized = vi.fn();
  window.addEventListener("reno-unauthorized", unauthorized);
  try {
    await expect(apiText("/api/reconciliation/export", controller.signal)).rejects.toBe(aborted);
    expect(fetch).toHaveBeenCalledWith("/api/reconciliation/export", expect.objectContaining({ signal: controller.signal }));
    expect(unauthorized).not.toHaveBeenCalled();
  } finally { fetch.mockRestore(); window.removeEventListener("reno-unauthorized", unauthorized); }
});

it("读取导出正文时的取消也保持AbortError", async () => {
  const aborted = new DOMException("Synthetic body cancellation", "AbortError");
  const response = new Response("synthetic");
  const text = vi.spyOn(response, "text").mockRejectedValue(aborted);
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(response);
  try { await expect(apiText("/api/reconciliation/export")).rejects.toBe(aborted); }
  finally { text.mockRestore(); fetch.mockRestore(); }
});
