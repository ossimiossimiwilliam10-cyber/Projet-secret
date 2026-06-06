const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });

  // Page d'accueil
  await page.goto('http://localhost:8512/', { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: 'C:/Users/User/Desktop/screen_home.png', fullPage: true });

  // Configuration
  await page.goto('http://localhost:8512/configuration', { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: 'C:/Users/User/Desktop/screen_config.png', fullPage: true });

  // Texte brut (debug)
  const bodyText = await page.textContent('body');
  console.log('CONTENU PAGE (500 chars):');
  console.log(bodyText.slice(0, 500));

  await browser.close();
  console.log('\nScreenshots sauvegardés sur le Bureau');
})();
