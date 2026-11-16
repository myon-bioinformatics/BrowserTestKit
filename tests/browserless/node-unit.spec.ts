import {test, expect} from '@playwright/test';
import {spawnSync} from 'node:child_process';

// One browserless Playwright case per node:test file, so each lands in this lane's JUnit.
// tests/node-unit/btk_playwright.test.mjs launches Chromium and runs in the python-browser job instead.
const suites: [title: string, file: string, plain?: boolean][] = [
  ['platform fixture state machines and Node runtime contract', 'page_inspect_scenario_fixture.test.mjs'],
  ['Node page-inspect unit tests and Python parity', 'page_inspect.test.mjs'],
  // minimatch is overridden to ^11 (GHSA-g3hv-75wg-m249); see docs/minimatch-override.md.
  ['ExcelJS xlsx, streaming and CSV under the minimatch override', 'exceljs_minimatch.test.cjs'],
];

for (const [title, file, plain] of suites) {
  test(title, () => {
    test.setTimeout(120000);
    const env = {...process.env};
    if (plain) { delete env.FORCE_COLOR; delete env.NO_COLOR; }
    const run = spawnSync(process.execPath, ['--test', `tests/node-unit/${file}`], {
      env, encoding: 'utf8', timeout: 110000, maxBuffer: 8 * 1024 * 1024,
    });
    expect(run.status, `${run.error ?? ''}\n${run.stdout}\n${run.stderr}`).toBe(0);
  });
}
