import { notFound } from "next/navigation";
import { CreationTaskWorkspace } from "@/components/CreationTaskWorkspace";
import { WorkspaceRoute } from "@/components/workbench/WorkspaceRoute";
import {
  CreationPlanApiError,
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
  const workspace = await getCreationTaskWorkspace(taskId).catch((cause: unknown) => {
    if (cause instanceof CreationPlanApiError && cause.status === 404) notFound();
    throw cause;
  });
  const [taskList, topics] = await Promise.all([
    listCreationTasks().then((response) => response.items).catch(() => [workspace.task]),
    getCreationTaskTopics(taskId).catch(() => null),
  ]);
  return (
    <WorkspaceRoute label={workspace.task.name}><CreationTaskWorkspace
      initialTasks={taskList}
      initialTopics={topics}
      initialWorkspace={workspace}
    /></WorkspaceRoute>
  );
}
