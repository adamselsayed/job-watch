// Field Notes theme + three dashboard-style top sections, screenshotted with live data.
const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const b = await chromium.launch();
  fs.rmSync('shots', { recursive: true, force: true }); fs.mkdirSync('shots', { recursive: true });
  const css = fs.readFileSync('themes/field-notes.css', 'utf8') + fs.readFileSync('mock/top.css', 'utf8');
  const js = fs.readFileSync('mock/top.js', 'utf8');
  for (const v of ['a', 'b', 'c']) {
    for (const [vw, vh, tag, clip] of [[1440, 1000, 'desktop', 640], [390, 844, 'phone', 0]]) {
      const pg = await b.newPage({ viewport: { width: vw, height: vh }, deviceScaleFactor: 2, colorScheme: 'light' });
      await pg.goto('http://localhost:8000/index.html', { waitUntil: 'networkidle' });
      await pg.waitForFunction(() => document.querySelector('#tNew') && document.querySelector('#tNew').textContent !== '–');
      await pg.addStyleTag({ content: css }); await pg.addScriptTag({ content: js });
      await pg.evaluate(v => window.__buildTop(v), v);
      await pg.evaluate(() => document.fonts.ready); await pg.waitForTimeout(1000);
      await pg.screenshot({ path: `shots/top-${v}-${tag}.png`, ...(clip ? { clip: { x: 0, y: 0, width: vw, height: clip } } : { fullPage: false }) });
      await pg.close();
    }
  }
  await b.close();
})();
