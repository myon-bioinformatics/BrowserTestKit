"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {spawnSync} = require("node:child_process");
const {test} = require("node:test");
const ROOT = path.join(__dirname, "../..");
const PYTHON = process.env.PY_LAUNCH_TEST_PYTHON || (process.platform === "win32" ? "python" : "python3");

// Behavioral regressions for the source-identical shared parent launcher.
const LAUNCHER = path.join(ROOT, "vendor/py_launch.cjs");
const {spawn} = require("node:child_process");

test("launcher keeps Node 26 minimum without an upper bound", () => {
  const {assertNodeVersion} = require(LAUNCHER);
  for (const version of ["26.0.0", "99.0.0"]) assertNodeVersion(version);
  assert.throws(() => assertNodeVersion("24.0.0"),
    (error) => error.code === "ENODEVERSION" && /Node 26\+/.test(error.message));
});

test("launcher main reports missing interpreter as one JSON error", () => {
  const missing = path.join(ROOT, "missing-python-for-launcher-test");
  const result = spawnSync(process.execPath, ["-e",
    `require(${JSON.stringify(LAUNCHER)}).main("unused.py", {python: ${JSON.stringify(missing)}});`],
  {encoding: "utf8", timeout: 10000, env: {...process.env, PY_LAUNCH_ERROR_FORMAT: "json"}});
  assert.ifError(result.error);
  assert.equal(result.status, 2);
  assert.equal(result.stdout, "");
  const error = JSON.parse(result.stderr);
  assert.equal(error.ok, false);
  assert.equal(error.code, "ENOINTERPRETER");
  assert.equal(error.exit_code, 2);
  assert.match(error.hint, /Python 3\.9\+/);
});

function withSiteHook(t, mode, flags) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vendored-launcher-site-"));
  t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  const marker = path.join(dir, "hook-ran");
  const script = path.join(dir, "echo.py");
  fs.writeFileSync(path.join(dir, "sitecustomize.py"),
    'import os\nwith open(os.environ["PY_LAUNCH_SITE_MARKER"], "w") as out:\n    out.write("ran")\nraise SystemExit(42)\n');
  fs.writeFileSync(script, 'import sys\nprint(" ".join(sys.argv[1:]))\n');
  const opts = JSON.stringify({python: PYTHON, ...flags});
  const calls = {
    resolve: `launcher.resolvePython(${opts}); console.log("safe");`,
    run: `process.stdout.write(launcher.run(${JSON.stringify(script)}, ["safe"], ${opts}).stdout);`,
    main: `launcher.main(${JSON.stringify(script)}, {...${opts}, argv: ["safe"]});`,
  };
  const result = spawnSync(process.execPath,
    ["-e", `const launcher = require(${JSON.stringify(LAUNCHER)}); ${calls[mode]}`],
    {encoding: "utf8", timeout: 10000, killSignal: "SIGKILL",
      env: {...process.env, PYTHONPATH: dir, PY_LAUNCH_SITE_MARKER: marker}});
  assert.ifError(result.error);
  return {result, hookRan: fs.existsSync(marker)};
}

test("interpreter discovery leaves default site startup enabled", (t) => {
  const {result, hookRan} = withSiteHook(t, "resolve", {});
  assert.equal(hookRan, true);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /not suitable/);
});

for (const flags of [{noSite: true}, {isolated: true}, {noSite: true, isolated: true}]) {
  for (const mode of ["resolve", "run", "main"]) {
    test(`${mode} suppresses sitecustomize during discovery and execution: ${JSON.stringify(flags)}`, (t) => {
      const {result, hookRan} = withSiteHook(t, mode, flags);
      assert.equal(hookRan, false, "the version probe must also suppress sitecustomize");
      assert.equal(result.status, 0, result.stderr);
      assert.equal(result.stdout.trim(), "safe");
    });
  }
}

test("launcher preserves interpreter prefix before probe isolation flags", (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vendored-launcher-prefix-"));
  t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  const probe = path.join(dir, "probe.cjs");
  fs.writeFileSync(probe, `require("node:assert/strict").deepEqual(process.argv.slice(2, -1), ["-S", "-I", "-c"]); console.log("3.12");`);
  const {resolvePython} = require(LAUNCHER);
  const result = resolvePython({candidates: [{cmd: process.execPath, prefix: [probe]}], noSite: true, isolated: true});
  assert.equal(result.cmd, process.execPath);
});

for (const interruptFirst of [false, true]) {
  for (const jsonErrors of [false, true]) {
    test(`launcher forwards SIGTERM${interruptFirst ? " after ignored SIGINT" : ""} (${jsonErrors ? "JSON" : "text"})`,
      {skip: process.platform === "win32", timeout: 15000}, async (t) => {
        const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vendored-launcher-signal-"));
        t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
        const script = path.join(dir, "ignore_sigint.py");
        fs.writeFileSync(script, 'import signal, time\ndef ignore_interrupt(_signal, _frame):\n    print("IGNORED SIGINT", flush=True)\nsignal.signal(signal.SIGINT, ignore_interrupt)\nprint("READY", flush=True)\nwhile True:\n    time.sleep(60)\n');
        const opts = {python: PYTHON, jsonErrors, noSite: true};
        const child = spawn(process.execPath,
          ["-e", `require(${JSON.stringify(LAUNCHER)}).main(${JSON.stringify(script)}, ${JSON.stringify(opts)});`],
          {detached: true, stdio: ["ignore", "pipe", "pipe"], env: {...process.env, PY_LAUNCH_ERROR_FORMAT: ""}});
        let stdout = "", stderr = "", sentFirst = false, sentTerm = false, timedOut = false, spawnError;
        const killGroup = () => {
          if (!child.pid) return;
          try { process.kill(-child.pid, "SIGKILL"); }
          catch (error) { if (error.code !== "ESRCH") throw error; }
        };
        const closed = new Promise((resolve) => child.once("close", (code, signal) => resolve({code, signal})));
        const timer = setTimeout(() => { timedOut = true; killGroup(); }, 7500);
        child.on("error", (error) => { spawnError = error; });
        child.stderr.on("data", (chunk) => { stderr += chunk; });
        child.stdout.on("data", (chunk) => {
          stdout += chunk;
          if (!sentFirst && stdout.includes("READY\n")) {
            sentFirst = true;
            if (interruptFirst) child.kill("SIGINT");
            else { sentTerm = true; child.kill("SIGTERM"); }
          }
          if (interruptFirst && !sentTerm && stdout.includes("IGNORED SIGINT\n")) {
            sentTerm = true;
            child.kill("SIGTERM");
          }
        });
        try {
          const {code, signal} = await closed;
          assert.ifError(spawnError);
          assert.equal(timedOut, false, `launcher did not terminate: ${stdout}; ${stderr}`);
          assert.equal(sentTerm, true);
          assert.equal(code, 2);
          assert.equal(signal, null);
          if (jsonErrors) {
            const payload = JSON.parse(stderr);
            assert.equal(payload.code, "ESIGNAL");
            assert.equal(payload.exit_code, 2);
            assert.match(payload.message, /SIGTERM/);
          } else assert.match(stderr, /terminated by signal SIGTERM/);
        } finally { clearTimeout(timer); killGroup(); }
      });
  }
}
