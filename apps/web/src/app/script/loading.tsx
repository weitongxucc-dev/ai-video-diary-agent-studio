import { AppShell } from "@/components/AppShell";
import {
  ListSkeleton,
  LoadingShell,
  Skeleton,
} from "@/components/Skeleton";

export default function ScriptLoading() {
  return (
    <AppShell>
      <LoadingShell label="正在加载口播稿...">
        <div className="script-page">
          <section className="script-main">
            <section className="script-dashboard ui-panel">
              {Array.from({ length: 4 }).map((_, index) => (
                <div className="script-dashboard-stat" key={`stat-${index}`}>
                  <Skeleton height={10} width="50%" />
                  <Skeleton height={20} width="60%" />
                  <Skeleton height={6} width="80%" />
                </div>
              ))}
            </section>

            <section className="script-section ui-panel">
              <Skeleton height={14} width={120} />
              <Skeleton height={120} width="100%" />
            </section>

            <ListSkeleton count={3} itemClassName="loading-script-section" />
          </section>

          <aside className="script-side">
            <Skeleton height={140} width="100%" />
            <Skeleton height={80} width="100%" />
            <Skeleton height={80} width="100%" />
          </aside>
        </div>
      </LoadingShell>
    </AppShell>
  );
}