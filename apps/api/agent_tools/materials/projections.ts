import type {MaterialAsset} from './contracts.ts';

// API diagnostics can contain local paths; neither fields nor embedded paths go to Pi.
export function materialText(text: string): string {
  return text.replace(/(^|[\s("'：])(?:[A-Za-z]:[\\/]|\/)[^\s"'<>，。；]+/g, "$1[本地路径已省略]");
}
export function fileName(asset: MaterialAsset): string {
  return asset.file_name?.split(/[\\/]/).at(-1) ?? "";
}

// Python isoformat and Pydantic JSON differ only in the UTC suffix here.
// Preserve every fractional digit; Date milliseconds are not a version key.
export function utcVersion(version: string): string {
  return version.replace(/\+00:00$/, "Z");
}
