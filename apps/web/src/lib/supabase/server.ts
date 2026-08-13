import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { getSupabasePublicConfig } from "./config";

export async function createClient() {
  const result = getSupabasePublicConfig();
  if (!result.ok) {
    throw new Error(result.message);
  }

  const cookieStore = await cookies();

  return createServerClient(result.config.url, result.config.publishableKey, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          cookiesToSet.forEach(({ name, value, options }) => {
            cookieStore.set(name, value, options);
          });
        } catch {
          // Server Components cannot write cookies. Middleware refreshes the
          // session before rendering; Route Handlers and Actions can write.
        }
      },
    },
  });
}
