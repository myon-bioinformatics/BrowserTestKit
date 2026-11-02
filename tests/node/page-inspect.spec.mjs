import {createRequire} from "node:module";
import {spawn, spawnSync} from "node:child_process";
import {createServer} from "node:http";
import {existsSync, readFileSync} from "node:fs";
import {resolve} from "node:path";

const require = createRequire(import.meta.url);
const {test, expect} = require(String.fromCharCode(64) + "playwright/test");
const fixture = readFileSync(new URL("../../fixtures/page_inspect.html", import.meta.url));

function listen() {
  const server = createServer((request, response) => {
    response.writeHead(200, {"content-type": "text/html; charset=utf-8"});
    response.end(fixture);
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

test("the Node CLI observes Chromium through the Python bridge", async ({}, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "one CLI launch is enough; Firefox and WebKit have no CDP session");
  test.skip(Number(process.versions.node.split(".")[0]) < 26, "py_launch requires Node 26+");
  const python = existsSync(".venv/bin/python") ? resolve(".venv/bin/python") : (process.env.PYTHON || "python3");
  const probe = spawnSync(python, ["-c", "import playwright"], {encoding: "utf8"});
  test.skip(probe.status !== 0, "observe through py_launch needs the Python browser extra");
  test.setTimeout(90000);
  const server = await listen();
  try {
    const port = server.address().port;
    const run = await new Promise((resolvePromise) => {
      const child = spawn(process.execPath, [
        "scripts/btk.cjs", "observe", `http://127.0.0.1:${port}/page`, "--browser", "chromium", "--json",
      ], {env: {...process.env, PYTHON: python}});
      let stdout = "";
      let stderr = "";
      child.stdout.setEncoding("utf8");
      child.stderr.setEncoding("utf8");
      child.stdout.on("data", (chunk) => { stdout += chunk; });
      child.stderr.on("data", (chunk) => { stderr += chunk; });
      child.on("close", (status) => resolvePromise({status, stdout, stderr}));
    });
    expect(run.status, run.stderr).toBe(0);
    const payload = JSON.parse(run.stdout);
    expect(payload.schema).toBe("btk-page-observe/1");
    expect(payload.limits.page_js_called).toBe(false);
    expect(payload.limits.cdp_methods).not.toContain("Runtime.evaluate");
    expect(payload.elements.some((element) => element.address === "id:save-draft")).toBe(true);
    expect(run.stdout).not.toContain("secret-value");
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
