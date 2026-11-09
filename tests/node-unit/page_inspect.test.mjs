// btk.py の Node 入口（vendor/py_launch.cjs の直接呼び出し。専用の .cjs ラッパーはない）を確かめる。ブラウザは使わない。
import test from "node:test";
import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {existsSync, mkdtempSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";

function pythonBin() {
  const candidates = [process.env.BTK_PYTHON, process.env.PYTHON, "python3", "python"].filter(Boolean);
  for (const bin of candidates) {
    const probe = spawnSync(bin, ["-c", "import sys; print(sys.version)"], {encoding: "utf8"});
    if (probe.status === 0) return bin;
  }
  throw new Error("python is required for btk.py");
}

const python = pythonBin();
const env = {...process.env, BTK_PYTHON: python};
const LAUNCH = ["vendor/py_launch.cjs", "--env-var", "BTK_PYTHON", "--site", "tools/btk.py"];
const ENTRIES = [["py_launch", LAUNCH]];

test("help goes through btk.py from the Node entry", () => {
  for (const [label, prefix] of ENTRIES) {
    const help = spawnSync(process.execPath, [...prefix, "--help"], {encoding: "utf8", env});
    assert.equal(help.status, 0, `${label}: ${help.stderr}`);
    assert.match(help.stdout, /observe/);
  }
});

test("locate through Node matches python -S tools/btk.py", () => {
  const directory = mkdtempSync(join(tmpdir(), "page-inspect-"));
  const snapshot = join(directory, "observe.json");
  const body = {
    schema: "btk-page-observe/1", ok: true, elements: [{
      handle: "h:0", index: 0, address: "id:save-draft", tag: "button", role: "button", name: "Save draft",
      text: "Save draft", id: "save-draft", testid: "", visible: true, enabled: true, locators: [],
    }],
  };
  writeFileSync(snapshot, `${JSON.stringify(body)}\n`);
  const args = ["locate", snapshot, "--query", "Save draft", "--unique", "--json"];
  const py = spawnSync(python, ["-S", "tools/btk.py", ...args], {encoding: "utf8"});
  assert.equal(py.status, 0, py.stderr);
  for (const [label, prefix] of ENTRIES) {
    const node = spawnSync(process.execPath, [...prefix, ...args], {encoding: "utf8", env});
    assert.equal(node.status, 0, `${label}: ${node.stderr}`);
    assert.deepEqual(JSON.parse(node.stdout), JSON.parse(py.stdout));
  }
});

test("BTK_PYTHON is strict: a missing interpreter exits 2 and names the variable", () => {
  const bad = {...process.env, BTK_PYTHON: join(tmpdir(), "no-such-python-btk")};
  for (const [label, prefix] of ENTRIES) {
    const done = spawnSync(process.execPath, [...prefix, "--help"], {encoding: "utf8", env: bad});
    assert.equal(done.status, 2, label);
    assert.match(done.stderr, /BTK_PYTHON/, label);
  }
});

test("exit codes from btk.py pass through unchanged", () => {
  for (const [label, prefix] of ENTRIES) {
    const done = spawnSync(process.execPath, [...prefix, "locate", "/nonexistent/observe.json", "--query", "x", "--json"], {encoding: "utf8", env});
    assert.equal(done.status, 2, `${label}: ${done.stdout}${done.stderr}`);
  }
});

test("there is no per-tool .cjs wrapper next to btk.py", () => {
  assert.equal(existsSync("tools/page_inspect.cjs"), false);
  assert.equal(existsSync("tools/btk.cjs"), false);
});
