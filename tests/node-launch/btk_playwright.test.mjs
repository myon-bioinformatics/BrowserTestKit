// observe / act / scenario を Node から vendor/py_launch.cjs 経由で動かし、本物の Playwright（Chromium）で確かめる。
// BTK_PYTHON に Playwright 入りの Python を渡す。BTK_REQUIRE_PLAYWRIGHT=1（CI）のときは、Playwright がなければ失敗にする。
import test from "node:test";
import assert from "node:assert/strict";
import {spawn, spawnSync} from "node:child_process";
import {createServer} from "node:http";
import {readFileSync} from "node:fs";

const PAGE = readFileSync(new URL("../../fixtures/page_inspect.html", import.meta.url));
const SCENARIOS = readFileSync(new URL("../../fixtures/page_inspect_scenarios.html", import.meta.url));
const python = process.env.BTK_PYTHON || process.env.PYTHON || "python3";
const required = process.env.BTK_REQUIRE_PLAYWRIGHT === "1";
const probe = spawnSync(python, ["-c", "import playwright"], {encoding: "utf8"});
const skip = probe.status !== 0 && !required ? "BTK_PYTHON に Playwright が入っていません" : false;
const ENTRIES = [
  ["py_launch", ["vendor/py_launch.cjs", "--env-var", "BTK_PYTHON", "--site", "scripts/btk.py"]],
];

function listen() {
  const server = createServer((request, response) => {
    if (request.url.startsWith("/api/probe")) {
      response.writeHead(200, {"content-type": "text/plain"});
      response.end("ok");
    } else if (request.url.startsWith("/scenario.txt")) {
      response.writeHead(200, {"content-type": "text/plain", "content-disposition": 'attachment; filename="scenario.txt"'});
      response.end("fixture");
    } else {
      response.writeHead(200, {"content-type": "text/html; charset=utf-8"});
      response.end(request.url.startsWith("/scenarios") ? SCENARIOS : PAGE);
    }
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

function btk(prefix, args) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [...prefix, ...args], {env: {...process.env, BTK_PYTHON: python}});
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("close", (status) => resolve({status, stdout, stderr}));
  });
}

test("BTK_PYTHON has Playwright when the CI requires it", {skip}, () => {
  assert.equal(probe.status, 0, `BTK_PYTHON=${python} cannot import playwright: ${probe.stderr}`);
});

for (const [label, prefix] of ENTRIES) {
  test(`${label}: observe, act, and scenario run in Chromium`, {skip, timeout: 240000}, async () => {
    const server = await listen();
    try {
      const base = `http://127.0.0.1:${server.address().port}`;

      const observed = await btk(prefix, ["observe", `${base}/page`, "--browser", "chromium", "--json"]);
      assert.equal(observed.status, 0, observed.stderr);
      const payload = JSON.parse(observed.stdout);
      assert.equal(payload.schema, "btk-page-observe/1");
      assert.equal(payload.ok, true);
      assert.equal(payload.limits.page_js_called, false);
      assert.ok(payload.elements.some((element) => element.address === "id:save-draft"));
      assert.ok(!observed.stdout.includes("secret-value"));

      const acted = await btk(prefix, ["act", `${base}/page`, "--browser", "chromium", "--action", "click", "--locator", "id:save-draft", "--json"]);
      assert.equal(acted.status, 0, acted.stderr);
      const action = JSON.parse(acted.stdout);
      assert.equal(action.ok, true);

      const scenario = await btk(prefix, ["scenario", `${base}/scenarios?token=redact-me`, "--browser", "chromium", "--case", "shortcut", "--json"]);
      assert.equal(scenario.status, 0, scenario.stderr);
      const evidence = JSON.parse(scenario.stdout);
      assert.equal(evidence.schema, "btk-page-observe/1");
      assert.equal(evidence.sections.scenario.select_all_replaced, true);
      assert.ok(!scenario.stdout.includes("redact-me"));
    } finally {
      await new Promise((resolve) => server.close(resolve));
    }
  });
}
