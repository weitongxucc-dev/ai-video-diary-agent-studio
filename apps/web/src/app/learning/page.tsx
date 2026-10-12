import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

export default function LearningPage(): never {
  redirect("/profile");
}
