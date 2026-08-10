import { AppShell } from "@/components/AppShell";
import {
  ListSkeleton,
  LoadingShell,
  SidebarSkeletonCards,
  Skeleton,
} from "@/components/Skeleton";

export default function DraftLoading() {
  return (
    <AppShell>
      <LoadingShell label="正在加载剪映草稿...">
        <div className="workspace-page">
          <section className="workspace-main pixel-card draft-main">
            <div className="workspace-header">
              <div>
                <Skeleton height={12} width={92} />
                <Skeleton height={26} width={240} />
                <Skeleton height={12} width={340} />
              </div>
              <Skeleton height={32} width={140} />
            </div>

            <div className="draft-flow">
              {Array.from({ length: 4 }).map((_, index) => (
                <div className="draft-flow-step active" key={`flow-${index}`}>
                  <Skeleton height={14} width="50%" />
                  <Skeleton height={10} width="80%" />
                </div>
              ))}
            </div>

            <ListSkeleton count={4} itemClassName="loading-draft-row" />

            <div className="workspace-subsection">
              <Skeleton height={18} width={180} />
              <ListSkeleton count={3} itemClassName="loading-draft-row compact" />
            </div>
          </section>

          <aside className="workspace-side">
            <SidebarSkeletonCards count={4} />
          </aside>
        </div>
      </LoadingShell>
    </AppShell>
  );
}