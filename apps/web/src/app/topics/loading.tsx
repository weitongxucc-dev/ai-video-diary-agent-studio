import { AppShell } from "@/components/AppShell";
import {
  ListSkeleton,
  LoadingShell,
  Skeleton,
} from "@/components/Skeleton";

export default function TopicsLoading() {
  return (
    <AppShell>
      <LoadingShell label="正在拉取候选选题...">
        <div className="topic-page">
          <section className="topic-list">
            {Array.from({ length: 4 }).map((_, index) => (
              <div className="topic-card pixel-card" key={`topic-card-${index}`}>
                <div className="topic-card-header">
                  <Skeleton height={24} width={48} />
                  <Skeleton height={14} width="35%" />
                </div>
                <Skeleton height={20} width="75%" />
                <Skeleton height={12} width="90%" />
                <Skeleton height={12} width="70%" />
                <div className="source-row">
                  <Skeleton height={18} width={84} />
                  <Skeleton height={18} width={110} />
                </div>
              </div>
            ))}
          </section>

          <section className="topic-detail pixel-card">
            <header className="topic-detail-header">
              <div>
                <Skeleton height={12} width={92} />
                <Skeleton height={24} width="55%" />
                <Skeleton height={12} width="80%" />
              </div>
              <div className="topic-detail-status">
                <Skeleton height={10} width="40%" />
                <Skeleton height={28} width={64} />
                <Skeleton height={14} width="60%" />
              </div>
            </header>

            <div className="topic-review-workbench">
              <div className="topic-detail-main">
                {Array.from({ length: 3 }).map((_, index) => (
                  <section
                    className="topic-console-panel"
                    key={`console-${index}`}
                  >
                    <Skeleton height={14} width="35%" />
                    <ListSkeleton count={3} />
                  </section>
                ))}
              </div>
              <aside className="topic-review-aside">
                <Skeleton height={140} width="100%" />
                <Skeleton height={120} width="100%" />
              </aside>
            </div>
          </section>
        </div>
      </LoadingShell>
    </AppShell>
  );
}