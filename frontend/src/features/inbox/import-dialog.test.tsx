import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ImportDialog } from "./import-dialog";
import { renderWithApp } from "@/test/utils";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { syncKeys } from "@/lib/sync-api";

describe("ImportDialog batch parsing (frontend hint only; backend is authority)", () => {
  it("本地排队反馈不声称已导入或等待家庭节点", async () => {
    server.use(http.post("/api/import", () => HttpResponse.json({ status: "queued", task_id: 12, node_role: "all" }, { status: 202 })));
    const user = userEvent.setup();
    renderWithApp(<ImportDialog open onOpenChange={() => {}} onImported={() => {}} />);
    await user.type(screen.getByLabelText("视频链接"), "https://video.test/a");
    await user.click(screen.getByRole("button", { name: "导入并处理" }));
    expect(await screen.findByText("本地导入任务已排队，进度可在采集助手查看")).toBeInTheDocument();
    expect(screen.queryByText(/尚未确认家庭节点接单/)).not.toBeInTheDocument();
  });

  it("响应不确定时重提同一选择保原UUID，改选择生成新UUID", async () => {
    const received: { url: string; request_id?: string }[] = [];
    server.use(http.post("/api/import", async ({ request }) => {
      received.push(await request.json() as { url: string; request_id?: string });
      return received.length === 1 ? HttpResponse.json({ detail: "uncertain" }, { status: 500 })
        : HttpResponse.json({ status: "queued", task_id: 12, node_role: "all" }, { status: 202 });
    }));
    const user = userEvent.setup();
    renderWithApp(<ImportDialog open onOpenChange={() => {}} onImported={() => {}} />);
    await user.type(screen.getByLabelText("视频链接"), "https://video.test/a");
    await user.click(screen.getByRole("button", { name: "导入并处理" }));
    await screen.findByText(/导入失败.*uncertain/);
    await user.click(screen.getByRole("button", { name: "导入并处理" }));
    await waitFor(() => expect(screen.getByLabelText("视频链接")).toHaveValue(""));
    await user.type(screen.getByLabelText("视频链接"), "https://video.test/b");
    await user.click(screen.getByRole("button", { name: "导入并处理" }));
    await waitFor(() => expect(received).toHaveLength(3));
    expect(received[0].request_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(received[1].request_id).toBe(received[0].request_id);
    expect(received[2].request_id).not.toBe(received[0].request_id);
  });

  it("classifies valid / invalid / duplicate lines and disables submit when none valid", async () => {
    const user = userEvent.setup();
    const onImported = vi.fn();
    renderWithApp(
      <ImportDialog open onOpenChange={() => {}} onImported={onImported} />,
    );

    await user.click(screen.getByRole("tab", { name: /批量/ }));
    const textarea = screen.getByLabelText("批量链接");
    await user.type(
      textarea,
      "https://www.bilibili.com/video/BV111{enter}not a url{enter}https://www.bilibili.com/video/BV111?from=x{enter}https://v.douyin.com/abc/",
    );

    await waitFor(() => {
      // "无效" appears in the stat row AND the helper note — assert on the
      // stat itself (span whose text starts with 无效)
      const stat = screen
        .getAllByText(/无效/)
        .find((el) => el.tagName === "SPAN" && el.textContent.startsWith("无效"));
      expect(stat).toBeTruthy();
    });
    // Different queries remain distinct; only identical links/fragments dedupe.
    const dupeStat = screen
      .getAllByText(/重复行/)
      .find((el) => el.tagName === "SPAN" && el.textContent.startsWith("重复行"));
    expect(dupeStat?.textContent).toContain("0");
    const submit = screen.getByRole("button", { name: /提交 \d+ 条/ });
    expect(submit).toHaveTextContent("提交 3 条");
    expect(submit).toBeEnabled();

    await user.clear(textarea);
    await user.type(textarea, "still not a url");
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /提交 \d+ 条/ })).toBeDisabled();
    });
  });

  it("批量提交保留分P、完整query顺序和重复参数，去fragment后刷新同步状态", async () => {
    const received: string[][] = [];
    server.use(http.post("/api/import-batch", async ({ request }) => {
      received.push(((await request.json()) as { urls: string[] }).urls);
      return HttpResponse.json({ queued: 3 });
    }));
    const user = userEvent.setup();
    const { client } = renderWithApp(<ImportDialog open onOpenChange={() => {}} onImported={() => {}} />);
    client.setQueryDefaults(syncKeys.all, { gcTime: 60_000 });
    client.setQueryData(syncKeys.status(0, 10), { marker: true });
    await user.click(screen.getByRole("tab", { name: /批量/ }));
    fireEvent.change(screen.getByLabelText("批量链接"), { target: { value:
      "https://www.bilibili.com/video/BV111?p=1&x=1&x=2#top\nhttps://www.bilibili.com/video/BV111?p=1&x=1&x=2#again\nhttps://www.bilibili.com/video/BV111?p=2&x=1&x=2\nhttps://www.bilibili.com/video/BV111?p=2&x=2&x=1" } });
    await user.click(screen.getByRole("button", { name: "提交 3 条" }));
    await waitFor(() => expect(received).toHaveLength(1));
    expect(received[0]).toEqual(["https://www.bilibili.com/video/BV111?p=1&x=1&x=2",
      "https://www.bilibili.com/video/BV111?p=2&x=1&x=2", "https://www.bilibili.com/video/BV111?p=2&x=2&x=1"]);
    await waitFor(() => expect(client.getQueryState(syncKeys.status(0, 10))?.isInvalidated).toBe(true));
  });

  it("单条导入也刷新同步状态并保留query", async () => {
    const received: string[] = [];
    server.use(http.post("/api/import", async ({ request }) => {
      received.push(((await request.json()) as { url: string }).url);
      return HttpResponse.json({ status: "queued" });
    }));
    const user = userEvent.setup();
    const { client } = renderWithApp(<ImportDialog open onOpenChange={() => {}} onImported={() => {}} />);
    client.setQueryDefaults(syncKeys.all, { gcTime: 60_000 });
    client.setQueryData(syncKeys.status(0, 10), { marker: true });
    await user.type(screen.getByLabelText("视频链接"), "https://www.bilibili.com/video/BV111?p=2#top");
    await user.click(screen.getByRole("button", { name: "导入并处理" }));
    await waitFor(() => expect(received).toEqual(["https://www.bilibili.com/video/BV111?p=2"]));
    await waitFor(() => expect(client.getQueryState(syncKeys.status(0, 10))?.isInvalidated).toBe(true));
  });

  it("shows per-item failures from a partially-successful batch", async () => {
    const user = userEvent.setup();
    renderWithApp(
      <ImportDialog open onOpenChange={() => {}} onImported={() => {}} />,
    );
    await user.click(screen.getByRole("tab", { name: /批量/ }));
    await user.type(
      screen.getByLabelText("批量链接"),
      "https://www.bilibili.com/video/BV111",
    );
    await user.click(screen.getByRole("button", { name: /提交 \d+ 条/ }));
    // mock returns failed:1 + sample line
    await waitFor(() => {
      expect(screen.getByText(/1 条失败/)).toBeInTheDocument();
      expect(screen.getByText(/mock ingest failure/)).toBeInTheDocument();
    });
  });

  it("网页入口拒绝本地路径且不把路径发送到服务器", async () => {
    const path = "E:\\videos\\装修#1.mp4";
    const received: string[] = [];
    server.use(http.post("/api/import", async ({ request }) => {
      received.push(((await request.json()) as { url: string }).url);
      return HttpResponse.json({ status: "imported" });
    }));
    renderWithApp(<ImportDialog open onOpenChange={() => {}} onImported={() => {}} />);
    fireEvent.change(screen.getByLabelText("视频链接"), { target: { value: path } });
    expect(screen.getByRole("button", { name: "导入并处理" })).toBeDisabled();
    expect(received).toEqual([]);
  });
});
