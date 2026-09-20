import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  AtomStatusBadge,
  ConflictStatusBadge,
  PolarityBadge,
  VideoStatusBadge,
} from "./status-badge";

describe("VideoStatusBadge", () => {
  it("maps known statuses to labeled badges", () => {
    render(<VideoStatusBadge status="processed" />);
    expect(screen.getByText("已完成")).toBeInTheDocument();
  });
  it("keeps UNKNOWN statuses visible and diagnosable", () => {
    render(<VideoStatusBadge status="weird_new_state" />);
    expect(screen.getByText(/weird_new_state/)).toBeInTheDocument();
  });
});

describe("AtomStatusBadge", () => {
  it("maps disputed", () => {
    render(<AtomStatusBadge status="disputed" />);
    expect(screen.getByText("存疑")).toBeInTheDocument();
  });
});

describe("PolarityBadge", () => {
  it("maps require", () => {
    render(<PolarityBadge polarity="require" />);
    expect(screen.getByText("规范要求")).toBeInTheDocument();
  });
});

describe("ConflictStatusBadge", () => {
  it("maps decided actions", () => {
    render(<ConflictStatusBadge status="decided:accept_b" />);
    expect(screen.getByText("已采纳 B")).toBeInTheDocument();
  });
  it("maps open states", () => {
    render(<ConflictStatusBadge status="needs_user_decision" />);
    expect(screen.getByText("待你裁决")).toBeInTheDocument();
  });
});
