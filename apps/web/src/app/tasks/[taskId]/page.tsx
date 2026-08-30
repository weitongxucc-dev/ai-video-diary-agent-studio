import { notFound } from "next/navigation";
import { CreationTaskWorkspace } from "@/components/CreationTaskWorkspace";
import {
  getCreationTaskTopics,
  getCreationTaskWorkspace,
  listCreationTasks,
} from "@/lib/api.server";

export const dynamic = "force-dynamic";

type Props = {
  params: Promise<{ taskId: string }>;
};

export default async function TaskPage({ params }: Props) {
  const { taskId } = await params;
  const workspace = await getCreationTaskWorkspace(taskId).catch(() => null);
  if (!workspace) notFound();
  const [taskList, topics] = await Promise.all([
    listCreationTasks().then((response) => response.items).catch(() => [workspace.task]),
    getCreationTaskTopics(taskId).catch(() => null),
  ]);
  return (
    <CreationTaskWorkspace
      initialTasks={taskList}
      initialTopics={topics}
      initialWorkspace={workspace}
    />
  );
}
