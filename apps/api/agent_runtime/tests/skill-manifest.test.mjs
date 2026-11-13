import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {dirname, join} from "node:path";
import {fileURLToPath} from "node:url";
import {loadSkillManifests, validateSkillManifest} from "../skills/manifest.mjs";

const root = dirname(dirname(fileURLToPath(import.meta.url)));

test("packaged external editing skill has a fixed, safe manifest", async () => {
  const manifests = await loadSkillManifests(join(root, "skills"));
  const manifest = manifests.get("cut-as-code-video-cut");
  assert.equal(manifest.provenance.revision, "a96c13e6eaa72e6882e78df08ee4c04fa7be3bc4");
  assert.equal(manifest.runtime.network, false);
  assert.equal(manifest.runtime.arbitrary_code, false);
  assert.equal(manifest.runtime.execution_mode, "method-only");
  assert.equal(manifest.runtime.runnable, false);
  assert.equal(manifest.install_checks.mode, "package-time");
  assert.equal(manifest.adoption.status, "installed");
  assert.equal(manifest.rollback.deletes_user_data, false);
  assert.ok(manifest.dependencies.tools.includes("preview_typed_edit"));
  assert.match(await readFile(manifest.skillPath, "utf8"), /受控适配/);
});

test("OpenFilm replicate skill is packaged as a method-only adapter", async () => {
  const manifests = await loadSkillManifests(join(root, "skills"));
  const manifest = manifests.get("openfilm-replicate");
  assert.equal(manifest.provenance.repository, "https://github.com/openfilm/skill-replicate");
  assert.equal(manifest.provenance.revision, "eef1ee5eda2a2aaeec60a3f133d73b86dd53c053");
  assert.equal(manifest.provenance.license_spdx, "MIT");
  assert.equal(manifest.runtime.runnable, false);
  assert.equal(manifest.runtime.network, false);
  assert.ok(manifest.dependencies.tools.includes("read_editing_resource_catalog"));
  assert.match(await readFile(manifest.skillPath, "utf8"), /参考视频/);
});

test("manifest rejects external execution and tools outside the product boundary", () => {
  const base = {
    schema_version: 1, name: "test", version: "1", entry: {skill: "x", adapter: "y"},
    inputs: ["input"], outputs: ["output"], permissions: ["read"],
    dependencies: {skills: [], tools: ["read_current_task"]},
    runtime: {kind: "trusted-markdown", execution_mode: "method-only", runnable: false, network: false, shell: false, arbitrary_code: false},
    install_checks: {mode: "package-time", network: false, shell: false, arbitrary_code: false, checks: ["check"]},
    adoption: {entrypoint: "read_creation_skill", status: "installed", writes: false, requires_explicit_edit_authorization: true},
    rollback: {mode: "pinned-package-revert", pre_adoption: "remove", post_adoption: "query", deletes_user_data: false},
    provenance: {repository: "https://example.invalid/repo", revision: "abc", license: "MIT", license_spdx: "MIT"},
    evaluation: {status: "unknown", evidence: []},
  };
  assert.throws(() => validateSkillManifest({...base, runtime: {...base.runtime, network: true}}), /network/);
  assert.throws(() => validateSkillManifest({...base, runtime: {...base.runtime, shell: true}}), /shell/);
  assert.throws(() => validateSkillManifest({...base, runtime: {...base.runtime, runnable: true}}), /不可直接运行/);
  assert.throws(() => validateSkillManifest({...base, rollback: {...base.rollback, deletes_user_data: true}}), /删除用户数据/);
  assert.throws(() => validateSkillManifest({...base, dependencies: {skills: [], tools: ["bash"]}}), /未受控工具/);
  assert.equal(validateSkillManifest({...base, provenance: {...base.provenance, license: "Other", license_spdx: "LicenseRef-Local"}}).provenance.license_spdx, "LicenseRef-Local");
});
