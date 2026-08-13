import { describe, expect, it, vi } from "vitest";
import {
  bootstrapPersonalWorkspace,
  WORKSPACE_BOOTSTRAP_ERROR,
} from "../bootstrap";

describe("workspace bootstrap", () => {
  it("creates or restores onboarding state through one idempotent RPC", async () => {
    const rpc = vi.fn().mockResolvedValue({
      data: [{ workspace_id: "workspace-1", onboarding_complete: false }],
      error: null,
    });

    await expect(
      bootstrapPersonalWorkspace({ rpc }, "creator@example.com"),
    ).resolves.toEqual({
      ok: true,
      workspaceId: "workspace-1",
      nextPath: "/onboarding",
    });
    expect(rpc).toHaveBeenCalledWith("bootstrap_personal_workspace", {
      workspace_name: "creator的创作空间",
    });
  });

  it("restores completed users to the workbench", async () => {
    const rpc = vi.fn().mockResolvedValue({
      data: [{ workspace_id: "workspace-1", onboarding_complete: true }],
      error: null,
    });

    await expect(bootstrapPersonalWorkspace({ rpc })).resolves.toMatchObject({
      ok: true,
      nextPath: "/",
    });
  });

  it("returns a useful error when the RPC fails", async () => {
    const rpc = vi.fn().mockResolvedValue({
      data: null,
      error: { message: "database unavailable" },
    });

    await expect(bootstrapPersonalWorkspace({ rpc })).resolves.toEqual({
      ok: false,
      message: WORKSPACE_BOOTSTRAP_ERROR,
    });
  });
});
