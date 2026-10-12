import { PeopleAndVoiceWorkspace } from "@/components/PeopleAndVoiceWorkspace";

export const dynamic = "force-dynamic";

export default async function ProfilesPage({ searchParams }: { searchParams?: Promise<{ view?: string; task?: string }> }) {
  const params = searchParams ? await searchParams : {};
  return <PeopleAndVoiceWorkspace initialView={params.view === "voices" ? "voices" : "people"} returnTaskId={params.task} />;
}
