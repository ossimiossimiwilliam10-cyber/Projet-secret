/**
 * QA approfondi — interaction utilisateur réelle
 */
const { chromium } = require('playwright');

const BASE = 'http://localhost:8512';
const LOG = [];

function log(msg) { LOG.push(msg); console.log('  ' + msg); }

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));

  console.log('\n🔬 QA APPROFONDI — Exocerveau\n');

  // ═══ 1. PAGE AUJOURD'HUI ═══
  log('── Aujourd\'hui ──');
  await page.goto(BASE + '/aujourdhui', { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(1500);
  
  // Compter les expanders
  const expanders = page.locator('[data-testid="stExpander"], .st-expander, details');
  const expanderCount = await expanders.count();
  log(`${expanderCount} sections expansibles trouvées`);

  // Compter les boutons
  const buttons = page.locator('button');
  const btnCount = await buttons.count();
  log(`${btnCount} boutons trouvés`);

  // ═══ 2. PAGE CONFIGURATION ═══
  log('── Configuration ──');
  await page.goto(BASE + '/configuration', { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(2000);

  // Chercher tous les inputs
  const inputs = page.locator('input:not([type="hidden"])');
  const inputCount = await inputs.count();
  log(`${inputCount} champs de saisie trouvés`);

  // Lister les labels/placeholders
  for (let i = 0; i < Math.min(inputCount, 10); i++) {
    const input = inputs.nth(i);
    const label = await input.getAttribute('aria-label') || 
                  await input.getAttribute('placeholder') || 
                  await input.getAttribute('name') || 
                  '?';
    const type = await input.getAttribute('type') || 'text';
    log(`  Champ #${i}: [${type}] "${label}"`);
  }

  // Chercher des selects / dropdowns
  const selects = page.locator('select, [data-baseweb="select"], [role="combobox"]');
  const selectCount = await selects.count();
  log(`${selectCount} menus déroulants trouvés`);

  // ═══ 3. PAGE PLANIFICATION ═══
  log('── Planification ──');
  await page.goto(BASE + '/planification', { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(2000);
  
  const planButtons = page.locator('button');
  const planBtnCount = await planButtons.count();
  log(`${planBtnCount} boutons/éléments interactifs trouvés`);

  // Chercher "Générer" ou "Planning"
  const genBtn = page.locator('button:has-text("Générer"), button:has-text("Planning"), button:has-text("Lancer")').first();
  const hasGenBtn = await genBtn.isVisible({ timeout: 2000 }).catch(() => false);
  log(hasGenBtn ? 'Bouton de génération trouvé ✅' : 'Pas de bouton génération (normal si pas de profil)');

  // ═══ 4. PAGE PROGRESSION ═══
  log('── Progression ──');
  await page.goto(BASE + '/progression', { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(2000);
  const bodyAfter = await page.textContent('body');
  const hasData = bodyAfter.includes('XP') || bodyAfter.includes('niveau') || bodyAfter.includes('streak');
  log(hasData ? 'Statistiques de progression présentes ✅' : 'Page progression vide (normal si pas de données)');

  // ═══ 5. RÉSUMÉ ═══
  console.log(`\n📊 RÉSUMÉ QA`);
  console.log(`  Erreurs JS : ${errors.length}  |  Sections : ${expanderCount}  |  Boutons : ${btnCount}`);
  console.log(`  Inputs : ${inputCount}  |  Selects : ${selectCount}  |  Génération : ${hasGenBtn ? 'OUI' : 'NON'}`);
  console.log('─'.repeat(40));
  LOG.forEach(l => console.log('  ' + l));
  
  if (errors.length > 0) {
    console.log(`\n⚠️  ${errors.length} ERREUR(S) JAVASCRIPT :`);
    errors.forEach(e => console.log(`  ❌ ${e.slice(0,200)}`));
  }

  await browser.close();
})();
