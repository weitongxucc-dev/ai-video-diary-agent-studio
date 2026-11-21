import test from "node:test";
import assert from "node:assert/strict";
import {dirname, join} from "node:path";
import {fileURLToPath} from "node:url";
import {loadSkillManifests, validateSkillManifest} from "../skills/manifest.mjs";

const root = dirname(dirname(fileURLToPath(import.meta.url)));

test("ffmpeg-skill is discovered as a pinned, non-runnable capability catalog", async () => {
  const manifests = await loadSkillManifests(join(root, "skills"));
  const manifest = manifests.get("ffmpeg-skill");
  assert.ok(manifest);
  assert.equal(manifest.provenance.revision, "fe577168f39dd8effdf8c95b91ce16986a511f37");
  assert.equal(manifest.provenance.release_tag, "v3.12.1");
  assert.equal(manifest.provenance.license_spdx, "MIT");
  assert.equal(manifest.runtime.execution_mode, "method-only");
  assert.equal(manifest.runtime.runnable, false);
  assert.equal(manifest.catalog_status, "catalog_only");
  assert.equal(manifest.version, "v3.12.1-adapter.2");
  assert.equal(manifest.capabilities.find(item => item.id === "probe").status, "catalog_only");
  assert.equal(manifest.capabilities.find(item => item.id === "scenes").status, "planned");
  assert.match(manifest.capabilities.find(item => item.id === "caption").unavailable_reason, /字体/);
  assert.equal(manifest.capabilities.find(item => item.id === "probe").runtime_requirements.runnable, false);
  assert.equal(manifest.adapter_contract.lifecycle.cancel.runnable, false);
  assert.equal(Object.keys(manifest.adapter_contract.operation_map).length, manifest.capabilities.length);
  assert.match(await (await import("node:fs/promises")).readFile(manifest.skillPath, "utf8"), /不可用行为/);
});

test("manifest validator rejects ungoverned capability states", () => {
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
  const capability = {id: "probe", label: "Probe", status: "catalog_only", stability: "stable", inputs: ["x"], outputs: ["y"], runnable: false};
  assert.equal(validateSkillManifest({...base, catalog_status: "catalog_only", status_reason: "not wired", capabilities: [capability]}).catalog_status, "catalog_only");
  assert.throws(() => validateSkillManifest({...base, capabilities: [{...capability, status: "unknown"}]}), /capability.status/);
  assert.throws(() => validateSkillManifest({...base, capabilities: [{...capability, runnable: true}]}), /不可直接运行/);
});

test("manifest validator rejects an adapter contract that could run or omit lifecycle gaps", () => {
  const base = {
    schema_version: 1, name: "test", version: "1", entry: {skill: "x", adapter: "y"},
    inputs: ["input"], outputs: ["output"], permissions: ["read"],
    dependencies: {skills: [], tools: ["read_current_task"]},
    runtime: {kind: "trusted-markdown", execution_mode: "method-only", runnable: false, network: false, shell: false, arbitrary_code: false},
    install_checks: {mode: "package-time", network: false, shell: false, arbitrary_code: false, checks: ["check"]},
    adoption: {entrypoint: "read_creation_skill", status: "installed", writes: false, requires_explicit_edit_authorization: true},
    rollback: {mode: "pinned-package-revert", pre_adoption: "remove", post_adoption: "query", deletes_user_data: false},
    provenance: {repository: "https://example.invalid/repo", revision: "abc", license: "MIT", license_spdx: "MIT"},
    evaluation: {status: "unknown", evidence: []}, catalog_status: "catalog_only", status_reason: "not wired",
    capabilities: [{id: "probe", label: "Probe", status: "catalog_only", stability: "stable", inputs: ["x"], outputs: ["y"], dependencies: ["ffprobe"], risk: "unknown", unavailable_reason: "not wired", runnable: false}],
  };
  const contract = {
    status: "catalog_only", version: "1", runtime_requirements: {execution_mode: "method-only", runnable: false},
    operation_map: {probe: {status: "catalog_only", missing: [], runnable: false, runtime_requirements: {execution_mode: "method-only", runnable: false}}},
    lifecycle: {cancel: {status: "catalog_only", missing: [], runnable: false}},
  };
  assert.equal(validateSkillManifest({...base, adapter_contract: contract}).adapter_contract.status, "catalog_only");
  assert.throws(() => validateSkillManifest({...base, adapter_contract: {...contract, operation_map: {probe: {...contract.operation_map.probe, runnable: true}}}}), /adapter_contract\.operation_map/);
  assert.throws(() => validateSkillManifest({...base, adapter_contract: {...contract, lifecycle: {cancel: {...contract.lifecycle.cancel, missing: undefined}}}}), /adapter_contract\.lifecycle/);
});
