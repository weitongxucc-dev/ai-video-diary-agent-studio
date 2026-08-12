/* eslint-disable @typescript-eslint/no-unused-expressions */
async (page) => {
  const viewports = [
    { width: 1440, height: 900 },
    { width: 1180, height: 800 },
    { width: 1024, height: 768 },
  ];
  const results = [];

  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    await page.goto("http://127.0.0.1:3100/ui-preview", {
      waitUntil: "domcontentloaded",
    });
    await page.waitForTimeout(350);

    const initial = await page.evaluate(() => {
      const root = document.documentElement;
      const body = document.body;
      const shown = (element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
      };
      const targetElements = [...document.querySelectorAll("button,a,input,[tabindex]")].filter(shown);
      const undersized = targetElements
        .map((element) => {
          const rect = element.getBoundingClientRect();
          return {
            label: element.getAttribute("aria-label") || element.textContent?.trim() || element.tagName,
            width: Math.round(rect.width),
            height: Math.round(rect.height),
          };
        })
        .filter((target) => target.width < 32 || target.height < 32);
      const importantUndersized = [...document.querySelectorAll(".pixel-button--primary,.pixel-button--danger")]
        .filter(shown)
        .map((element) => {
          const rect = element.getBoundingClientRect();
          return { label: element.textContent?.trim(), width: rect.width, height: rect.height };
        })
        .filter((target) => target.width < 40 || target.height < 40);
      const regionFailures = [...document.querySelectorAll("[data-scroll-region]")]
        .filter(shown)
        .flatMap((element) => {
          const role = element.getAttribute("data-scroll-region");
          const style = getComputedStyle(element);
          const failures = [];
          if (role === "local-x") {
            if (!/auto|scroll/.test(style.overflowX)) failures.push(`${role}: missing horizontal scroll`);
          } else if (element.scrollWidth > element.clientWidth + 1) {
            failures.push(`${role}: unexpected horizontal overflow ${element.scrollWidth}/${element.clientWidth}`);
          }
          if (/hidden|clip/.test(style.overflowX) && element.scrollWidth > element.clientWidth + 1) {
            failures.push(`${role}: inaccessible clipped content`);
          }
          return failures;
        });
      const main = document.querySelector(".shell-content-grid")?.getBoundingClientRect();
      const dock = document.querySelector(".voice-command-console")?.getBoundingClientRect();
      const dockOverlap = Boolean(main && dock && main.bottom > dock.top + 1);
      const disabledChrome = document.querySelectorAll(
        ".system-titlebar :disabled,.system-menubar :disabled,.system-toolbar :disabled",
      ).length;

      return {
        bodyOverflow: body.scrollWidth - body.clientWidth,
        disabledChrome,
        dockOverlap,
        importantUndersized,
        regionFailures,
        rootOverflow: root.scrollWidth - root.clientWidth,
        undersized,
      };
    });

    if (initial.rootOverflow > 0 || initial.bodyOverflow > 0) {
      throw new Error(`${viewport.width}: root horizontal overflow ${JSON.stringify(initial)}`);
    }
    if (initial.undersized.length > 0 || initial.importantUndersized.length > 0) {
      throw new Error(`${viewport.width}: undersized targets ${JSON.stringify(initial)}`);
    }
    if (initial.regionFailures.length > 0 || initial.dockOverlap || initial.disabledChrome > 0) {
      throw new Error(`${viewport.width}: shell contract failed ${JSON.stringify(initial)}`);
    }

    const primary = page.getByTestId("phase-a-primary");
    await primary.focus();
    const focus = await primary.evaluate((element) => {
      const style = getComputedStyle(element);
      return { color: style.outlineColor, style: style.outlineStyle, width: parseFloat(style.outlineWidth) };
    });
    if (focus.style === "none" || focus.width < 2) {
      throw new Error(`${viewport.width}: primary focus is not visibly rendered ${JSON.stringify(focus)}`);
    }

    const rail = page.getByRole("region", { name: "生产阶段轨道" });
    const railMetrics = await rail.evaluate((element) => ({
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
    }));
    if (railMetrics.scrollWidth > railMetrics.clientWidth + 1) {
      const leftControl = page.getByRole("button", { name: "向左滚动生产阶段轨道" });
      const rightControl = page.getByRole("button", { name: "向右滚动生产阶段轨道" });
      if (!(await leftControl.isVisible()) || !(await rightControl.isVisible())) {
        throw new Error(`${viewport.width}: horizontal controls are missing`);
      }
      if (!(await page.getByText("可横向滚动查看更多").isVisible())) {
        throw new Error(`${viewport.width}: horizontal overflow hint is missing`);
      }
      await rail.focus();
      await rail.press("End");
      await page.waitForTimeout(250);
      const keyboardEnd = await rail.evaluate((element) =>
        element.scrollLeft + element.clientWidth >= element.scrollWidth - 1,
      );
      if (!keyboardEnd) {
        throw new Error(`${viewport.width}: End did not reach the horizontal region end`);
      }
      await rail.press("Home");
      await page.waitForTimeout(250);
      if ((await rail.evaluate((element) => element.scrollLeft)) > 1) {
        throw new Error(`${viewport.width}: Home did not return the horizontal region start`);
      }
    }
    const scrollResult = await rail.evaluate((element) => {
      element.scrollLeft = element.scrollWidth;
      const last = element.querySelector('[data-scroll-item="last"]');
      if (!last) return { reachable: false, reason: "missing last item" };
      const viewportRect = element.getBoundingClientRect();
      const lastRect = last.getBoundingClientRect();
      const reachable = lastRect.left >= viewportRect.left - 1 && lastRect.right <= viewportRect.right + 1;
      element.scrollLeft = 0;
      return { reachable, lastRight: lastRect.right, viewportRight: viewportRect.right };
    });
    if (!scrollResult.reachable) {
      throw new Error(`${viewport.width}: last stage is not reachable ${JSON.stringify(scrollResult)}`);
    }

    const dockToggle = page.locator('[aria-controls="voice-command-details"]');
    await dockToggle.click();
    if ((await dockToggle.getAttribute("aria-expanded")) !== "true") {
      throw new Error(`${viewport.width}: command details did not expand`);
    }
    await dockToggle.click();

    if (viewport.width === 1024) {
      const mainWidthBefore = await page.locator(".shell-main-scroll").evaluate(
        (element) => element.getBoundingClientRect().width,
      );
      await page.getByRole("button", { name: "查看详情" }).click();
      const inspector = page.getByRole("complementary", { name: "当前任务依据" });
      await inspector.waitFor({ state: "visible" });
      if (await inspector.getAttribute("aria-modal")) {
        throw new Error("1024: complementary Inspector must remain non-modal");
      }
      const mainWidthAfter = await page.locator(".shell-main-scroll").evaluate(
        (element) => element.getBoundingClientRect().width,
      );
      if (Math.abs(mainWidthBefore - mainWidthAfter) > 1) {
        throw new Error(`1024: inspector drawer shrank main task ${mainWidthBefore}/${mainWidthAfter}`);
      }
      await page.getByRole("button", { name: "关闭任务详情" }).last().focus();
      await page.keyboard.press("Escape");
      if (await page.locator(".inspector-backdrop").isVisible()) {
        throw new Error("1024: inspector backdrop remained after closing the drawer");
      }
      const trigger = page.locator('[aria-controls="agent-inspector"]');
      const focusReturned = await trigger.evaluate(
        (element) => document.activeElement === element,
      );
      if (!focusReturned) {
        throw new Error("1024: inspector focus was not returned to its trigger");
      }
    }

    const filename = `output/playwright/phase-a-preview-${viewport.width}x${viewport.height}.png`;
    await page.screenshot({ path: filename, scale: "css" });
    results.push({ viewport: `${viewport.width}x${viewport.height}`, screenshot: filename, ...initial });
  }

  return results;
}
