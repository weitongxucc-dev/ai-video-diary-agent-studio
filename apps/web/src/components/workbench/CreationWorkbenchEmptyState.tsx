import styles from "./CreationWorkbenchEmptyState.module.css";

export function CreationWorkbenchEmptyState() {
  return (
    <section className={styles.root} aria-label="新建视频工作区">
      <div className={styles.content}>
        <h1>新建视频</h1>
        <p>先在左侧告诉曜核你想做什么。确定目标后，这里会切换为作品剪辑台。</p>
      </div>
    </section>
  );
}
