"use strict";

const crypto = require("node:crypto");
const path = require("node:path");

function unitLen(text) {
  return String(text).length;
}

function lcsLength(left, right) {
  const first = String(left).toLowerCase();
  const second = String(right).toLowerCase();
  if (!first.length || !second.length) return 0;
  let previous = new Array(second.length + 1).fill(0);
  let best = 0;
  for (let i = 0; i < first.length; i += 1) {
    const unit = first.charCodeAt(i);
    const current = new Array(second.length + 1).fill(0);
    for (let j = 0; j < second.length; j += 1) {
      if (unit === second.charCodeAt(j)) {
        current[j + 1] = previous[j] + 1;
        if (current[j + 1] > best) best = current[j + 1];
      }
    }
    previous = current;
  }
  return best;
}

function round6(value) {
  return Math.floor(value * 1000000 + 0.5) / 1000000;
}

function similarity(left, right) {
  if (!left || !right) return 0;
  const first = String(left).toLowerCase();
  const second = String(right).toLowerCase();
  return round6(lcsLength(first, second) / Math.min(unitLen(first), unitLen(second)));
}

function unpairedSurrogate(text) {
  for (let index = 0; index < text.length; index += 1) {
    const unit = text.charCodeAt(index);
    if (unit >= 0xD800 && unit <= 0xDBFF) {
      const next = text.charCodeAt(index + 1);
      if (next >= 0xDC00 && next <= 0xDFFF) {
        index += 1;
        continue;
      }
      return true;
    }
    if (unit >= 0xDC00 && unit <= 0xDFFF) return true;
  }
  return false;
}

function storageFingerprint(value) {
  if (typeof value !== "string") throw new Error("storage value must be a string");
  let raw;
  let encoding;
  if (unpairedSurrogate(value)) {
    raw = Buffer.alloc(1 + value.length * 2);
    raw[0] = 0xff;
    for (let index = 0; index < value.length; index += 1) raw.writeUInt16LE(value.charCodeAt(index), 1 + index * 2);
    encoding = "utf16le-unpaired";
  } else {
    raw = Buffer.from(value, "utf8");
    encoding = "utf8";
  }
  return {
    fingerprint_version: 2,
    encoding,
    sha256: crypto.createHash("sha256").update(raw).digest("hex"),
  };
}

const COMPLETE = new Set(["complete", "complete_within_window"]);

function evidenceRelation(left, right) {
  if (!left || typeof left !== "object" || !right || typeof right !== "object") return "unknown";
  if (!COMPLETE.has(left.coverage) || !COMPLETE.has(right.coverage)) return "unknown";
  if (typeof left.digest !== "string" || typeof right.digest !== "string" || !left.digest || !right.digest) return "unknown";
  return left.digest === right.digest ? "same" : "different";
}

function loopbackTarget(target) {
  if (typeof target !== "string" || !(target.startsWith("http://") || target.startsWith("https://"))) {
    throw new Error("target must be an http or https loopback URL");
  }
  let url;
  try {
    url = new URL(target);
  } catch (error) {
    throw new Error("target is not a loopback URL");
  }
  if (url.username || url.password) throw new Error("URL must not include credentials");
  const host = url.hostname;
  if (host !== "127.0.0.1" && host !== "[::1]" && host !== "::1") {
    throw new Error("target host must be a loopback address");
  }
  if (!explicitPort(target)) throw new Error("target needs an explicit port");
  return target;
}

function explicitPort(target) {
  const rest = target.slice(target.indexOf("://") + 3);
  if (rest.startsWith("[")) {
    const end = rest.indexOf("]");
    if (end < 0) return false;
    return /^:[0-9]+/.test(rest.slice(end + 1));
  }
  const cut = rest.split(/[/?#]/, 1)[0];
  return /:[0-9]+$/.test(cut);
}

function helpText() {
  return `btk.cjs — observe a loopback page, rank locators, and run declared steps.

Commands delegate to btk.py through vendor/py_launch.cjs (Node 26+).
offline: node scripts/btk.cjs locate SNAPSHOT --query TEXT --json
live:    node scripts/btk.cjs observe http://127.0.0.1:PORT/page --json
steps:   node scripts/btk.cjs observe http://127.0.0.1:PORT/page --viewport 390x844 --step focus:id:search --step type:id:search=env:SEARCH --out obs.json --out-dir DIR
`;
}

function delegate(argv) {
  const launch = require(path.join(__dirname, "..", "vendor", "py_launch.cjs"));
  try {
    const result = launch.run(path.join(__dirname, "btk.py"), argv, {
      timeout: 180000,
      maxBuffer: 16 * 1024 * 1024,
    });
    process.stdout.write(result.stdout);
    return 0;
  } catch (error) {
    if (error.stdout) process.stdout.write(error.stdout);
    if (error.stderr) process.stderr.write(error.stderr);
    if (typeof error.status === "number") return error.status;
    process.stderr.write(`${error.message || error}\n`);
    return 2;
  }
}

function main(argv) {
  if (argv.includes("-h")) {
    process.stderr.write("error: short options are not accepted\nhint: pass --help\n");
    return 2;
  }
  if (argv.length === 0 || argv[0] === "--help") {
    process.stdout.write(helpText());
    return argv.length === 0 ? 2 : 0;
  }
  return delegate(argv);
}

module.exports = {
  similarity,
  lcsLength,
  storageFingerprint,
  evidenceRelation,
  loopbackTarget,
  main,
};

if (require.main === module) {
  process.exit(main(process.argv.slice(2)));
}
