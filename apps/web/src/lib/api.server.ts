import "server-only";

import { setServerAuthorizationProvider } from "./api";
import { createClient } from "./supabase/server";

setServerAuthorizationProvider(async () => {
  try {
    const supabase = await createClient();
    const { data: claimsData, error } = await supabase.auth.getClaims();
    if (error || !claimsData?.claims?.sub) return null;

    const { data } = await supabase.auth.getSession();
    return data.session?.access_token
      ? `Bearer ${data.session.access_token}`
      : null;
  } catch {
    return null;
  }
});

export * from "./api";
