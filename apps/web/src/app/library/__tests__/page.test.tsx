import { render, screen } from "@testing-library/react";
import type { AnchorHTMLAttributes } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const serverMocks = vi.hoisted(() => ({
  getMaterialCollections: vi.fn(),
  getMaterialLibraryInsights: vi.fn(),
  getProductionMaterials: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a>,
}));
vi.mock("@/lib/api.server", () => ({
  getMaterialCollections: serverMocks.getMaterialCollections,
  getMaterialLibraryInsights: serverMocks.getMaterialLibraryInsights,
  getProductionMaterials: serverMocks.getProductionMaterials,
}));
vi.mock("@/components/MaterialLibraryWorkspace", () => ({
  MaterialLibraryWorkspace: ({ initialTag }: { initialTag?: string }) => (
    <div data-testid="workspace" data-tag={initialTag ?? ""} />
  ),
}));

import LibraryPage from "@/app/library/page";

const empty = { assets: [], total_count: 0, limit: 24, offset: 0, has_more: false };

describe("LibraryPage tag filter", () => {
  beforeEach(() => {
    serverMocks.getProductionMaterials.mockReset();
    serverMocks.getMaterialLibraryInsights.mockReset();
    serverMocks.getMaterialCollections.mockReset();
    serverMocks.getProductionMaterials.mockResolvedValue(empty);
    serverMocks.getMaterialLibraryInsights.mockResolvedValue({ asset_count: 0, segment_count: 0, tags: [], recommended_gaps: [] });
    serverMocks.getMaterialCollections.mockResolvedValue({ collections: [], total_count: 0 });
  });

  it("sends the query value through unchanged so a tag with spaces still matches the server's exact-name filter", async () => {
    render(await LibraryPage({ searchParams: Promise.resolve({ tag: " 产品演示 " }) }));
    expect(serverMocks.getProductionMaterials).toHaveBeenCalledWith({ tag: " 产品演示 " });
    expect(screen.getByTestId("workspace")).toHaveAttribute("data-tag", " 产品演示 ");
  });

  it("treats only a missing or empty tag as no filter", async () => {
    render(await LibraryPage({ searchParams: Promise.resolve({}) }));
    expect(serverMocks.getProductionMaterials).toHaveBeenCalledWith({ tag: undefined });
    expect(screen.getByTestId("workspace")).toHaveAttribute("data-tag", "");
  });
});