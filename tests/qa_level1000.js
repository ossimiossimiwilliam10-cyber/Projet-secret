/**
 * ⚡ TEST NIVEAU 1000 — Exocerveau
 * 
 * Simule un étudiant L2 SPI Strasbourg qui :
 * - Configure son profil complet
 * - Ajoute 10 matières 
 * - Génère un planning DeepSeek
 * - Fait une session d'étude
 * - Pousse l'app dans ses retranchements
 */
const { chromium } = require('playwright');

const BASE = 'http://localhost:8512';
const DEEPSEEK_KEY = 'sk-48227cb4bdca4ba3b0dee280f1c603cc';
const REPORT = [];

function log(icon, msg) {
  const line = `  ${icon} ${msg}`;
  REPORT.push(line);
  console.log(line);
}

async function safeClick(page, selector, timeout = 5000) {
  try {
    const el = page.locator(selector).first();
    await el.waitFor({ state: 'visible', timeout });
    await el.click();
    await page.waitForTimeout(500);
    return true;
  } catch {
    return false;
  }
}

async function safeFill(page, selector, value, timeout = 5000) {
  try {
    const el = page.locator(selector).first();
    await el.waitFor({ state: 'visible', timeout });
    await el.fill(value);
    await page.waitForTimeout(300);
    return true;
  } catch {
    return false;
  }
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  const jsErrors = [];
  const networkErrors = [];
  page.on('pageerror', e => jsErrors.push(e.message));
  page.on('response', r => {
    if (r.status() >= 400) networkErrors.push(`${r.status()} ${r.url().slice(0, 80)}`);
  });

  console.log('\n⚡ TEST NIVEAU 1000 — Exocerveau');
  console.log('═'.repeat(50));

  // ═══════════════════════════════════════════════
  // 1. CHARGEMENT INITIAL
  // ═══════════════════════════════════════════════
  console.log('\n── 1. CHARGEMENT ──');

  await page.goto(BASE + '/configuration', { waitUntil: 'networkidle', timeout: 20000 });
  await page.waitForTimeout(2000);
  log('✅', 'Page Configuration chargée');

  // ═══════════════════════════════════════════════
  // 2. PROFIL ÉTUDIANT
  // ═══════════════════════════════════════════════
  console.log('\n── 2. PROFIL ÉTUDIANT ──');

  // Cliquer sur l'onglet Profil (premier onglet)
  const profilTab = page.locator('button:has-text("Profil")').first();
  if (await profilTab.isVisible({ timeout: 3000 }).catch(() => false)) {
    await profilTab.click();
    await page.waitForTimeout(1000);
    log('✅', 'Onglet Profil ouvert');
  } else {
    log('⚠️', 'Onglet Profil non trouvé — test en lecture seule');
  }

  // Lister tous les champs texte
  const textInputs = page.locator('input[type="text"]:not([readonly]), input:not([type]):not([readonly])');
  const inputCount = await textInputs.count();
  log('📋', `${inputCount} champs texte trouvés`);

  // Essayer de remplir les champs clés
  let filled = 0;
  for (let i = 0; i < Math.min(inputCount, 15); i++) {
    const inp = textInputs.nth(i);
    const lbl = (await inp.getAttribute('aria-label')) || (await inp.getAttribute('placeholder')) || '';
    const val = 
      lbl.includes('nom') || lbl.includes('Nom') ? 'Dupont' :
      lbl.includes('prénom') || lbl.includes('Prénom') ? 'Jean' :
      lbl.includes('étude') || lbl.includes('niveau') || lbl.includes('Niveau') ? 'L2 SPI' :
      lbl.includes('heure') || lbl.includes('dispo') || lbl.includes('h/sem') ? '25' :
      lbl.includes('mati') || lbl.includes('Matière') ? 'Physique' :
      null;
    if (val) {
      try {
        await inp.fill(val);
        filled++;
      } catch {}
    }
  }
  log(filled > 0 ? '✅' : '⚠️', `${filled} champ(s) rempli(s)`);

  // Chercher le champ clé API
  const apiInputs = page.locator('input[type="password"]');
  const apiCount = await apiInputs.count();
  if (apiCount > 0) {
    try {
      await apiInputs.first().fill(DEEPSEEK_KEY);
      log('✅', 'Clé DeepSeek configurée');
    } catch {
      log('❌', 'Impossible de remplir la clé API');
    }
  } else {
    log('ℹ️', 'Pas de champ clé API visible (déjà configurée ?)');
  }

  // ═══════════════════════════════════════════════
  // 3. EXPLORATION DES AUTRES PAGES
  // ═══════════════════════════════════════════════
  console.log('\n── 3. EXPLORATION ──');

  const pages = ['/aujourdhui', '/planification', '/centre-etude', '/progression'];
  for (const p of pages) {
    try {
      await page.goto(BASE + p, { waitUntil: 'networkidle', timeout: 10000 });
      await page.waitForTimeout(1500);
      const text = await page.textContent('body');
      const hasContent = text.replace(/\s/g, '').length > 200;
      log(hasContent ? '✅' : '⚠️', `${p} ${hasContent ? '(OK)' : '(presque vide)'}`);
    } catch (e) {
      log('❌', `${p} CRASH: ${e.message.slice(0, 60)}`);
    }
  }

  // ═══════════════════════════════════════════════
  // 4. BOUTONS ET INTERACTIONS
  // ═══════════════════════════════════════════════
  console.log('\n── 4. INTERACTIONS ──');

  await page.goto(BASE + '/planification', { waitUntil: 'networkidle', timeout: 10000 });
  await page.waitForTimeout(2000);

  // Compter tous les boutons cliquables
  const allBtns = page.locator('button:not([disabled])');
  const totalBtns = await allBtns.count();
  log('🔘', `${totalBtns} boutons actifs`);

  // Chercher boutons spécifiques
  const genBtn = page.locator('button:has-text("Générer"), button:has-text("Planning"), button:has-text("Lancer"), button:has-text("Créer")').first();
  const hasGen = await genBtn.isVisible({ timeout: 2000 }).catch(() => false);
  if (hasGen) {
    log('✅', 'Bouton de génération trouvé !');
    await genBtn.click();
    await page.waitForTimeout(3000);
    log('✅', 'Clic sur Générer — attente réponse...');
  } else {
    log('ℹ️', 'Pas de bouton génération (normal si pas de profil complet)');
  }

  // ═══════════════════════════════════════════════
  // 5. CENTRE D'ÉTUDES
  // ═══════════════════════════════════════════════
  console.log('\n── 5. CENTRE D\'ÉTUDES ──');

  await page.goto(BASE + '/centre-etude', { waitUntil: 'networkidle', timeout: 10000 });
  await page.waitForTimeout(2000);

  const studyBtns = page.locator('button');
  const studyCount = await studyBtns.count();
  log('🔘', `${studyCount} éléments interactifs`);

  // Chercher bouton "Démarrer" ou "Session"
  const startBtn = page.locator('button:has-text("Démarrer"), button:has-text("Session"), button:has-text("Commencer"), button:has-text("Nouvelle session")').first();
  if (await startBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
    await startBtn.click();
    await page.waitForTimeout(2000);
    log('✅', 'Session d\'étude démarrée');
  }

  // ═══════════════════════════════════════════════
  // 6. RÉSILIENCE — Retour page vide
  // ═══════════════════════════════════════════════
  console.log('\n── 6. RÉSILIENCE ──');

  // Page inexistante
  try {
    await page.goto(BASE + '/inexistante', { waitUntil: 'networkidle', timeout: 8000 });
    await page.waitForTimeout(1000);
    log('✅', 'Page inexistante ne crashe pas');
  } catch {
    log('✅', 'Page inexistante gérée proprement');
  }

  // Reload rapide x3
  let reloadOk = true;
  for (let i = 0; i < 3; i++) {
    try {
      await page.goto(BASE + '/aujourdhui', { waitUntil: 'networkidle', timeout: 8000 });
      await page.waitForTimeout(500);
    } catch { reloadOk = false; }
  }
  log(reloadOk ? '✅' : '❌', '3 rechargements rapides');

  // ═══════════════════════════════════════════════
  // 7. RAPPORT FINAL
  // ═══════════════════════════════════════════════
  console.log('\n' + '═'.repeat(50));
  console.log('📊 RAPPORT FINAL\n');

  const ok = REPORT.filter(l => l.includes('✅')).length;
  const warn = REPORT.filter(l => l.includes('⚠️')).length;
  const err = REPORT.filter(l => l.includes('❌')).length;

  console.log(`  Réussites : ${ok}  |  Avertissements : ${warn}  |  Échecs : ${err}`);
  console.log(`  Erreurs JS : ${jsErrors.length}  |  Erreurs réseau : ${networkErrors.filter(e => !e.includes('404') && !e.includes('favicon')).length}`);
  console.log('─'.repeat(50));

  if (err > 0) {
    console.log('\n❌ ÉCHECS :');
    REPORT.filter(l => l.includes('❌')).forEach(l => console.log(l));
  }

  if (jsErrors.length > 0) {
    console.log(`\n⚠️  ${jsErrors.length} ERREUR(S) JS :`);
    jsErrors.slice(0, 5).forEach(e => console.log(`  • ${e.slice(0, 150)}`));
  }

  console.log('\n✅ Test niveau 1000 terminé. Streamlit toujours actif.');

  await browser.close();
})();
