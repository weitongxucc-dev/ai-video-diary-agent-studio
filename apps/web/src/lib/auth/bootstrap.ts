type BootstrapRpcResult = {
  workspace_id: string;
  onboarding_complete: boolean;
};

type BootstrapClient = {
  rpc(
    name: "bootstrap_personal_workspace",
    args: { workspace_name: string },
  ): PromiseLike<{ data: BootstrapRpcResult[] | null; error: { message: string } | null }>;
};

export type BootstrapResult =
  | { ok: true; workspaceId: string; nextPath: "/" | "/onboarding" }
  | { ok: false; message: string };

export const WORKSPACE_BOOTSTRAP_ERROR =
  "账号已登录，但个人工作区初始化失败。请稍后重试；问题持续时请联系内测管理员。";

function personalWorkspaceName(email: string | undefined): string {
  const emailName = email?.split("@")[0]?.trim();
  const ownerName = emailName || "我的";
  return `${ownerName.slice(0, 68)}的创作空间`;
}

export async function bootstrapPersonalWorkspace(
  client: BootstrapClient,
  email?: string,
): Promise<BootstrapResult> {
  const { data, error } = await client.rpc("bootstrap_personal_workspace", {
    workspace_name: personalWorkspaceName(email),
  });

  const bootstrap = data?.[0];
  if (error || !bootstrap?.workspace_id) {
    return {
      ok: false,
      message: WORKSPACE_BOOTSTRAP_ERROR,
    };
  }

  return {
    ok: true,
    workspaceId: bootstrap.workspace_id,
    nextPath: bootstrap.onboarding_complete ? "/" : "/onboarding",
  };
}
