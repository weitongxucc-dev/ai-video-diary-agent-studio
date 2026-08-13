const AUTH_ERROR_MESSAGES: Record<string, string> = {
  "Invalid login credentials": "邮箱或密码不正确。请检查后重试；如果你尚未受邀，请联系内测管理员。",
  "Email not confirmed": "邮箱邀请尚未确认。请先打开邀请邮件中的链接，再回来登录。",
  "Token has expired or is invalid": "邀请或恢复链接已失效。请联系管理员重新发送。",
};

export function readableAuthError(error: unknown): string {
  const message = error instanceof Error ? error.message : "";
  if (message === SUPABASE_CONFIG_ERROR || message === WORKSPACE_BOOTSTRAP_ERROR) {
    return message;
  }
  if (message in AUTH_ERROR_MESSAGES) {
    return AUTH_ERROR_MESSAGES[message];
  }
  return "登录服务暂时无法完成请求。请检查网络后重试；问题持续时请联系内测管理员。";
}
import { SUPABASE_CONFIG_ERROR } from "@/lib/supabase/config";
import { WORKSPACE_BOOTSTRAP_ERROR } from "./bootstrap";
