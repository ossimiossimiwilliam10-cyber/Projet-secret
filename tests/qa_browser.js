/**
 * Testeur automatique Exocerveau — Playwright
 * Parcourt chaque page, clique, remplit, détecte crashs et erreurs.
 */
const { chromium } = require('playwright');

const BASE = 'http://localhost:8512';
const RESULTS = [];

async function test(name, fn) {
  process.stdout.write(`  ${name}... `);
  try {
    await fn();
    process.stdout.write('✅\n');
    RESULTS.push({ name, status: 'PASS' });
  } catch (e) {
    process.stdout.write(`❌ ${e.message.slice(0,80)}\n`);
    RESULTS.push({ name, status: 'FAIL', error: e.message });
  }
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();

  // Collecte les erreurs console
  const consoleErrors = [];
  page.on('console', msg => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  page.on('pageerror', err => {
    consoleErrors.push(err.message);
  });

  console.log('\n🔍 EXOCERVEAU — Test Automatique\n');

  // ═══ 1. NAVIGATION ═══
  console.log('── Navigation ──');

  await test('Page Aujourd\'hui', async () => {
    await page.goto(BASE + '/aujourdhui', { waitUntil: 'networkidle', timeout: 15000 });
    await page.waitForTimeout(1000);
  });

  await test('Page Planification', async () => {
    await page.goto(BASE + '/planification', { waitUntil: 'networkidle', timeout: 15000 });
    await page.waitForTimeout(1000);
  });

  await test('Page Centre Études', async () => {
    await page.goto(BASE + '/centre-etude', { waitUntil: 'networkidle', timeout: 15000 });
    await page.waitForTimeout(1000);
    // Chercher "Démarrer une session"
    const startBtn = page.locator('button:has-text("Démarrer"), button:has-text("Commencer"), button:has-text("Nouvelle")').first();
    if (await startBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
      await startBtn.click();
      await page.waitForTimeout(500);
    }
  });

  await test('Page Progression', async () => {
    await page.goto(BASE + '/progression', { waitUntil: 'networkidle', timeout: 15000 });
    await page.waitForTimeout(1000);
  });

  await test('Page Configuration', async () => {
    await page.goto(BASE + '/configuration', { waitUntil: 'networkidle', timeout: 15000 });
    await page.waitForTimeout(1000);
  });

  // ═══ 2. PROFIL ═══
  console.log('── Profil Étudiant ──');

  await test('Navigation Profil', async () => {
    await page.goto(BASE + '/configuration', { waitUntil: 'networkidle', timeout: 15000 });
    await page.waitForTimeout(1500);
  });

  // Chercher un champ nom/prénom
  const nameInput = page.locator('input[aria-label*="nom"], input[aria-label*="prénom"], input[placeholder*="Nom"]').first();
  await test('Champ nom/prénom visible', async () => {
    const visible = await nameInput.isVisible({ timeout: 3000 }).catch(() => false);
    if (!visible) throw new Error('Champ nom introuvable');
  });

  // Chercher le formulaire profil
  const profilTabs = page.locator('button[role="tab"], .stTabs button, [data-testid="stTabs"] button');
  await test('Onglets profil présents', async () => {
    const count = await profilTabs.count();
    if (count < 2) throw new Error(`Seulement ${count} onglet(s) trouvé(s)`);
  });

  // ═══ 3. VÉRIFICATION FINALE ═══
  await test('Texte "Exocerveau" visible', async () => {
    const bodyText = await page.textContent('body');
    if (!bodyText.includes('Exocerveau') && !bodyText.includes('exocerveau')) {
      throw new Error('Marque introuvable dans la page');
    }
  });

  await test('Aucune erreur console', async () => {
    // Filtrer les faux positifs
    const realErrors = consoleErrors.filter(e =>
      !e.includes('favicon') &&
      !e.includes('third-party') &&
      !e.includes('hydration')  // Streamlit warning normal
    );
    if (realErrors.length > 0) {
      throw new Error(`${realErrors.length} erreur(s): ${realErrors.slice(0,3).join(' | ')}`);
    }
  });

  // ═══ RAPPORT ═══
  console.log(`\n📊 RÉSULTATS : ${RESULTS.filter(r => r.status === 'PASS').length}/${RESULTS.length} réussis`);
  console.log('─'.repeat(40));
  RESULTS.filter(r => r.status === 'FAIL').forEach(r => {
    console.log(`  ❌ ${r.name}: ${r.error}`);
  });
  console.log('─'.repeat(40));

  await browser.close();
})();
