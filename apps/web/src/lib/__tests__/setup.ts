import "@testing-library/jest-dom/vitest";

// JSDOM has no Web Animations engine. Base UI ScrollArea queries the viewport
// after mount, so expose the same empty animation list for every test element.
if (typeof Element.prototype.getAnimations === "undefined") {
  Object.defineProperty(Element.prototype, "getAnimations", {
    configurable: true,
    writable: true,
    value: () => [],
  });
}

if (typeof globalThis.ResizeObserver === "undefined") {
  class TestResizeObserver implements ResizeObserver {
    constructor(_callback: ResizeObserverCallback) {}
    observe(_target: Element, _options?: ResizeObserverOptions) {}
    unobserve(_target: Element) {}
    disconnect() {}
  }
  Object.defineProperty(globalThis, "ResizeObserver", { configurable: true, value: TestResizeObserver });
}
