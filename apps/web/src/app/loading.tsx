import { AppShell } from "@/components/AppShell";
import {
  ListSkeleton,
  LoadingShell,
  MetricSkeletonRow,
  Skeleton,
} from "@/components/Skeleton";

export default function HomeLoading() {
  return (
    <AppShell>
      <LoadingShell label="正在加载今日工作台...">
        <div className="workbench-grid">
          <div className="workbench-main">
            <section className="episode-progress ui-panel">
              <div className="episode-progress-flow">
                {Array.from({ length: 9 }).map((_, index) => (
                  <div className="progress-step" key={`step-${index}`}>
                    <div className="progress-step-card">
                      <Skeleton height={10} width="70%" />
                      <Skeleton height={18} variant="circle" width={18} />
                      <Skeleton height={10} width="80%" />
                      <Skeleton height={14} width="40%" />
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <section className="task-banner ui-panel">
              <div className="task-terminal">
                <Skeleton height={26} width="55%" />
                <Skeleton height={14} width="80%" />
                <Skeleton height={14} width="65%" />
              </div>
              <div className="task-queue">
                <Skeleton height={14} width="40%" />
                <ol>
                  {Array.from({ length: 4 }).map((_, index) => (
                    <li key={`queue-${index}`}>
                      <Skeleton height={18} width={28} />
                      <Skeleton height={12} width="70%" />
                    </li>
                  ))}
                </ol>
              </div>
              <div className="task-agent-next">
                <Skeleton height={48} variant="circle" width={48} />
                <div>
                  <Skeleton height={12} width="80%" />
                  <Skeleton height={12} width="60%" />
                </div>
                <Skeleton height={32} width={140} />
              </div>
            </section>

            <MetricSkeletonRow count={4} />

            <section className="current-topic ui-panel">
              <div className="current-topic-main">
                <div>
                  <Skeleton height={12} width={92} />
                  <Skeleton height={22} width="60%" />
                  <Skeleton height={12} width="90%" />
                  <Skeleton height={12} width="70%" />
                </div>
                <div className="topic-actions">
                  <Skeleton height={32} width={120} />
                  <Skeleton height={32} width={150} />
                </div>
              </div>
              <div className="topic-review-panel">
                <Skeleton height={14} width="40%" />
                <ListSkeleton count={4} itemClassName="loading-topic-row" />
              </div>
            </section>
          </div>

          <aside className="loading-agent-panel">
            <Skeleton height={120} width="100%" />
            <Skeleton height={80} width="100%" />
            <Skeleton height={80} width="100%" />
          </aside>
        </div>
      </LoadingShell>
    </AppShell>
  );
}