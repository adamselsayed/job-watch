// Renders the dashboard with each theme in themes/ and saves screenshots to shots/.
const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const b = await chromium.launch();
  fs.mkdirSync('shots', { recursive: true });
  const themes = [null, ...fs.readdirSync('themes').filter(f => f.endsWith('.css')).sort()];
  for (const t of themes) {
    for (const [vw, vh, tag] of [[1440, 1000, 'desktop'], [390, 844, 'phone']]) {
      const pg = await b.newPage({ viewport: { width: vw, height: vh }, deviceScaleFactor: 2, colorScheme: 'light' });
      await pg.goto('http://localhost:8000/index.html', { waitUntil: 'networkidle' });
      if (t) await pg.addStyleTag({ content: fs.readFileSync('themes/' + t, 'utf8') });
      await pg.evaluate(() => document.fonts.ready);
      await pg.waitForTimeout(1200);
      await pg.screenshot({ path: `shots/${t ? t.replace('.css', '') : '0-current'}-${tag}.png` });
      await pg.close();
    }
  }
  await b.close();
})();
