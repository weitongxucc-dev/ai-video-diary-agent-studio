import { AppShell } from "@/components/AppShell";
import {
  ListSkeleton,
  LoadingShell,
  SidebarSkeletonCards,
  Skeleton,
} from "@/components/Skeleton";

export default function VideoLoading() {
  return (
    <AppShell>
      <LoadingShell label="正在扫描本地视频...">
        <div className="video-page">
          <section className="video-main pixel-card">
            <div className="video-header">
              <div>
                <Skeleton height={12} width={92} />
                <Skeleton height={26} width={200} />
                <Skeleton height={12} width={320} />
              </div>
              <Skeleton height={32} width={120} />
            </div>

            <section className="video-mode-strip">
              {Array.from({ length: 4 }).map((_, index) => (
                <div className="video-mode-card" key={`mode-${index}`}>
                  <Skeleton height={14} width="55%" />
                  <Skeleton height={10} width="90%" />
                  <Skeleton height={10} width="70%" />
                </div>
              ))}
            </section>

            <section className="video-chain">
              <div className="video-chain-heading">
                <Skeleton height={14} width="40%" />
                <Skeleton height={12} width="55%" />
              </div>
              {Array.from({ length: 5 }).map((_, index) => (
                <div className="video-chain-row" key={`chain-${index}`}>
                  <Skeleton height={22} variant="circle" width={22} />
                  <Skeleton height={12} width="55%" />
                  <Skeleton height={18} width={84} />
                </div>
              ))}
            </section>

            <ListSkeleton count={4} itemClassName="loading-video-row" />
          </section>

          <aside className="video-side pixel-card">
            <Skeleton height={18} width="60%" />
            <SidebarSkeletonCards count={4} />
          </aside>
        </div>
      </LoadingShell>
    </AppShell>
  );
}