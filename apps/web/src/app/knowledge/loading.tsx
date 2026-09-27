import { AppShell } from "@/components/AppShell";
import { Skeleton } from "@/components/Skeleton";
import styles from "./KnowledgeWorkspace.module.css";

export default function KnowledgeLoading() {
  return <AppShell>
    <div aria-busy="true" aria-label="正在读取知识库" className={styles.page}>
      <div className={styles.top}><div className={styles.categories}>{Array.from({ length: 4 }, (_, index) => <div className={styles.category} key={index}><Skeleton height={20} width={20} /><Skeleton height={14} width="70%" /><Skeleton height={10} width="60%" /></div>)}</div></div>
      <div className={styles.searchBar}><Skeleton height={36} width="100%" /></div>
      <div className={styles.columns}><section className={styles.card}><Skeleton height={15} width={110} /><div className={styles.grid}>{Array.from({ length: 6 }, (_, index) => <div className={styles.cover} key={index}><Skeleton height={100} width="100%" /><Skeleton height={13} width="80%" /><Skeleton height={11} width="60%" /></div>)}</div></section><section className={styles.card}><Skeleton height={15} width={110} /><Skeleton height={90} width="100%" /></section></div>
    </div>
  </AppShell>;
}
