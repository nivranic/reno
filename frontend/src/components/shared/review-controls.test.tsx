import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { renderWithApp } from "@/test/utils";
import { AtomReviewControls } from "./review-controls";

describe("content-bound knowledge review", () => {
  it("preserves the note and event id when explicitly retrying an uncertain response", async () => {
    const user = userEvent.setup();
    const bodies: Record<string, unknown>[] = [];
    server.use(http.post("/api/decision", async ({ request }) => {
      bodies.push(await request.json() as Record<string, unknown>);
      return bodies.length === 1 ? HttpResponse.error() : HttpResponse.json({ ok: true });
    }));
    renderWithApp(<AtomReviewControls atomId="atom-a" fingerprint="v1:content" />);
    await user.click(screen.getByRole("button", { name: "人工复核" }));
    await user.type(screen.getByLabelText("复核备注"), "先核对当前材料条件");
    await user.click(screen.getByRole("button", { name: "确认这条知识" }));
    await screen.findByRole("alert");
    expect(screen.getByLabelText("复核备注")).toHaveValue("先核对当前材料条件");
    await user.click(screen.getByRole("button", { name: "确认这条知识" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("复核已保存"));
    expect(bodies).toHaveLength(2);
    expect(bodies[0]).toEqual(bodies[1]);
    expect(bodies[0].target_fingerprint).toBe("v1:content");
  });

  it("does not offer a new decision without a content identity", async () => {
    const user = userEvent.setup();
    renderWithApp(<AtomReviewControls atomId="old-atom" />);
    await user.click(screen.getByRole("button", { name: "人工复核" }));
    expect(screen.getByRole("button", { name: "确认这条知识" })).toBeDisabled();
  });
});
