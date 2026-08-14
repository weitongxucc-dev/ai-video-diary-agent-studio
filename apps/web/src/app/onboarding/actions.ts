"use server";

import { redirect } from "next/navigation";
import { completeOnboarding } from "@/lib/api.server";

export async function completeOnboardingAction(): Promise<never> {
  let completed = false;
  try {
    const result = await completeOnboarding();
    completed = result.completed;
  } catch {
    redirect("/onboarding?error=completion");
  }

  if (!completed) {
    redirect("/onboarding?error=completion");
  }
  redirect("/");
}
