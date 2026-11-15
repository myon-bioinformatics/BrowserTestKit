import {createRequire} from "node:module";
import {createServer} from "node:http";
import {readFileSync} from "node:fs";
const require = createRequire(import.meta.url);
const {test, expect} = require(String.fromCharCode(64) + "playwright/test");
const fixture = readFileSync(new URL("../../fixtures/page_inspect_scenarios.html", import.meta.url));

test("real Chromium fixture exercises environment, shortcut and HTTP error", async ({browser}, info) => {
  test.skip(info.project.name !== "chromium", "Chromium-only fixture smoke test");
  const server = createServer((req, res) => {
    if (req.url.startsWith("/api/probe")) {
      res.writeHead(503, {"content-type": "text/plain"});
      res.end("failure");
    } else {
      res.writeHead(200, {"content-type": "text/html; charset=utf-8"});
      res.end(fixture);
    }
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const context = await browser.newContext({
    colorScheme: "dark", reducedMotion: "reduce", locale: "ja-JP",
    timezoneId: "Asia/Tokyo", viewport: {width: 390, height: 844}
  });
  try {
    const page = await context.newPage();
    await page.goto("http://127.0.0.1:" + server.address().port + "/scenarios");
    expect(await page.locator("#scenario-display").getAttribute("data-scheme")).toBe("dark");
    expect(await page.locator("#scenario-display").getAttribute("data-motion")).toBe("reduce");
    await page.locator("#scenario-fetch").click();
    await expect(page.locator("#scenario-result")).toHaveAttribute("data-state", "error");
    await page.locator("#scenario-shortcut").click();
    await page.keyboard.press("ControlOrMeta+A");
    await page.keyboard.type("Z");
    expect(await page.locator("#scenario-shortcut").inputValue()).toBe("Z");
  } finally {
    await context.close();
    await new Promise(resolve => server.close(resolve));
  }
});
