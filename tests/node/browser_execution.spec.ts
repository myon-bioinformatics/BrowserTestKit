import { test, expect } from '@playwright/test';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

let explore, choice;
test.beforeAll(async () => {
  ({ explore, choice } = await new Function('url', 'return import(url)')(
    pathToFileURL(resolve('tools/browser_execution.mjs')).href));
});
const seedFor = (index: number) => {
  for (let seed = 0; seed < 1000; seed++) {
    if (choice(String(seed), '/sample', 0, 5) === index) return String(seed);
  }
  throw new Error('No seed for fixture action');
};

test.beforeEach(async ({ page }) => {
  await page.setContent('<meta name="viewport" content="width=device-width,initial-scale=1"><label>Name <input></label><button onclick="this.textContent=\'pressed\'">Run</button>');
});

test('shared exploration sends real pointer input and returns its receipt', async ({ page }) => {
  const receipt = await explore(page, { route: '/sample', seed: seedFor(4), steps: 1,
    buttonName: /^Run$/, ready: async () => expect(page.getByRole('button')).toHaveText('pressed') });
  expect(receipt.ok).toBe(true);
  expect(receipt.actions[0]).toMatchObject({ kind: 'press', label: 'Run' });
  await expect(page.getByRole('button')).toHaveText('pressed');
});

test('shared exploration fills supplied Unicode values without redefining emoji identity', async ({ page }) => {
  const receipt = await explore(page, { route: '/sample', seed: seedFor(3), steps: 1,
    buttonName: /^Run$/, values: ['日本語👩🏽‍💻'] });
  expect(receipt.actions[0]).toMatchObject({ kind: 'fill', value: '日本語👩🏽‍💻' });
  await expect(page.getByRole('textbox')).toHaveValue('日本語👩🏽‍💻');
});

for (const kind of ['field', 'button']) {
  test(`candidate removed between visibility and state check stays bounded: ${kind}`, async ({ page }) => {
    // Deterministic race: actual Playwright locators and DOM removal, with only
    // visibility observation wrapped to remove the candidate at that boundary.
    const originalFields = page.locator.bind(page);
    const originalButtons = page.getByRole.bind(page);
    const wrap = (locator) => new Proxy(locator, {
      get(target, key) {
        if (key === 'nth') return index => {
          const item = target.nth(index);
          return new Proxy(item, { get(node, method) {
            if (method === 'isVisible') return async () => {
              const visible = await node.isVisible();
              if (visible) await node.evaluate(element => element.remove());
              return visible;
            };
            const value = node[method];
            return typeof value === 'function' ? value.bind(node) : value;
          } });
        };
        const value = target[key];
        return typeof value === 'function' ? value.bind(target) : value;
      },
    });
    const adapter = new Proxy(page, { get(target, key) {
      if (key === 'locator') return selector => kind === 'field' ? wrap(originalFields(selector)) : originalFields(selector);
      if (key === 'getByRole') return (role, options) => kind === 'button'
        ? wrap(originalButtons(role, options)) : originalButtons(role, options);
      const value = target[key];
      return typeof value === 'function' ? value.bind(target) : value;
    } });
    let seed = 0;
    while (choice(String(seed), '/sample', 0, 4) !== 0) seed++;
    const receipt = await explore(adapter, { route: '/sample', seed: String(seed), steps: 1,
      buttonName: /^Run$/ });
    expect(receipt.ok).toBe(true);
    expect(receipt.inventories[0].checks).toContainEqual(expect.objectContaining({
      kind: kind === 'field' ? 'fill' : 'press', outcome: 'unavailable_after_inventory',
    }));
    expect(receipt.actions[0].kind).toBe('tab');
  });
}
