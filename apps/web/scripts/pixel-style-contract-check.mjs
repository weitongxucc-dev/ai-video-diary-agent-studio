import { readFile } from "node:fs/promises";
import { globSync } from "node:fs";

const emojiPattern =
  /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u;

const tsxFiles = globSync("src/**/*.{ts,tsx}", {
  cwd: new URL("../", import.meta.url),
});

const sourceByFile = new Map();
const css = await readFile(
  new URL("../src/app/globals.css", import.meta.url),
  "utf8",
);

const failures = [];

for (const file of tsxFiles) {
  const source = await readFile(new URL(`../${file}`, import.meta.url), "utf8");
  sourceByFile.set(file, source);
  if (emojiPattern.test(source)) {
    failures.push(`${file} contains emoji characters; use PixelIcon assets instead.`);
  }
}

if (css.includes("linear-gradient(")) {
  failures.push("globals.css uses gradients; pixel UI should use solid block colors.");
}

const terminalPalette = [
  "#020403",
  "#030503",
  "#050705",
  "#080b08",
  "#090d09",
  "#101610",
  "#36d85f",
  "#39db64",
];

const terminalPaletteHits = terminalPalette.filter((color) => css.includes(color));
if (terminalPaletteHits.length > 0) {
  failures.push(
    `globals.css still uses terminal-style black/green colors: ${terminalPaletteHits.join(", ")}. Use Win95 panel colors instead.`,
  );
}

const excessiveRadii = [...css.matchAll(/border-radius:\s*(\d+)px/g)]
  .map((match) => Number(match[1]))
  .filter((value) => value > 4);

if (excessiveRadii.length > 0) {
  failures.push(
    `globals.css has border radii above 4px: ${[...new Set(excessiveRadii)].join(", ")}`,
  );
}

if (!css.includes("--pixel-shadow")) {
  failures.push("globals.css must define a hard --pixel-shadow token.");
}

if (!css.includes(".pixel-sprite")) {
  failures.push("globals.css must define reusable .pixel-sprite icons.");
}

for (const required of [
  ".pixel-icon-slot",
  "overflow: hidden",
  ".pixel-icon-slot .pixel-sprite",
]) {
  if (!css.includes(required)) {
    failures.push(`globals.css must define a clipped small icon slot with ${required}.`);
  }
}

for (const [file, required] of [
  ["src/components/EpisodeProgress.tsx", "pixel-icon-slot"],
  ["src/components/PixelAgentPanel.tsx", "pixel-icon-slot"],
  ["src/app/topics/page.tsx", "pixel-icon-slot"],
  ["src/app/edit-plan/page.tsx", "pixel-icon-slot"],
]) {
  if (!sourceByFile.get(file)?.includes(required)) {
    failures.push(`${file} must wrap small PixelIcon instances in .pixel-icon-slot.`);
  }
}

if (!css.includes("--font-pixel")) {
  failures.push("globals.css must define a --font-pixel token.");
}

if (!css.includes("font-family: var(--font-pixel)")) {
  failures.push("body must use the --font-pixel token.");
}

if (!css.includes("-webkit-font-smoothing: none") || !css.includes("font-smooth: never")) {
  failures.push("pixel font rendering must disable font smoothing.");
}

const oversizedFontSizes = [...css.matchAll(/font-size:\s*(\d+)px/g)]
  .map((match) => Number(match[1]))
  .filter((value) => value > 28);

if (oversizedFontSizes.length > 0) {
  failures.push(
    `globals.css has font sizes above 28px: ${[...new Set(oversizedFontSizes)].join(", ")}`,
  );
}

const oversizedPadding = [...css.matchAll(/padding:\s*(\d+)px/g)]
  .map((match) => Number(match[1]))
  .filter((value) => value > 18);

if (oversizedPadding.length > 0) {
  failures.push(
    `globals.css has single-value padding above 18px: ${[...new Set(oversizedPadding)].join(", ")}`,
  );
}

if (css.includes("grid-template-columns: 236px")) {
  failures.push("sidebar column is too wide for compact pixel UI.");
}

if (css.includes("366px")) {
  failures.push("right-side panel width is too large for compact pixel UI.");
}

if (failures.length > 0) {
  console.error(failures.map((failure) => `- ${failure}`).join("\n"));
  process.exit(1);
}

console.log("pixel style contract checks passed");
