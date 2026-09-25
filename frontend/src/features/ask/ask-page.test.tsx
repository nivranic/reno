import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AskPage from "./ask-page";
import { renderWithApp } from "@/test/utils";
import { http } from "msw";
import { server } from "@/test/server";
import { makeAsk } from "@/mocks/fixtures";

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
    // citation chip deep-links into the workbench at the evidence timestamp
    const chip = screen.getByTitle("卫生间淋浴区墙面防水应涂刷约1.8米高");
    expect(chip).toHaveAttribute("href", "/videos/BVmock000?t=210000");
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
});
