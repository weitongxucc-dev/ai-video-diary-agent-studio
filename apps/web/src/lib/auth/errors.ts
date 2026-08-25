const AUTH_ERROR_MESSAGES: Record<string, string> = {
  "Invalid login credentials": "账号或密码不正确，请检查后重试。",
  "Email not confirmed": "账号尚未启用，请联系工作区管理员。",
  "Token has expired or is invalid": "账号设置会话已失效，请联系管理员重新设置。",
};

export function readableAuthError(error: unknown): string {
  const message = error instanceof Error ? error.message : "";
  if (message === SUPABASE_CONFIG_ERROR || message === WORKSPACE_BOOTSTRAP_ERROR) {
    return message;
  }
  if (message in AUTH_ERROR_MESSAGES) {
    return AUTH_ERROR_MESSAGES[message];
  }
  return "账号服务暂时无法完成请求。请稍后重试；问题持续时请联系工作区管理员。";
}
import { SUPABASE_CONFIG_ERROR } from "@/lib/supabase/config";
import { WORKSPACE_BOOTSTRAP_ERROR } from "./bootstrap";
