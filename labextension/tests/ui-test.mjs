import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true
});
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
try {
  await page.goto('http://127.0.0.1:8893/lab/workspaces/daslang-highlight-check/tree/highlighting-demo.ipynb?token=daslang-highlight-test', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(5000);
  await page.screenshot({path: process.env.DAS_HIGHLIGHT_SCREENSHOT || 'highlighting-check.png',fullPage:true});
  await page.locator('.jp-Notebook .jp-CodeCell .cm-content:visible').first().waitFor({ timeout: 60000 });
  await page.waitForFunction(() => [...document.querySelectorAll('.jp-Notebook .jp-CodeCell .cm-content span')]
    .some(el => el.textContent === 'struct' && getComputedStyle(el).color !== 'rgb(0, 0, 0)'), { timeout: 30000 });
  const spans = await page.locator('.jp-Notebook .jp-CodeCell .cm-content:visible').first().evaluate(el => {
    return [...el.querySelectorAll('span')].map(span => ({
      text: span.textContent, color: getComputedStyle(span).color,
      fontStyle: getComputedStyle(span).fontStyle, cls: span.className
    }));
  });
  for (const text of ['struct', 'Stats', 'def', 'int', '0x2a']) {
    assert.ok(spans.some(span => span.text === text), `Missing styled span: ${text}`);
  }
  const keyword = spans.find(span => span.text === 'struct');
  const number = spans.find(span => span.text === '0x2a');
  assert.notEqual(keyword.color, number.color, 'Keywords and numbers have distinct theme styles');
  assert.ok(spans.some(span => span.text?.includes('Outer comment')), 'Multiline comments are highlighted');
  assert.ok(spans.some(span => span.text?.includes('Привет')), 'Strings are highlighted');
  await page.screenshot({ path: process.env.DAS_HIGHLIGHT_SCREENSHOT || 'highlighting-check.png', fullPage: true });
  const fixture = 'tests/highlighting-probe.das';
  const content = '/* file comment */\nstruct Probe {value : int}\ndef answer() : int {return 42}';
  const created = await page.request.put('http://127.0.0.1:8893/api/contents/' + fixture + '?token=daslang-highlight-test',
    { data: { type: 'file', format: 'text', content } });
  assert.ok(created.ok(), await created.text());
  let originalTheme;
  try {
    await page.goto('http://127.0.0.1:8893/lab/workspaces/daslang-highlight-check/tree/' + fixture + '?token=daslang-highlight-test', { waitUntil: 'domcontentloaded' });
    await page.locator('.jp-FileEditor .cm-content:visible').first().waitFor({timeout:30000});
    await page.waitForFunction(() => [...document.querySelectorAll('.jp-FileEditor .cm-content span')]
      .some(span => span.textContent === 'struct'));
    console.log('.das file editor: language selected and highlighted.');
    originalTheme = await page.locator('body').getAttribute('data-jp-theme-name');
    await page.getByRole('menuitem', {name:'Settings', exact:true}).click();
    await page.locator('.lm-Menu').getByText('Theme', {exact:true}).hover();
    await page.locator('.lm-Menu').getByText('JupyterLab Dark', {exact:true}).click();
    await page.waitForFunction(() => document.body.getAttribute('data-jp-theme-name') === 'JupyterLab Dark');
    const typeColor = await page.locator('.jp-FileEditor .cm-content span').evaluateAll(spans =>
      getComputedStyle(spans.find(span => span.textContent === 'int')).color);
    assert.notEqual(typeColor, 'rgb(0, 0, 0)');
    console.log('Dark theme: types retain theme-aware highlighting.');

  } finally {
    if (originalTheme && await page.locator('body').getAttribute('data-jp-theme-name') !== originalTheme) {
      await page.getByRole('menuitem', {name:'Settings', exact:true}).click();
      await page.locator('.lm-Menu').getByText('Theme', {exact:true}).hover();
      await page.locator('.lm-Menu').getByText(originalTheme, {exact:true}).click();
      await page.waitForFunction(theme => document.body.getAttribute('data-jp-theme-name') === theme, originalTheme);
    }
    await page.request.delete('http://127.0.0.1:8893/api/contents/' + fixture + '?token=daslang-highlight-test');
  }
  fs.writeFileSync(process.env.DAS_HIGHLIGHT_REPORT || 'highlighting-check.json', JSON.stringify({ spans, errors }, null, 2));
  assert.ok(!errors.some(error => /jupyterlab-daslang|StreamLanguage|codemirror/i.test(error)), JSON.stringify(errors));
  console.log('JupyterLab notebook: daslang styles, comments, types, numbers and strings verified.');
} finally {
  await page.screenshot({path:(process.env.DAS_HIGHLIGHT_SCREENSHOT || 'highlighting-check.png').replace('.png','-last.png'),fullPage:true});
  await browser.close();
}
