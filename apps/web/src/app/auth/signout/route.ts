import { revalidatePath } from "next/cache";
import { NextResponse, type NextRequest } from "next/server";
import { createClient } from "@/lib/supabase/server";

export async function POST(request: NextRequest) {
  try {
    const supabase = await createClient();
    const { data } = await supabase.auth.getClaims();
    if (data?.claims?.sub) {
      await supabase.auth.signOut();
    }
  } catch {
    // A missing/unreachable auth service should still return to a stable page.
  }

  revalidatePath("/", "layout");
  return NextResponse.redirect(new URL("/signin", request.url), { status: 303 });
}
