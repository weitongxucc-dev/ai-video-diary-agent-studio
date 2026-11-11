/** Display abbreviation only. Keep the original name for identity and accessible text. */
export function compactObjectName(name: string, limit = 22): string {
  const letters = Array.from(name);
  if (letters.length <= limit) return name;
  const prefix = Math.min(6, Math.floor(limit / 3));
  return `${letters.slice(0, prefix).join("")}…${letters.slice(-(limit - prefix - 1)).join("")}`;
}
