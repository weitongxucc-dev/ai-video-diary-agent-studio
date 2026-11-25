import type { CatalogResource } from "../EditorResourceLibraryPanel";
import styles from "../EditorResourceLibraryPanel.module.css";

function skillAvailability(resource: CatalogResource) {
  if (resource.status === "planned") return { label: "计划中", note: "当前版本尚未接通此技能。" };
  if (resource.status === "failed") return { label: "暂不可用", note: "当前无法使用此技能。" };
  const requirements = resource.runtime_requirements;
  if (resource.status === "connected" && resource.entrypoints.includes("read_creation_skill")) {
    if (requirements?.runnable === true) return { label: "可用", note: "可由助手按当前作品的工具权限使用。" };
    if (requirements?.execution_mode === "method-only") {
      return { label: "可用方法", note: "助手可读取此方法。实际操作由当前作品已接通的工具完成。" };
    }
  }
  return { label: "仅供参考", note: "当前目录只提供参考信息。" };
}

export function SkillCatalogCard({ resource }: { resource: CatalogResource }) {
  const { label, note } = skillAvailability(resource);
  const { manifest_version, status_reason, capabilities } = resource.runtime_requirements ?? {};
  const specification = capabilities as { inputs?: string[]; outputs?: string[]; permissions?: string[] } | undefined;
  const sections = [
    ["输入", specification?.inputs],
    ["输出", specification?.outputs],
    ["权限范围", specification?.permissions],
  ] as const;
  return <article className={`${styles.catalogCard} ${styles.skillCard}`} aria-label={resource.name}>
    <div><strong>{resource.name}</strong><span>{label}</span></div>
    <p>{note}</p>
    <details>
      <summary>查看详情</summary>
      <div className={styles.catalogPreview}>
        <p>版本 {typeof manifest_version === "string" ? manifest_version : `v${resource.version}`}</p>
        {typeof status_reason === "string" ? <p>{status_reason}</p> : null}
        {sections.map(([title, entries]) => entries?.length ? <div key={title}>
          <strong>{title}</strong>
          <ul>{entries.map(entry => <li key={entry}>{entry}</li>)}</ul>
        </div> : null)}
        {resource.provenance.map(source => <p key={`${source.repository}:${source.path}:${source.revision}`}>
          来源 {source.repository} · {source.path} · 修订 {source.revision} · {source.license}
        </p>)}
      </div>
    </details>
  </article>;
}
