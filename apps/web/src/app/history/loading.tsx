import { AppShell } from "@/components/AppShell";
import {
  LoadingShell,
  SidebarSkeletonCards,
  Skeleton,
} from "@/components/Skeleton";

export default function HistoryLoading() {
  return (
    <AppShell>
      <LoadingShell label="正在拉取发布与数据...">
        <div className="history-page">
          <section className="history-main ui-panel">
            <div className="history-header">
              <div>
                <Skeleton height={12} width={92} />
                <Skeleton height={26} width={260} />
                <Skeleton height={12} width={300} />
              </div>
              <Skeleton height={32} width={140} />
            </div>

            <nav className="history-filter">
              {Array.from({ length: 4 }).map((_, index) => (
                <Skeleton height={28} key={`filter-${index}`} width={84} />
              ))}
            </nav>

            <div className="history-list">
              {Array.from({ length: 5 }).map((_, index) => (
                <div className="history-item" key={`history-${index}`}>
                  <div className="history-item-summary">
                    <div className="history-item-day">
                      <Skeleton height={14} width="70%" />
                      <Skeleton height={10} width="55%" />
                    </div>
                    <div className="history-item-body">
                      <Skeleton height={14} width="60%" />
                      <Skeleton height={10} width="80%" />
                      <Skeleton height={10} width="65%" />
                    </div>
                    <div className="history-item-actions">
                      <Skeleton height={28} width={140} />
                      <Skeleton height={28} width={140} />
                    </div>
                  </div>
                  <Skeleton height={20} width="80%" />
                </div>
              ))}
            </div>
          </section>

          <aside className="history-side">
            <SidebarSkeletonCards count={4} />
          </aside>
        </div>
      </LoadingShell>
    </AppShell>
  );
}
