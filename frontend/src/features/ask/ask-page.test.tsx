import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AskPage from "./ask-page";
import { renderWithApp } from "@/test/utils";
import { http } from "msw";
import { server } from "@/test/server";
import { makeAsk } from "@/mocks/fixtures";
import { resetReadingSessions } from "@/lib/reading-session";

beforeEach(() => resetReadingSessions());

describe("AskPage", () => {
  it("sends a question and renders the cited answer with evidence links", async () => {
    const user = userEvent.setup();
    const bodies: unknown[] = [];
    server.use(
      http.post("/api/ask", async ({ request }) => {
        bodies.push(await request.json());
        return Response.json(makeAsk("卫生间淋浴区防水要做多高？"));
      }),
    );
    renderWithApp(<AskPage />);

    const box = screen.getByLabelText("问题输入框");
    await user.type(box, "卫生间淋浴区防水要做多高？");
    await user.click(screen.getByRole("button", { name: "发送问题" }));

    await waitFor(() => {
      expect(bodies).toEqual([
        { question: "卫生间淋浴区防水要做多高？", history: [] },
      ]);
    });
    await waitFor(() => {
      expect(screen.getByText(/主流做法如下/)).toBeInTheDocument();
    });
    // conflict banner for the disputed topic
    expect(screen.getByText(/有分歧的主题/)).toBeInTheDocument();
    // citation chip deep-links into the workbench at the evidence timestamp;
    // tooltip carries the full video title (what gets truncated) plus claim
    const chip = screen.getByTitle(
      "卫生间防水施工全流程(mock)：卫生间淋浴区墙面防水应涂刷约1.8米高",
    );
    expect(chip.closest("a")).toHaveAttribute("href", "/videos/BVmock000?t=210000");
    // input cleared after send
    expect(box).toHaveValue("");
  });

  it("passes recent turns as history on a follow-up", async () => {
    const user = userEvent.setup();
    const bodies: unknown[] = [];
    server.use(
      http.post("/api/ask", async ({ request }) => {
        bodies.push(await request.json());
        return Response.json(makeAsk("追问"));
      }),
    );
    renderWithApp(<AskPage />);
    const box = screen.getByLabelText("问题输入框");

    await user.type(box, "防水要做多高？");
    await user.click(screen.getByRole("button", { name: "发送问题" }));
    await waitFor(() => expect(screen.getByText(/主流做法如下/)).toBeInTheDocument());

    await user.type(box, "那干区呢？");
    await user.click(screen.getByRole("button", { name: "发送问题" }));
    await waitFor(() => expect(bodies.length).toBe(2));
    const second = bodies[1] as { question: string; history: unknown[] };
    expect(second.question).toBe("那干区呢？");
    expect(second.history).toEqual([
      { role: "user", content: "防水要做多高？" },
      { role: "assistant", content: expect.stringContaining("主流做法") },
    ]);
  });

  it("keeps the question visible and offers retry when the API fails", async () => {
    const user = userEvent.setup();
    server.use(http.post("/api/ask", () => new Response(null, { status: 500 })));
    renderWithApp(<AskPage />);

    const box = screen.getByLabelText("问题输入框");
    await user.type(box, "水电验收注意什么？");
    await user.click(screen.getByRole("button", { name: "发送问题" }));

    await waitFor(() => {
      expect(screen.getByText(/HTTP 500/)).toBeInTheDocument();
    });
    // user turn preserved in the thread
    expect(screen.getByText("水电验收注意什么？")).toBeInTheDocument();
  });

  it("keeps completed answers and an unsent draft when the route remounts", async () => {
    const user = userEvent.setup();
    let calls = 0;
    server.use(http.post("/api/ask", () => { calls++; return Response.json(makeAsk("防水")); }));
    const first = renderWithApp(<AskPage />);
    await user.type(screen.getByLabelText("问题输入框"), "防水{Enter}");
    await screen.findByText(/主流做法如下/);
    await user.type(screen.getByLabelText("问题输入框"), "尚未发送的补充条件");
    first.unmount();
    renderWithApp(<AskPage />);
    expect(screen.getByText(/主流做法如下/)).toBeInTheDocument();
    expect(screen.getByLabelText("问题输入框")).toHaveValue("尚未发送的补充条件");
    expect(calls).toBe(1);
  });

  it("clears a pending request and ignores its late response", async () => {
    const user = userEvent.setup();
    let resolve!: () => void;
    const pending = new Promise<void>((done) => { resolve = done; });
    server.use(http.post("/api/ask", async () => { await pending; return Response.json(makeAsk("防水")); }));
    renderWithApp(<AskPage />);
    await user.type(screen.getByLabelText("问题输入框"), "防水{Enter}");
    await screen.findByText(/正在检索知识库/);
    await user.click(screen.getByRole("button", { name: "清空会话" }));
    resolve();
    await waitFor(() => expect(screen.getByText("向装修知识库提问")).toBeInTheDocument());
    expect(screen.queryByText(/主流做法如下/)).not.toBeInTheDocument();
  });

  it("separates matching and old unbound relations while preserving saved answer text", async () => {
    let calls = 0;
    server.use(http.post("/api/ask", () => {
      calls++;
      const result = makeAsk("防水");
      result.conflicts.push({ cluster_id: "legacy", topic: "旧关联", conflict_id: null });
      return Response.json(result);
    }));
    const user = userEvent.setup();
    const first = renderWithApp(<AskPage />);
    await user.type(screen.getByLabelText("问题输入框"), "防水{Enter}");
    const matching = await screen.findByRole("region", { name: "依据匹配的分歧" });
    expect(matching).toHaveTextContent("回答生成时：1 个有分歧的主题");
    expect(matching).not.toHaveTextContent("旧关联");
    const history = screen.getByRole("region", { name: "历史关联待核对" });
    expect(within(history).getByText(/旧关联/)).toBeInTheDocument();
    expect(history).toHaveTextContent("判定依据未知");
    expect(screen.getByText(/主流做法如下/)).toBeInTheDocument();
    first.unmount();
    renderWithApp(<AskPage />);
    expect(screen.getByRole("region", { name: "历史关联待核对" })).toHaveTextContent("判定依据未知");
    expect(calls).toBe(1);
  });
});
