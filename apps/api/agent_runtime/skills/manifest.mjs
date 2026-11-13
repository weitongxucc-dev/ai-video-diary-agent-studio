import { readFile, readdir } from "node:fs/promises";
import { join } from "node:path";

export const SKILL_MANIFEST_VERSION = 1;
const APPROVED_CODE_LICENSES = new Set(["MIT", "Apache-2.0", "CC0-1.0"]);

// Imported skills are method documents. They may name product tools, but they
// never gain a loader, network, shell, or arbitrary-code capability from a
// manifest. This list is deliberately narrow and reviewed with each adapter.
const PRODUCT_TOOL_NAMES = new Set([
  "read_current_task", "read_task_object", "read_task_edit_capabilities",
  "read_material_evidence", "read_task_observations", "observe_task_output",
  "read_edit_history", "read_edit_control_context", "read_timeline_restore_request",
  "preview_typed_edit", "apply_typed_edit", "read_creation_skill",
]);

function invalid(message) {
  throw new Error(`Skill manifest 无效：${message}`);
}

function nonEmptyString(value, field) {
  if (typeof value !== "string" || !value.trim()) invalid(`${field} 必须是非空字符串`);
  return value;
}

function stringList(value, field) {
  if (!Array.isArray(value) || value.some(item => typeof item !== "string" || !item.trim())) {
    invalid(`${field} 必须是字符串数组`);
  }
  return value;
}

export function validateSkillManifest(manifest, {skillName, skillPath} = {}) {
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) invalid("根必须是对象");
  if (manifest.schema_version !== SKILL_MANIFEST_VERSION) invalid("schema_version 不受支持");
  const name = nonEmptyString(manifest.name, "name");
  if (skillName && name !== skillName) invalid(`name ${name} 与 Skill ${skillName} 不一致`);
  nonEmptyString(manifest.version, "version");
  if (!manifest.entry || typeof manifest.entry !== "object") invalid("缺少 entry");
  nonEmptyString(manifest.entry.skill, "entry.skill");
  nonEmptyString(manifest.entry.adapter, "entry.adapter");
  stringList(manifest.inputs, "inputs");
  stringList(manifest.outputs, "outputs");
  stringList(manifest.permissions, "permissions");
  if (manifest.permissions.some(item => /network|shell|arbitrary|github|download|execute/i.test(item))) {
    invalid("permissions 不得声明网络、Shell、任意代码或外部下载");
  }
  if (!manifest.dependencies || typeof manifest.dependencies !== "object") invalid("缺少 dependencies");
  stringList(manifest.dependencies.skills, "dependencies.skills");
  stringList(manifest.dependencies.tools, "dependencies.tools");
  for (const name of manifest.dependencies.tools) {
    if (!PRODUCT_TOOL_NAMES.has(name)) invalid(`dependencies.tools 包含未受控工具 ${name}`);
  }
  if (!manifest.runtime || typeof manifest.runtime !== "object") invalid("缺少 runtime");
  nonEmptyString(manifest.runtime.kind, "runtime.kind");
  if (manifest.runtime.execution_mode !== "method-only" || manifest.runtime.runnable !== false) {
    invalid("runtime 必须明确是不可直接运行的方法文档");
  }
  if (manifest.runtime.network !== false || manifest.runtime.shell !== false || manifest.runtime.arbitrary_code !== false) {
    invalid("runtime 必须明确关闭 network、shell 和 arbitrary_code");
  }
  if (!manifest.install_checks || typeof manifest.install_checks !== "object") invalid("缺少 install_checks");
  nonEmptyString(manifest.install_checks.mode, "install_checks.mode");
  stringList(manifest.install_checks.checks, "install_checks.checks");
  if (manifest.install_checks.network !== false || manifest.install_checks.shell !== false || manifest.install_checks.arbitrary_code !== false) {
    invalid("install_checks 必须明确关闭 network、shell 和 arbitrary_code");
  }
  if (!manifest.adoption || typeof manifest.adoption !== "object") invalid("缺少 adoption");
  nonEmptyString(manifest.adoption.entrypoint, "adoption.entrypoint");
  if (manifest.adoption.status !== "installed" || manifest.adoption.writes !== false || manifest.adoption.requires_explicit_edit_authorization !== true) {
    invalid("adoption 必须是 installed、只读且要求明确编辑授权");
  }
  if (!manifest.rollback || typeof manifest.rollback !== "object") invalid("缺少 rollback");
  nonEmptyString(manifest.rollback.mode, "rollback.mode");
  nonEmptyString(manifest.rollback.pre_adoption, "rollback.pre_adoption");
  nonEmptyString(manifest.rollback.post_adoption, "rollback.post_adoption");
  if (manifest.rollback.deletes_user_data !== false) invalid("rollback 不得删除用户数据");
  if (!manifest.provenance || typeof manifest.provenance !== "object") invalid("缺少 provenance");
  nonEmptyString(manifest.provenance.repository, "provenance.repository");
  nonEmptyString(manifest.provenance.revision, "provenance.revision");
  nonEmptyString(manifest.provenance.license, "provenance.license");
  if (!manifest.provenance.license_spdx || typeof manifest.provenance.license_spdx !== "string") {
    invalid("缺少 provenance.license_spdx");
  }
  if (!APPROVED_CODE_LICENSES.has(manifest.provenance.license_spdx)) {
    invalid(`provenance.license_spdx ${manifest.provenance.license_spdx} 不在可导入许可范围`);
  }
  if (!manifest.evaluation || typeof manifest.evaluation !== "object") invalid("缺少 evaluation");
  nonEmptyString(manifest.evaluation.status, "evaluation.status");
  stringList(manifest.evaluation.evidence, "evaluation.evidence");
  if (skillPath && typeof skillPath !== "string") invalid("skillPath 无效");
  return Object.freeze({...manifest, skillPath});
}

function frontmatterName(markdown) {
  const match = /^---\s*\nname:\s*([^\n]+)\n/m.exec(markdown);
  const name = match?.[1]?.trim();
  if (!name) invalid("SKILL.md 缺少 frontmatter name");
  return name;
}

/** Load only explicit manifest.json files under the trusted, packaged skills root. */
export async function loadSkillManifests(skillsRoot) {
  const result = new Map();
  const entries = await readdir(skillsRoot, {withFileTypes: true});
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const directory = join(skillsRoot, entry.name);
    let raw;
    try { raw = await readFile(join(directory, "manifest.json"), "utf8"); }
    catch (error) { if (error?.code === "ENOENT") continue; throw error; }
    const manifest = validateSkillManifest(JSON.parse(raw), {skillName: frontmatterName(await readFile(join(directory, "SKILL.md"), "utf8")), skillPath: join(directory, "SKILL.md")});
    if (result.has(manifest.name)) invalid(`重复 Skill ${manifest.name}`);
    result.set(manifest.name, manifest);
  }
  return result;
}
