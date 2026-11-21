import test from "node:test";
import assert from "node:assert/strict";
import {dirname, join} from "node:path";
import {fileURLToPath} from "node:url";
import {loadSkillManifests} from "../skills/manifest.mjs";

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
  assert.equal(manifest.capabilities.find(item => item.id === "probe").status, "catalog_only");
  assert.equal(manifest.capabilities.find(item => item.id === "scenes").status, "planned");
  assert.match(manifest.capabilities.find(item => item.id === "caption").unavailable_reason, /字体/);
  assert.match(await (await import("node:fs/promises")).readFile(manifest.skillPath, "utf8"), /不可用行为/);
});
