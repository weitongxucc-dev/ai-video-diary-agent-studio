import { AppShell } from "@/components/AppShell";
import { LoadingShell, Skeleton } from "@/components/Skeleton";
import styles from "./KnowledgeWorkspace.module.css";

export default function KnowledgeLoading() {
  return (
    <AppShell>
      <LoadingShell label="正在读取知识库…">
        <div aria-label="知识库内容占位" className={styles.page}>
          <div className={styles.top}>
            <div className={styles.categories}>
              {Array.from({ length: 4 }, (_, index) => (
                <div className={styles.category} key={index}>
                  <Skeleton height={20} width={20} />
                  <Skeleton height={14} width="70%" />
                  <Skeleton height={10} width="60%" />
                </div>
              ))}
            </div>
          </div>
          <div className={styles.searchBar}>
            <Skeleton height={36} width="100%" />
          </div>
          <section className={styles.card}>
            <Skeleton height={15} width={110} />
            <div className={styles.grid}>
              {Array.from({ length: 6 }, (_, index) => (
                <div className={styles.cover} key={index}>
                  <Skeleton height={100} width="100%" />
                  <Skeleton height={13} width="80%" />
                  <Skeleton height={11} width="60%" />
                </div>
              ))}
            </div>
          </section>
        </div>
      </LoadingShell>
    </AppShell>
  );
}
