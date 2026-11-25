export const APPEARANCE_PALETTE_KEY = "ai-video-diary-palette";
export const DEFAULT_PALETTE = "blue-orange";
export const WARM_PALETTE = "warm-orange";

export type AppearancePalette = typeof DEFAULT_PALETTE | typeof WARM_PALETTE;

export function readAppearancePalette(): AppearancePalette {
  return window.localStorage.getItem(APPEARANCE_PALETTE_KEY) === WARM_PALETTE
    ? WARM_PALETTE
    : DEFAULT_PALETTE;
}

export function setAppearancePalette(palette: AppearancePalette): void {
  if (palette === DEFAULT_PALETTE) {
    window.localStorage.removeItem(APPEARANCE_PALETTE_KEY);
  } else {
    window.localStorage.setItem(APPEARANCE_PALETTE_KEY, palette);
  }
  document.documentElement.dataset.palette = palette;
}
