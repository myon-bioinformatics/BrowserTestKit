"use strict";
// page_inspect.cjs: btk.py を vendor/py_launch.cjs から動かす Node の入口（Node 26+）。
// 次と同じ動きです: node vendor/py_launch.cjs --env-var BTK_PYTHON --site scripts/btk.py ARGS...
// BTK_PYTHON で Python を選べます。Playwright を読むため -S は付けません。
const path = require("node:path");
const { main } = require(path.join(__dirname, "..", "vendor", "py_launch.cjs"));

if (require.main === module) {
  main(path.join(__dirname, "btk.py"), { envVar: "BTK_PYTHON", noSite: false });
}
