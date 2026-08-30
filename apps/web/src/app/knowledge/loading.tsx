import { AppShell } from "@/components/AppShell";
import {
  ListSkeleton,
  LoadingShell,
  SidebarSkeletonCards,
  Skeleton,
} from "@/components/Skeleton";

export default function KnowledgeLoading() {
  return (
    <AppShell>
      <LoadingShell label="正在读取知识库...">
        <div className="workspace-page">
          <section className="workspace-main ui-panel">
            <div className="workspace-header">
              <div>
                <Skeleton height={12} width={92} />
                <Skeleton height={26} width={240} />
                <Skeleton height={12} width={360} />
              </div>
              <Skeleton height={32} width={140} />
            </div>

            <div className="workspace-search">
              <Skeleton height={32} width="100%" />
              <Skeleton height={32} width={120} />
            </div>

            <ListSkeleton count={6} itemClassName="loading-knowledge-row" />
          </section>

          <aside className="workspace-side">
            <SidebarSkeletonCards count={3} />
          </aside>
        </div>
      </LoadingShell>
    </AppShell>
  );
}