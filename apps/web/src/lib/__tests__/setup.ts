import "@testing-library/jest-dom/vitest";

if (typeof globalThis.ResizeObserver === "undefined") {
  class TestResizeObserver implements ResizeObserver {
    constructor(_callback: ResizeObserverCallback) {}
    observe(_target: Element, _options?: ResizeObserverOptions) {}
    unobserve(_target: Element) {}
    disconnect() {}
  }
  Object.defineProperty(globalThis, "ResizeObserver", { configurable: true, value: TestResizeObserver });
}
