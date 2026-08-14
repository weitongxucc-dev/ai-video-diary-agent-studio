"use client";

import { createBrowserClient } from "@supabase/ssr";
import { getSupabasePublicConfig } from "./config";

export function createClient() {
  const result = getSupabasePublicConfig();
  if (!result.ok) {
    throw new Error(result.message);
  }

  return createBrowserClient(result.config.url, result.config.publishableKey);
}
