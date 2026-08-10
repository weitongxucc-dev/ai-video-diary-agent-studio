import { AppShell } from "@/components/AppShell";
import {
  ListSkeleton,
  LoadingShell,
  SidebarSkeletonCards,
  Skeleton,
} from "@/components/Skeleton";

export default function LearningLoading() {
  return (
    <AppShell>
      <LoadingShell label="正在汇总偏好记忆...">
        <div className="workspace-page">
          <section className="workspace-main pixel-card">
            <div className="workspace-header">
              <div>
                <Skeleton height={12} width={92} />
                <Skeleton height={26} width={200} />
                <Skeleton height={12} width={320} />
              </div>
              <Skeleton height={32} width={140} />
            </div>

            <div className="learning-summary">
              {Array.from({ length: 4 }).map((_, index) => (
                <div className="loading-metric-card" key={`metric-${index}`}>
                  <Skeleton height={18} variant="circle" width={18} />
                  <Skeleton height={26} width="60%" />
                  <Skeleton height={10} width="40%" />
                </div>
              ))}
            </div>

            <section className="preference-cards-section">
              <div className="preference-cards-heading">
                <Skeleton height={14} width="30%" />
                <Skeleton height={12} width="45%" />
              </div>
              {Array.from({ length: 3 }).map((_, index) => (
                <div
                  className="preference-card enabled"
                  key={`pref-card-${index}`}
                >
                  <div className="preference-card-main">
                    <div className="preference-card-link">
                      <Skeleton height={14} width="60%" />
                      <Skeleton height={10} width="40%" />
                    </div>
                    <div className="preference-card-meta">
                      <Skeleton height={20} width={120} />
                      <Skeleton height={18} width={92} />
                    </div>
                    <div className="preference-card-actions">
                      <Skeleton height={28} width={120} />
                    </div>
                  </div>
                </div>
              ))}
            </section>

            <ListSkeleton count={4} itemClassName="loading-feedback-row" />
          </section>

          <aside className="workspace-side">
            <SidebarSkeletonCards count={2} />
          </aside>
        </div>
      </LoadingShell>
    </AppShell>
  );
}