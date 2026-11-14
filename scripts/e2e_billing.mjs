// Kiểm thử đầu-cuối phần THANH TOÁN (Polar) của DatTTS — dùng Polar GIẢ (scripts/fake_polar.py), không gọi polar.sh.
//
// Script tự bật/tắt máy chủ: Polar giả ở cổng 8899, DatTTS (server_local.py) ở cổng 8765 với dữ liệu tạm.
//   PYTHON=<venv>/bin/python node scripts/e2e_billing.mjs
// Các giai đoạn:
//   1. billing BẬT, engine giả: bảng giá EN/VI (Weekly/Monthly/Yearly, "Most popular", "Save X%"), mua TỪNG chu kỳ
//      → trang trả tiền giả → checkout-success.html → dán key → phòng thu hiện gói + hạn mức đúng kỳ (tuần/tháng),
//      tạo giọng tính vào đúng bucket quota, cổng khách hàng, key bị thu hồi, webhook subscription.revoked,
//      thông báo lỗi song ngữ theo Accept-Language, trang pháp lý EN/VI, chuyển hướng thanh-toan.html.
//   2. billing BẬT, engine VieNeu thật: một lần /api/tts bằng key Pro năm (bỏ qua nếu E2E_REAL=0).
//   3. billing TẮT (không có POLAR_ACCESS_TOKEN): "Coming soon" + danh sách chờ, checkout 503 (EN/VI).
// Biến môi trường: PYTHON (mặc định python3), DATTTS_MODEL_DIR (cho giai đoạn 2), E2E_REAL=0, E2E_SHOTS,
//   E2E_PORT (8765), E2E_POLAR_PORT (8899), E2E_DATA (nơi tạo thư mục dữ liệu tạm của lần chạy; mặc định thư mục tạm hệ thống).
import { createRequire } from 'node:module';
import { spawn, execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
let chromium;
try { ({ chromium } = require('playwright')); }
catch { ({ chromium } = require(require.resolve('playwright', { paths: [process.env.NODE_PATH || '', '/opt/node-tools/node_modules'] }))); }

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PY = process.env.PYTHON || 'python3';
const PORT = +(process.env.E2E_PORT || 8765), PPORT = +(process.env.E2E_POLAR_PORT || 8899);
const BASE = `http://127.0.0.1:${PORT}`, POLAR = `http://127.0.0.1:${PPORT}`;
const SHOTS = process.env.E2E_SHOTS || '';
// Mỗi lần chạy dùng thư mục dữ liệu MỚI (cache key/quota của lần trước không làm sai kết quả).
const DATA_ROOT = process.env.E2E_DATA || os.tmpdir();
fs.mkdirSync(DATA_ROOT, { recursive: true });
const DATA = fs.mkdtempSync(path.join(DATA_ROOT, 'dattts-e2e-billing-'));
const REAL = process.env.E2E_REAL !== '0';

const results = [];
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond, detail });
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
}
async function step(name, fn) {
  try { await fn(); } catch (e) { check(name + ' (ngoại lệ)', false, String(e && e.message || e).split('\n')[0]); }
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ---------- quản lý tiến trình ----------
const procs = new Set();
function polarEnv() {
  const out = execFileSync(PY, [path.join(REPO, 'scripts/fake_polar.py'), '--print-env', '--port', String(PPORT)], { encoding: 'utf8' });
  const env = {};
  for (const line of out.split('\n')) { const m = line.match(/^export (\w+)='(.*)'$/); if (m) env[m[1]] = m[2]; }
  return env;
}
function start(name, args, env, cwd, logFile) {
  const fd = fs.openSync(logFile, 'a');
  const p = spawn(PY, args, { cwd, env: { ...process.env, ...env }, stdio: ['ignore', fd, fd] });
  p.on('exit', () => procs.delete(p));
  p._name = name; procs.add(p);
  return p;
}
async function stop(p) {
  if (!p || p.exitCode != null) return;
  const done = new Promise(r => p.once('exit', r));
  p.kill('SIGTERM');
  await Promise.race([done, sleep(8000).then(() => p.kill('SIGKILL'))]);
}
async function waitUp(url, ms = 300000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try { const r = await fetch(url); if (r.ok) return true; } catch { /* chưa lên */ }
    await sleep(300);
  }
  throw new Error('không lên: ' + url);
}
async function startPolar() {
  const p = start('polar', [path.join(REPO, 'scripts/fake_polar.py'), '--port', String(PPORT), '--webhook-url', `${BASE}/api/billing/webhook`], {}, REPO, path.join(DATA, 'polar.log'));
  await waitUp(POLAR + '/');
  return p;
}
let dataDir = '';
async function startDatTTS({ billing, fake }) {
  // Xoá mọi biến POLAR_* thừa hưởng từ shell để giai đoạn "tắt" thật sự tắt.
  const env = { FREE_REQUESTS_PER_MIN: '0', PUBLIC_BASE_URL: BASE, ADMIN_TOKEN: 'e2e-admin', POLAR_ACCESS_TOKEN: '', POLAR_API_BASE: '', POLAR_ORGANIZATION_ID: '' };
  if (billing) Object.assign(env, polarEnv());
  dataDir = path.join(DATA, billing ? (fake ? 'on' : 'on-real') : 'off');
  const args = ['server_local.py', '--port', String(PORT), '--data-dir', dataDir];
  if (fake) args.push('--fake');
  const p = start('dattts', args, env, path.join(REPO, 'backend'), path.join(DATA, 'dattts.log'));
  await waitUp(BASE + '/api/health');
  return p;
}
// kv.json của LocalStore (server_local.py dùng <data-dir>/fake hoặc <data-dir>/vieneu)
function kv() {
  for (const sub of ['fake', 'vieneu']) {
    const f = path.join(dataDir, sub, 'kv.json');
    if (fs.existsSync(f)) { try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch { return {}; } }
  }
  return {};
}

// ---------- tiện ích trình duyệt ----------
async function newPage(browser, { viewport = { width: 1280, height: 900 }, locale = 'en-US' } = {}) {
  const ctx = await browser.newContext({ viewport, locale });
  await ctx.route(/fonts\.(googleapis|gstatic)\.com/, r => r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
  const page = await ctx.newPage();
  const errors = [];
  // 401/403/503 có chủ đích (key sai, billing tắt) làm Chromium in "Failed to load resource" — bỏ qua dòng đó.
  page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  return { ctx, page, errors };
}
const j = async (url, opts = {}) => {
  const r = await fetch(url, { ...opts, headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) } });
  let body = null; try { body = await r.json(); } catch { /* không phải JSON */ }
  return { status: r.status, body, headers: r.headers };
};
const me = (key, lang) => j(BASE + '/api/me', { headers: { 'X-API-Key': key, ...(lang ? { 'Accept-Language': lang } : {}) } });
const freeQuota = () => j(BASE + '/api/quota').then(r => r.body);
const polarPost = (p, body) => j(POLAR + p, { method: 'POST', body: JSON.stringify(body) });
const shot = (page, name) => SHOTS ? page.screenshot({ path: path.join(SHOTS, name), fullPage: false }).catch(() => {}) : null;
const text = (page, sel) => page.textContent(sel).then(t => (t || '').replace(/\s+/g, ' ').trim());
const nbsp = s => s.replace(/[  ]/g, ' ');
const pickCycle = (page, c) => page.click(`#cycle label:has(input[value="${c}"])`);
const proReady = page => page.waitForFunction(() => /\$|US\$/.test(document.querySelector('[data-plan-id="pro"] [data-price]').textContent)
  && document.querySelector('#buyPro').getAttribute('aria-disabled') !== 'true', null, { timeout: 15000 });

const PLANS = {
  week: { id: 'pro_week', name: 'Pro Weekly', nameVi: 'Pro tuần', price: '$1.99', per: '/ week', cents: 199, limit: 250000, period: 'week', quota: '250,000 characters per week', key: 'DATTTS-WEEK-TEST' },
  month: { id: 'pro_month', name: 'Pro Monthly', nameVi: 'Pro tháng', price: '$4.99', per: '/ month', cents: 499, limit: 1000000, period: 'month', quota: '1,000,000 characters per month', key: 'DATTTS-MONTH-TEST', save: 37, popular: true },
  year: { id: 'pro_year', name: 'Pro Yearly', nameVi: 'Pro năm', price: '$49.99', per: '/ year', cents: 4999, limit: 1000000, period: 'month', quota: '1,000,000 characters per month', key: 'DATTTS-YEAR-TEST', save: 16 },
};
// Bucket quota mong đợi theo giờ VN (UTC+7)
function bucketFor(period, now = new Date()) {
  const vn = new Date(now.getTime() + 7 * 3600e3);
  if (period === 'month') return `${vn.getUTCFullYear()}-${String(vn.getUTCMonth() + 1).padStart(2, '0')}`;
  const d = new Date(Date.UTC(vn.getUTCFullYear(), vn.getUTCMonth(), vn.getUTCDate()));
  const dow = d.getUTCDay() || 7; d.setUTCDate(d.getUTCDate() + 4 - dow);
  const y = d.getUTCFullYear(), w = Math.ceil(((d - Date.UTC(y, 0, 1)) / 864e5 + 1) / 7);
  return `${y}-W${String(w).padStart(2, '0')}`;
}

// ---------- giai đoạn 1: billing bật, engine giả ----------
async function phaseOn(browser) {
  await polarPost('/_fake/reset', {});

  await step('bảng giá không chờ giọng / không rơi về danh sách chờ khi đang tải', async () => {
    {
      const { ctx, page } = await newPage(browser);
      await page.route('**/api/voices', async r => { await sleep(5000); await r.continue().catch(() => {}); });
      const t0 = Date.now();
      await page.goto(BASE + '/#pricing');
      await page.waitForFunction(() => /Get Pro/.test(document.querySelector('#buyPro .lbl').textContent), null, { timeout: 4000 });
      check('giọng tải chậm 5 s: nút "Get Pro" hiện trước khi giọng xong', Date.now() - t0 < 4500 && !(await page.$('.vcard')), `${Date.now() - t0} ms`);
      check('giọng tải chậm: form chờ ẩn', await page.isHidden('#waitlist'));
      await page.unrouteAll({ behavior: 'ignoreErrors' }).catch(() => {});
      await ctx.close();
    }
    {
      const { ctx, page, errors } = await newPage(browser);
      await page.route('**/api/billing/plans', async r => { await sleep(3000); await r.continue().catch(() => {}); });
      await page.goto(BASE + '/#pricing');
      const lbl = await text(page, '#buyPro .lbl');
      check('khi chưa biết billing: nút "Loading…" + aria-disabled', /Loading/.test(lbl) && (await page.getAttribute('#buyPro', 'aria-disabled')) === 'true', lbl);
      check('khi chưa biết billing: form chờ ẩn', await page.isHidden('#waitlist'));
      await page.evaluate(() => document.querySelector('#buyPro').click());
      await page.waitForTimeout(300);
      check('bấm khi đang tải: không rời trang, không focus form chờ', page.url().startsWith(BASE) && !(await page.evaluate(() => document.activeElement && document.activeElement.id === 'wlEmail')), page.url());
      await page.waitForFunction(() => /Get Pro/.test(document.querySelector('#buyPro .lbl').textContent), null, { timeout: 10000 });
      check('sau khi tải: nút "Get Pro", hết aria-disabled', (await page.getAttribute('#buyPro', 'aria-disabled')) === null);
      check('không có lỗi console (tải bảng giá)', errors.length === 0, errors.join(' | '));
      await ctx.close();
    }
  });

  await step('API bảng giá', async () => {
    const r = await j(BASE + '/api/billing/plans');
    const plans = r.body, by = Object.fromEntries(plans.plans.map(p => [p.id, p]));
    check('/api/billing/plans enabled, currency usd, đủ 4 gói', plans.enabled === true && plans.currency === 'usd' && ['free', 'pro_week', 'pro_month', 'pro_year'].every(id => by[id]), JSON.stringify(plans.plans.map(p => p.id)));
    check('giá từ Polar 0/199/499/4999', by.free.price === 0 && by.pro_week.price === 199 && by.pro_month.price === 499 && by.pro_year.price === 4999, JSON.stringify(plans.plans.map(p => [p.id, p.price])));
    check('interval/period: week/week, month/month, year/month; free day', by.pro_week.interval === 'week' && by.pro_week.period === 'week' && by.pro_month.interval === 'month' && by.pro_month.period === 'month' && by.pro_year.interval === 'year' && by.pro_year.period === 'month' && by.free.period === 'day',
      JSON.stringify(plans.plans.map(p => [p.id, p.interval, p.period])));
    check('hạn mức 20000/250000/1000000/1000000', by.free.chars === 20000 && by.pro_week.chars === 250000 && by.pro_month.chars === 1000000 && by.pro_year.chars === 1000000);
    check('pro_month popular, Save 37%; pro_year Save 16%; pro_week không save', by.pro_month.popular === true && by.pro_month.save_percent === 37 && by.pro_year.save_percent === 16 && !by.pro_week.save_percent && !by.pro_year.popular,
      JSON.stringify(plans.plans.map(p => [p.id, p.popular, p.save_percent])));
    check('mọi gói Pro available', ['pro_week', 'pro_month', 'pro_year'].every(id => by[id].available === true));
    check('Vary: Accept-Language', /accept-language/i.test(r.headers.get('vary') || ''), r.headers.get('vary'));
    const vi = (await j(BASE + '/api/billing/plans', { headers: { 'Accept-Language': 'vi-VN,vi;q=0.9' } })).body;
    check('tên gói tiếng Việt khi Accept-Language: vi', vi.plans.find(p => p.id === 'pro_week').name === 'Pro tuần', vi.plans.map(p => p.name).join(', '));
  });

  await step('bảng giá EN: toggle Weekly / Monthly / Yearly', async () => {
    const { ctx, page, errors } = await newPage(browser);
    await page.goto(BASE + '/#pricing');
    await proReady(page);
    check('<html lang="en"> mặc định (en-US)', (await page.getAttribute('html', 'lang')) === 'en');
    check('mặc định Monthly', await page.isChecked('input[name="cycle"][value="month"]'));
    for (const c of ['month', 'week', 'year']) {
      const P = PLANS[c];
      await pickCycle(page, c);
      await page.waitForFunction(id => document.querySelector('#buyPro').dataset.buy === id, P.id, { timeout: 5000 });
      const price = nbsp(await text(page, '[data-plan-id="pro"] [data-price]'));
      check(`${c}: giá ${P.price} ${P.per}`, price.includes(P.price) && price.includes(P.per), price);
      const pop = await page.isVisible('#proPopular'), save = await page.isVisible('#proSave');
      check(`${c}: nhãn "Most popular" ${P.popular ? 'hiện' : 'ẩn'}`, pop === !!P.popular && (!P.popular || (await text(page, '#proPopular')) === 'Most popular'));
      check(`${c}: nhãn Save ${P.save ? P.save + '%' : 'ẩn'}`, save === !!P.save && (!P.save || (await text(page, '#proSave')) === `Save ${P.save}%`), save ? await text(page, '#proSave') : '');
      const q = await text(page, '#proQuota');
      check(`${c}: hạn mức "${P.quota}"`, q === P.quota, q);
      check(`${c}: nút "Get Pro"`, (await text(page, '#buyPro .lbl')) === 'Get Pro');
      check(`${c}: không có quy đổi VND ở EN`, await page.isHidden('[data-plan-id="pro"] [data-vnd]'));
      if (c === 'year') await shot(page, 'billing-pricing-en-year.png');
    }
    check('nhãn chu kỳ: Monthly −37%, Yearly −16%', (await text(page, '[data-cycle-badge="month"]')) === '−37%' && (await text(page, '[data-cycle-badge="year"]')) === '−16%');
    check('form chờ ẩn khi đã mở bán', await page.isHidden('#waitlist'));
    // ----- chuyển sang VI tại chỗ -----
    await pickCycle(page, 'month');
    await page.click('[data-lang="vi"]');
    await page.waitForFunction(() => document.documentElement.lang === 'vi', null, { timeout: 5000 });
    const pv = nbsp(await text(page, '[data-plan-id="pro"] [data-price]'));
    check('VI: giá "4,99 US$ / tháng"', pv.includes('4,99 US$') && pv.includes('/ tháng'), pv);
    const vnd = nbsp(await text(page, '[data-plan-id="pro"] [data-vnd]'));
    check('VI: quy đổi VND tham khảo hiện', await page.isVisible('[data-plan-id="pro"] [data-vnd]') && /đ|₫/.test(vnd) && /tham khảo/.test(vnd), vnd);
    check('VI: "Phổ biến nhất", "Tiết kiệm 37%", nút "Mua gói Pro"', (await text(page, '#proPopular')) === 'Phổ biến nhất' && (await text(page, '#proSave')) === 'Tiết kiệm 37%' && (await text(page, '#buyPro .lbl')) === 'Mua gói Pro');
    check('VI: hạn mức "1.000.000 ký tự mỗi tháng"', (await text(page, '#proQuota')) === '1.000.000 ký tự mỗi tháng', await text(page, '#proQuota'));
    check('VI: lựa chọn lưu vào localStorage', (await page.evaluate(() => localStorage.getItem('dattts.lang'))) === 'vi');
    await shot(page, 'billing-pricing-vi.png');
    await page.click('[data-lang="en"]');
    await page.waitForFunction(() => document.documentElement.lang === 'en', null, { timeout: 5000 });
    check('quay về EN tại chỗ', (await text(page, '#buyPro .lbl')) === 'Get Pro');
    check('không có lỗi console (bảng giá)', errors.length === 0, errors.join(' | '));
    await ctx.close();
  });

  // ----- mua từng chu kỳ: Get Pro → Polar giả → checkout-success → dán key → phòng thu → tạo giọng -----
  for (const c of ['week', 'month', 'year']) {
    const P = PLANS[c];
    await step(`mua ${P.id}`, async () => {
      const { ctx, page, errors } = await newPage(browser);
      await page.goto(BASE + '/#pricing');
      await proReady(page);
      await pickCycle(page, c);
      await page.waitForFunction(id => document.querySelector('#buyPro').dataset.buy === id, P.id, { timeout: 5000 });
      const req = page.waitForRequest(r => r.url().endsWith('/api/billing/checkout') && r.method() === 'POST');
      await page.click('#buyPro');
      const body = JSON.parse((await req).postData() || '{}');
      check(`${c}: POST checkout {plan:"${P.id}"}`, body.plan === P.id, JSON.stringify(body));
      await page.waitForURL(u => u.href.startsWith(POLAR + '/checkout/'), { timeout: 15000 });
      check(`${c}: chuyển sang trang thanh toán Polar (giả) đúng giá ${P.price}`, (await text(page, '#fakeAmount')) === P.price, await text(page, '#fakeAmount'));
      const buyer = `buy-${c}-${Date.now()}@example.com`;
      await page.fill('#fakeEmail', buyer);
      await page.click('#fakePay');
      await page.waitForURL(/\/checkout-success\.html\?checkout_id=[0-9a-f-]{36}$/, { timeout: 15000 });
      check(`${c}: Polar chuyển về /checkout-success.html với {CHECKOUT_ID} đã thay`, /checkout-success\.html\?checkout_id=[0-9a-f-]{36}$/.test(page.url()), page.url());
      await page.waitForSelector('#payCard[data-state="succeeded"]', { timeout: 20000 });
      const card = await text(page, '#payCard');
      check(`${c}: trang cảm ơn (EN) báo thành công + tên gói + email che bớt`, /You’re all set!/.test(card) && card.includes(P.name) && card.includes('b***@example.com'), card.slice(0, 160));
      if (c === 'month') await shot(page, 'billing-success-en.png');

      const mail = (await j(POLAR + '/_fake/outbox')).body.emails.find(m => m.to === buyer);
      check(`${c}: Polar giả cấp key DATTTS cho người mua`, mail && /^DATTTS-/.test(mail.key));
      if (!mail) { await ctx.close(); return; }
      await page.fill('#keyInput', mail.key);
      await page.click('#keySave');
      await page.waitForSelector('#keyMsg.ok', { timeout: 10000 });
      const msg = await text(page, '#keyMsg');
      check(`${c}: dán key ở trang cảm ơn → "${P.name}, ${P.quota}"`, msg.includes(P.name) && msg.includes(P.quota), msg);
      check(`${c}: key lưu localStorage`, (await page.evaluate(() => localStorage.getItem('dattts.apikey'))) === JSON.stringify(mail.key));
      // đổi ngôn ngữ ngay trên trang cảm ơn
      await page.click('[data-lang="vi"]');
      await page.waitForFunction(() => document.documentElement.lang === 'vi', null, { timeout: 5000 });
      const cardVi = await text(page, '#payCard');
      check(`${c}: trang cảm ơn đổi sang VI tại chỗ`, cardVi.includes(`Gói ${P.nameVi} đã được kích hoạt`), cardVi.slice(0, 120));
      if (c === 'month') await shot(page, 'billing-success-vi.png');
      await page.click('[data-lang="en"]');

      // ----- phòng thu -----
      await page.click('a[href="./#studio"]');
      await page.waitForSelector('#acctBar:not([hidden]) #planBadge.on', { timeout: 15000 });
      const badge = await text(page, '#planBadge'), usage = await text(page, '#acctUsage');
      check(`${c}: phòng thu hiện "${P.name}"`, badge === P.name, badge);
      check(`${c}: hạn mức ${P.period} ${P.limit.toLocaleString('en-US')}`, usage.includes(`/ ${P.limit.toLocaleString('en-US')} characters used this ${P.period}`) && usage.includes('b***@example.com'), usage);
      await page.waitForFunction(p => new RegExp('left this ' + p).test(document.querySelector('#quotaText').textContent), P.period, { timeout: 10000 }).catch(() => {});
      const qt = await text(page, '#quotaText');
      check(`${c}: ô quota "… left this ${P.period}"`, qt.includes(`/ ${P.limit.toLocaleString('en-US')} characters left this ${P.period}`), qt);

      const freeBefore = await freeQuota(), m0 = (await me(mail.key)).body;
      check(`${c}: /api/me plan=${P.id} period=${P.period} limit=${P.limit}`, m0.plan === P.id && m0.period === P.period && m0.limit === P.limit, JSON.stringify(m0));
      if (P.period === 'week') {
        const ra = new Date(m0.reset_at);
        check(`${c}: reset thứ Hai 00:00 giờ VN`, /T00:00:00\+07:00$/.test(m0.reset_at) && new Date(ra.getTime() + 7 * 3600e3).getUTCDay() === 1, m0.reset_at);
      } else check(`${c}: reset ngày 1 đầu tháng sau 00:00 giờ VN`, /-01T00:00:00\+07:00$/.test(m0.reset_at), m0.reset_at);
      const t = `Gói ${P.nameVi} đọc thử lúc ${Date.now()}. Cảm ơn bạn đã ủng hộ DatTTS.`;
      await page.fill('#txt', t);
      const respP = page.waitForResponse(r => r.url().endsWith('/api/tts') && r.request().method() === 'POST', { timeout: 60000 });
      await page.click('#genBtn');
      const resp = await respP;
      const data = await resp.json();
      check(`${c}: tạo giọng bằng key 200, mang X-API-Key + Accept-Language`, resp.status() === 200 && resp.request().headers()['x-api-key'] === mail.key && resp.request().headers()['accept-language'] === 'en', String(resp.status()));
      check(`${c}: response.quota theo kỳ ${P.period}`, data.quota && data.quota.period === P.period && data.quota.limit === P.limit, JSON.stringify(data.quota));
      const m1 = (await me(mail.key)).body, freeAfter = await freeQuota();
      check(`${c}: used tăng đúng ${data.chars} ký tự`, m1.used - m0.used === data.chars && data.chars > 0, `${m0.used} → ${m1.used}`);
      check(`${c}: quota miễn phí theo IP không đổi`, freeAfter.used === freeBefore.used, `${freeBefore.used} → ${freeAfter.used}`);
      const lk = (await j(POLAR + '/_fake/state')).body.keys[mail.key].id;
      const bk = `usage:${lk}:${bucketFor(P.period)}`, kvs = kv();
      const usageKeys = Object.keys(kvs).filter(k => k.startsWith(`usage:${lk}:`));
      check(`${c}: tính vào bucket ${bk.split(':').pop()}`, kvs[bk] === data.chars && usageKeys.length === 1, usageKeys.map(k => k + '=' + kvs[k]).join(', '));
      await page.waitForFunction(n => document.querySelector('#acctUsage').textContent.includes(n.toLocaleString('en-US') + ' /'), m1.used, { timeout: 10000 }).catch(() => {});
      check(`${c}: thanh tài khoản cập nhật số đã dùng`, (await text(page, '#acctUsage')).startsWith(m1.used.toLocaleString('en-US') + ' /'), await text(page, '#acctUsage'));
      if (c === 'week') await shot(page, 'billing-studio-week.png');
      check(`${c}: không có lỗi console`, errors.length === 0, errors.join(' | '));
      await ctx.close();
    });
  }

  await step('webhook order.paid + subscription.created', async () => {
    let ev = {};
    for (let i = 0; i < 20; i++) { ev = (await j(BASE + '/api/admin/billing', { headers: { Authorization: 'Bearer e2e-admin' } })).body || {}; if ((ev.events_by_type || {})['subscription.created'] >= 3) break; await sleep(250); }
    const by = ev.events_by_type || {};
    check('DatTTS nhận 3× order.paid + subscription.created (chữ ký đúng)', by['order.paid'] >= 3 && by['subscription.created'] >= 3, JSON.stringify(by));
    const u = ev.usage_by_plan || {};
    check('/api/admin/billing tổng ký tự theo gói (week/month/year) > 0', u.pro_week > 0 && u.pro_month > 0 && u.pro_year > 0 && /^\d{4}-W\d\d$/.test(ev.week || ''), JSON.stringify(u) + ' ' + ev.week);
  });

  await step('phòng thu: key thử + cổng khách hàng', async () => {
    const { ctx, page, errors } = await newPage(browser);
    await page.goto(BASE + '/#studio');
    await page.waitForSelector('.vcard', { timeout: 20000 });
    await page.click('#keyBtn');
    await page.fill('#keyInput', 'DATTTS-MONTH-TEST');
    await page.click('#keySave');
    await page.waitForSelector('#acctBar:not([hidden]) #planBadge.on', { timeout: 10000 });
    check('key nhập trong hộp thoại → "Pro Monthly"', (await text(page, '#planBadge')) === 'Pro Monthly');
    check('nút "Manage plan" hiện', await page.isVisible('#portalBtn'));
    await page.click('#portalBtn');
    await page.waitForURL(u => u.href.startsWith(POLAR + '/portal?'), { timeout: 15000 });
    check('"Manage plan" mở customer_portal_url của Polar', page.url().startsWith(POLAR + '/portal?customer_session_token='), page.url());
    check('cổng khách hàng đúng khách', (await text(page, '#portalEmail')) === 'an.nguyen@example.com');
    check('không có lỗi console (phòng thu)', errors.length === 0, errors.join(' | '));
    await ctx.close();
  });

  await step('key bị thu hồi', async () => {
    const { ctx, page, errors } = await newPage(browser);
    await ctx.addInitScript(() => { if (!sessionStorage.getItem('seeded')) { localStorage.setItem('dattts.apikey', JSON.stringify('DATTTS-REVOKED-TEST')); sessionStorage.setItem('seeded', '1'); } });
    await page.goto(BASE + '/#studio');
    await page.waitForSelector('#alert:not([hidden])', { timeout: 15000 });
    const al = await text(page, '#alert');
    check('key thu hồi → thông báo rõ (EN)', /no longer valid/.test(al), al.slice(0, 90));
    check('key thu hồi bị gỡ khỏi localStorage', (await page.evaluate(() => localStorage.getItem('dattts.apikey'))) === null);
    check('thanh tài khoản ẩn, quay về lượt miễn phí', await page.isHidden('#acctBar') && /left today/.test(await text(page, '#quotaText')), await text(page, '#quotaText'));
    // Đổi ngôn ngữ: thông báo (chuỗi phía trình duyệt) được dịch lại, kể cả aria-label nút đóng
    await page.click('[data-lang="vi"]');
    await page.waitForFunction(() => /không còn hợp lệ/.test(document.querySelector('#alert').textContent), null, { timeout: 5000 }).catch(() => {});
    const alVi = await text(page, '#alert'), xVi = await page.getAttribute('#alert .x', 'aria-label');
    check('VI: thông báo key thu hồi được dịch lại (không còn tiếng Anh)', /không còn hợp lệ/.test(alVi) && !/no longer valid/.test(alVi) && xVi !== 'Dismiss', `${alVi.slice(0, 60)} | ${xVi}`);
    await page.click('[data-lang="en"]');
    // Thông báo của MÁY CHỦ (ngôn ngữ cũ) bị gỡ khi đổi ngôn ngữ thay vì để lẫn hai thứ tiếng
    await page.fill('#txt', 'x'.repeat(29999));
    await page.waitForFunction(() => /min/.test(document.querySelector('#estTime')?.textContent || ''), null, { timeout: 5000 }).catch(() => {});
    const est = await text(page, '#estTime');
    check('ước lượng thời lượng 29,999 ký tự không ra "60 sec"', !/\b60 sec/.test(est) && /20 min/.test(est), est);
    await page.click('#genBtn');
    await page.waitForSelector('#alert:not([hidden])', { timeout: 15000 });
    const q = await text(page, '#alert');
    check('máy chủ báo vượt quota (EN)', /free characters left today|daily free/i.test(q), q.slice(0, 90));
    await page.click('[data-lang="vi"]');
    await page.waitForTimeout(300);
    const qv = (await page.isVisible('#alert')) ? await text(page, '#alert') : '';
    check('VI: thông báo máy chủ tiếng Anh không còn hiện sau khi đổi sang VI', !/free characters|today/i.test(qv), qv.slice(0, 90));
    await page.fill('#txt', 'Xin chào.');
    // VI: hộp nhập key hiện thông báo lỗi TIẾNG VIỆT từ máy chủ
    await page.click('[data-lang="vi"]');
    await page.click('#keyBtn');
    await page.fill('#keyInput', 'DATTTS-REVOKED-TEST');
    await page.click('#keySave');
    await page.waitForSelector('#keyMsg.err', { timeout: 10000 });
    const km = await text(page, '#keyMsg');
    check('VI: hộp nhập key báo lỗi tiếng Việt (message của máy chủ)', /không đúng|hết hạn|không hợp lệ/.test(km), km);
    check('không có lỗi console (key thu hồi)', errors.length === 0, errors.join(' | '));
    await ctx.close();
  });

  await step('webhook subscription.revoked', async () => {
    const K = 'DATTTS-MONTH-TEST';
    check('key còn hợp lệ trước khi huỷ', (await me(K)).status === 200);
    await polarPost('/_fake/revoke', { key: K });
    check('chưa có webhook → cache vẫn cho qua', (await me(K)).status === 200);
    const bad = (await polarPost('/_fake/webhook', { type: 'subscription.revoked', key: K, bad_signature: true })).body;
    check('webhook ký sai → 403', bad.status_code === 403, JSON.stringify(bad.body));
    check('webhook ký sai không xoá cache', (await me(K)).status === 200);
    const wh = (await polarPost('/_fake/webhook', { type: 'subscription.revoked', key: K, revoke: true })).body;
    check('webhook subscription.revoked → 202', wh.status_code === 202, JSON.stringify(wh.body));
    const after = await me(K);
    check('key ngừng hoạt động ngay sau webhook', after.status === 401 && after.body.error.code === 'invalid_api_key', JSON.stringify(after.body));
    const { ctx, page } = await newPage(browser);
    await ctx.addInitScript(k => { if (!sessionStorage.getItem('seeded')) { localStorage.setItem('dattts.apikey', JSON.stringify(k)); sessionStorage.setItem('seeded', '1'); } }, K);
    await page.goto(BASE + '/#studio');
    await page.waitForSelector('#alert:not([hidden])', { timeout: 15000 });
    check('phòng thu báo key hết hiệu lực và gỡ key', (await page.evaluate(() => localStorage.getItem('dattts.apikey'))) === null, await text(page, '#alert'));
    await ctx.close();
    const repeat = await j(BASE + '/api/billing/webhook', { method: 'POST', body: '{}' });
    check('webhook không chữ ký → 403', repeat.status === 403, String(repeat.status));
  });

  await step('thông báo lỗi song ngữ (Accept-Language)', async () => {
    const cases = [
      ['/api/me bad key', () => me('DATTTS-KHONG-CO-' + Date.now(), 'en-US,en;q=0.9'), () => me('DATTTS-KHONG-CO2-' + Date.now(), 'vi-VN,vi;q=0.9'), 401, 'invalid_api_key'],
      ['/api/tts empty_text', () => j(BASE + '/api/tts', { method: 'POST', headers: { 'Accept-Language': 'en' }, body: JSON.stringify({ text: '  ' }) }),
        () => j(BASE + '/api/tts', { method: 'POST', headers: { 'Accept-Language': 'vi' }, body: JSON.stringify({ text: '  ' }) }), 400, 'empty_text'],
      ['/api/tts bad_speed', () => j(BASE + '/api/tts', { method: 'POST', headers: { 'Accept-Language': 'en-GB' }, body: JSON.stringify({ text: 'Xin chào', speed: 3 }) }),
        () => j(BASE + '/api/tts', { method: 'POST', headers: { 'Accept-Language': 'fr,vi;q=0.5' }, body: JSON.stringify({ text: 'Xin chào', speed: 3 }) }), 400, 'bad_speed'],
      ['checkout bad_plan', () => j(BASE + '/api/billing/checkout', { method: 'POST', body: JSON.stringify({ plan: 'personal' }) }),
        () => j(BASE + '/api/billing/checkout', { method: 'POST', headers: { 'Accept-Language': 'vi' }, body: JSON.stringify({ plan: 'unlimited' }) }), 400, 'bad_plan'],
      ['checkout not found', () => j(BASE + '/api/billing/checkout/00000000-0000-4000-8000-000000000000'),
        () => j(BASE + '/api/billing/checkout/00000000-0000-4000-8000-000000000000', { headers: { 'Accept-Language': 'vi' } }), 404, null],
    ];
    const viRe = /[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]/i;
    for (const [name, en, vi, st, code] of cases) {
      const a = await en(), b = await vi();
      const ma = a.body && a.body.error && a.body.error.message || '', mb = b.body && b.body.error && b.body.error.message || '';
      check(`${name}: ${st} EN`, a.status === st && (!code || a.body.error.code === code) && ma && !viRe.test(ma), ma);
      check(`${name}: ${st} VI`, b.status === st && (!code || b.body.error.code === code) && viRe.test(mb), mb);
    }
    const none = await j(BASE + '/api/tts', { method: 'POST', body: JSON.stringify({ text: '' }) });
    check('không có Accept-Language → tiếng Anh', none.body && !viRe.test(none.body.error.message), none.body && none.body.error.message);
    // Từ trình duyệt: phòng thu ở VI gửi Accept-Language: vi và hiện message tiếng Việt
    const { ctx, page } = await newPage(browser, { locale: 'vi-VN' });
    await page.goto(BASE + '/#studio');
    await page.waitForSelector('.vcard', { timeout: 20000 });
    check('locale vi-VN → giao diện VI tự động', (await page.getAttribute('html', 'lang')) === 'vi');
    await page.fill('#txt', '[Không Có Giọng Này] Xin chào.');
    const rp = page.waitForResponse(r => r.url().endsWith('/api/tts'));
    await page.click('#genBtn');
    const r = await rp;
    await page.waitForSelector('#alert:not([hidden])', { timeout: 10000 });
    const al = await text(page, '#alert');
    check('VI: request mang Accept-Language: vi; lỗi unknown_voice tiếng Việt', r.request().headers()['accept-language'] === 'vi' && r.status() === 400 && viRe.test(al), al.slice(0, 100));
    await page.click('[data-lang="en"]');
    await page.waitForFunction(() => document.documentElement.lang === 'en');
    const rp2 = page.waitForResponse(r => r.url().endsWith('/api/tts'));
    await page.click('#genBtn');
    await rp2;
    await page.waitForFunction(() => !/[ăâđêôơư]/i.test(document.querySelector('#alert').textContent.replace(/\[[^\]]*\]|Trúc Ly|Thiện Minh|Mai Anh|[A-ZĐ][^\s,.]*\s?[A-ZĐ][^\s,.]*/g, '')), null, { timeout: 5000 }).catch(() => {});
    const al2 = await text(page, '#alert');
    check('EN: cùng lỗi, message tiếng Anh', /voice/i.test(al2), al2.slice(0, 100));
    await ctx.close();
  });

  await step('checkout API', async () => {
    const bad = await j(BASE + '/api/billing/checkout/khong-phai-uuid');
    check('checkout_id sai dạng → 400', bad.status === 400, String(bad.status));
    for (const id of ['pro_week', 'pro_month', 'pro_year']) {
      const r = await j(BASE + '/api/billing/checkout', { method: 'POST', body: JSON.stringify({ plan: id }) });
      const cid = r.body && r.body.url ? r.body.url.split('/').pop() : '';
      const st = (await j(BASE + '/api/billing/checkout/' + cid)).body || {};
      check(`POST checkout ${id} → url Polar, phiên mang plan ${id}`, r.status === 200 && r.body.url.startsWith(POLAR + '/checkout/') && st.plan === id && st.status === 'open', JSON.stringify(st));
    }
    const co = Object.values((await j(POLAR + '/_fake/state')).body.checkouts).pop();
    check('success_url = /checkout-success.html?checkout_id={CHECKOUT_ID}, return_url = /#pricing', co.success_url === BASE + '/checkout-success.html?checkout_id={CHECKOUT_ID}' && co.return_url === BASE + '/#pricing', co.success_url + ' ' + co.return_url);
    const r = await j(BASE + '/api/billing/checkout', { method: 'POST', body: JSON.stringify({ plan: 'pro_month', email: 'an@gmail.con' }) });
    check('checkout email tên miền hỏng → 400 bad_email', r.status === 400 && r.body.error.code === 'bad_email', `${r.status} ${JSON.stringify(r.body)}`);
  });

  await step('mua: khoá nút + chu kỳ khi đang tạo phiên', async () => {
    const { ctx, page, errors } = await newPage(browser);
    let posts = 0;
    await page.route('**/api/billing/checkout', async r => { posts++; await sleep(1500); await r.continue().catch(() => {}); });
    await page.goto(BASE + '/#pricing');
    await proReady(page);
    await pickCycle(page, 'year');
    await page.click('#buyPro');
    await page.waitForTimeout(150);
    check('đang tạo phiên: nút mua + chu kỳ bị khoá', await page.isDisabled('#buyPro') && await page.isDisabled('input[name="cycle"][value="week"]'));
    await page.click('#buyPro', { force: true }).catch(() => {});
    await page.click('#cycle label:has(input[value="week"])', { force: true, timeout: 500 }).catch(() => {});
    await page.waitForURL(u => u.href.startsWith(POLAR + '/checkout/'), { timeout: 15000 });
    check('chỉ MỘT POST /api/billing/checkout dù bấm nhiều lần', posts === 1, String(posts));
    check('đến đúng phiên Pro năm ($49.99)', (await text(page, '#fakeAmount')) === '$49.99', await text(page, '#fakeAmount'));
    check('không có lỗi console (khoá nút mua)', errors.length === 0, errors.join(' | '));
    await ctx.close();
  });

  await step('trang cảm ơn: phiên chưa trả tiền, thiếu id, thanh-toan.html cũ', async () => {
    const r = await j(BASE + '/api/billing/checkout', { method: 'POST', body: JSON.stringify({ plan: 'pro_week' }) });
    const cid = r.body && r.body.url ? r.body.url.split('/').pop() : '';
    const { ctx, page, errors } = await newPage(browser);
    await page.goto(BASE + '/checkout-success.html?checkout_id=' + cid);
    await page.waitForSelector('#payCard[data-state="unpaid"]', { timeout: 15000 });
    const card = await text(page, '#payCard');
    check('phiên "open" → "This checkout hasn’t been paid"', /hasn’t been paid/.test(card), card.slice(0, 120));
    check('có nút "Choose a plan" về #pricing', await page.isVisible('#payCard a[href="./#pricing"]'));
    await page.goto(BASE + '/checkout-success.html?lang=vi');
    await page.waitForSelector('#payCard[data-state="missing"]', { timeout: 10000 });
    check('?lang=vi + thiếu checkout_id → báo bằng tiếng Việt', (await page.getAttribute('html', 'lang')) === 'vi' && !/We couldn’t/.test(await text(page, '#payCard')), (await text(page, '#payCard')).slice(0, 80));
    await page.goto(BASE + '/thanh-toan.html?checkout_id=' + cid);
    await page.waitForURL(u => u.pathname.endsWith('/checkout-success.html'), { timeout: 10000 });
    check('/thanh-toan.html chuyển sang /checkout-success.html, giữ query', page.url() === BASE + '/checkout-success.html?checkout_id=' + cid, page.url());
    check('không có lỗi console (trang cảm ơn)', errors.length === 0, errors.join(' | '));
    await ctx.close();
  });

  await step('chu kỳ giá lấy từ sản phẩm', async () => {
    const prod = (await j(POLAR + '/_fake/state')).body.products;
    const tok = polarEnv().POLAR_ACCESS_TOKEN;
    const ps = await Promise.all(Object.keys(prod).map(id => j(POLAR + '/v1/products/' + id, { headers: { Authorization: 'Bearer ' + tok } }).then(x => x.body)));
    check('Polar giả: interval ở cấp sản phẩm, giá không có recurring_interval', ps.every(p => p.recurring_interval && p.prices.every(pr => !('recurring_interval' in pr))));
  });

  await step('key dò ngẫu nhiên: sai dạng không hỏi Polar, giới hạn theo IP', async () => {
    const bad = await j(BASE + '/api/quota', { headers: { 'X-API-Key': 'khong-phai-key' } });
    check('key sai dạng → 401 invalid_api_key', bad.status === 401 && bad.body.error.code === 'invalid_api_key', String(bad.status));
    const codes = [];
    for (let i = 0; i < 25; i++) codes.push((await j(BASE + '/api/quota', { headers: { 'X-API-Key': 'DATTTS-DO-' + i + '-' + Date.now() } })).status);
    const first429 = codes.indexOf(429);
    check('quá 20 key chưa cache/phút/IP → 429 rate_limited', first429 > 0 && first429 <= 20 && codes.slice(first429).every(c => c === 429) && codes.slice(0, first429).every(c => c === 401), codes.join(','));
    const free = await j(BASE + '/api/quota');
    check('không gửi key vẫn dùng thử bình thường, period=day', free.status === 200 && free.body.period === 'day', JSON.stringify(free.body));
  });

  await step('trang pháp lý', async () => {
    const pairs = [['terms.html', 'dieu-khoan.html'], ['privacy.html', 'bao-mat.html'], ['refund.html', 'hoan-tien.html']];
    for (const [en, vi] of pairs) {
      const [he, hv] = await Promise.all([en, vi].map(p => fetch(BASE + '/' + p).then(async r => [r.status, await r.text()])));
      check(`${en} + ${vi} 200, liên kết qua lại`, he[0] === 200 && hv[0] === 200 && he[1].includes(`href="${vi}"`) && hv[1].includes(`href="${en}"`));
      if (en !== 'privacy.html') check(`${en}/${vi}: nhắc Pro tuần/tháng/năm`, /week/i.test(he[1]) && /year/i.test(he[1]) && /tuần/.test(hv[1]));
      check(`${en}/${vi}: không còn gói cũ (Cá nhân / Không giới hạn)`, !/Unlimited plan|gói Không giới hạn|gói Cá nhân/i.test(he[1] + hv[1]));
    }
    const rf = await fetch(BASE + '/refund.html').then(r => r.text());
    check('refund.html: 14 ngày / 3 ngày (gói tuần)', /14 days/.test(rf) && /3 days/.test(rf));
    const { ctx, page, errors } = await newPage(browser);
    await page.goto(BASE + '/terms.html?lang=vi');
    await page.waitForURL(u => u.pathname.endsWith('/dieu-khoan.html'), { timeout: 10000 });
    check('terms.html?lang=vi → dieu-khoan.html', page.url().includes('/dieu-khoan.html'), page.url());
    await page.click('[data-lang="en"]');
    await page.waitForURL(u => u.pathname.endsWith('/terms.html'), { timeout: 10000 });
    check('nút EN trên trang VI → terms.html', page.url().includes('/terms.html'), page.url());
    check('không có lỗi console (pháp lý)', errors.length === 0, errors.join(' | '));
    await ctx.close();
    const htmls = await Promise.all(pairs.flat().map(p => fetch(BASE + '/' + p).then(r => r.text())));
    const ph = pairs.flat().filter((p, i) => /\[Legal name\]|\[Tên pháp lý\]/.test(htmls[i]));
    if (process.env.E2E_RELEASE === '1') check('trang pháp lý đã điền tên pháp lý', ph.length === 0, ph.join(', '));
    else if (ph.length) console.log('WARN  trang pháp lý còn placeholder [Legal name]/[Tên pháp lý]: ' + ph.join(', ') + ' (E2E_RELEASE=1 để coi là lỗi)');
  });
}

// ---------- giai đoạn 2: engine thật + key trả phí ----------
async function phaseReal() {
  await polarPost('/_fake/reset', {});
  await step('engine thật + key Pro năm', async () => {
    const h = (await j(BASE + '/api/health')).body;
    check('/api/health engine thật', h.engine === 'vieneu-v3-turbo', JSON.stringify(h));
    const K = 'DATTTS-YEAR-TEST';
    const freeBefore = await freeQuota(), m0 = (await me(K)).body;
    const r = await j(BASE + '/api/tts', { method: 'POST', headers: { 'X-API-Key': K }, body: JSON.stringify({ text: `Xin chào, đây là bản đọc thử bằng gói Pro năm lúc ${new Date().toISOString().slice(11, 19)}.`, voice: 'truc-ly' }) });
    check('POST /api/tts (VieNeu) bằng key Pro năm 200', r.status === 200 && r.body.duration > 1, `${r.status} ${r.body && (r.body.duration || JSON.stringify(r.body.error))}`);
    const audio = r.body && r.body.audio_url ? await fetch(new URL(r.body.audio_url, BASE)).then(x => x.arrayBuffer()) : new ArrayBuffer(0);
    check('file âm thanh tải được', audio.byteLength > 5000, audio.byteLength + 'B');
    const m1 = (await me(K)).body, freeAfter = await freeQuota();
    check('/api/me: pro_year, 1.000.000/tháng, used tăng đúng', m1.plan === 'pro_year' && m1.limit === 1000000 && m1.period === 'month' && m1.used - m0.used === r.body.chars, `${m0.used} → ${m1.used}`);
    check('quota IP không đổi (engine thật)', freeAfter.used === freeBefore.used);
  });
}

// ---------- giai đoạn 3: billing tắt ----------
async function phaseOff(browser) {
  await step('billing tắt', async () => {
    const plans = (await j(BASE + '/api/billing/plans')).body;
    check('/api/billing/plans enabled=false', plans.enabled === false, JSON.stringify(plans).slice(0, 120));
    const co = await j(BASE + '/api/billing/checkout', { method: 'POST', body: JSON.stringify({ plan: 'pro_month' }) });
    check('checkout → 503 billing_disabled (EN)', co.status === 503 && co.body.error.code === 'billing_disabled' && /coming soon/i.test(co.body.error.message), co.body && co.body.error.message);
    const cov = await j(BASE + '/api/billing/checkout', { method: 'POST', headers: { 'Accept-Language': 'vi' }, body: JSON.stringify({ plan: 'pro_month' }) });
    check('checkout → 503 billing_disabled (VI)', cov.status === 503 && /Sắp|sắp|chưa/.test(cov.body.error.message), cov.body && cov.body.error.message);
    const { ctx, page, errors } = await newPage(browser);
    await page.goto(BASE + '/#pricing');
    await page.waitForFunction(() => /Coming soon/.test(document.querySelector('#buyPro .lbl').textContent), null, { timeout: 15000 });
    const lbl = await text(page, '#buyPro .lbl');
    check('nút hiện "Coming soon · join the waitlist"', /Coming soon/.test(lbl), lbl);
    check('giá hiện "Coming soon"', /Coming soon/.test(await text(page, '[data-plan-id="pro"] [data-price]')), await text(page, '[data-plan-id="pro"] [data-price]'));
    check('form chờ hiện', await page.isVisible('#waitlist'));
    await pickCycle(page, 'year');
    await page.click('#buyPro');
    await page.waitForTimeout(600);
    check('bấm nút không rời trang, chọn Pro năm trong form chờ', page.url().startsWith(BASE) && (await page.inputValue('#wlPlan')) === 'pro_year', page.url());
    await page.fill('#wlEmail', `wl-${Date.now()}@example.com`);
    await page.click('#wlBtn');
    await page.waitForSelector('#wlMsg.ok', { timeout: 10000 });
    const wlEn = await text(page, '#wlMsg');
    check('đăng ký chờ thành công (EN), câu dùng tên gói "Pro Yearly" (không có " · ")', /on the waitlist for Pro Yearly\./.test(wlEn) && !/ · /.test(wlEn), wlEn);
    await shot(page, 'billing-off-en.png');
    await page.click('[data-lang="vi"]');
    await page.waitForFunction(() => /Sắp mở bán/.test(document.querySelector('#buyPro .lbl').textContent), null, { timeout: 5000 });
    check('VI: "Sắp mở bán"', /Sắp mở bán/.test(await text(page, '#buyPro .lbl')));
    const wlVi = await text(page, '#wlMsg');
    check('VI: câu danh sách chờ dùng "gói Pro năm"', /danh sách chờ gói Pro năm\./.test(wlVi) && !/ · /.test(wlVi), wlVi);
    const ftVi = (await text(page, 'footer')).replace(/\s+/g, ' ');
    check('VI: chân trang "Giọng đọc: VieNeu-TTS của Phạm Nguyễn Ngọc Bảo" (không có "by")', /Giọng đọc: VieNeu-TTS của Phạm Nguyễn Ngọc Bảo/.test(ftVi) && !/ by /.test(ftVi), ftVi.slice(0, 160));
    await page.goto(BASE + '/checkout-success.html?checkout_id=00000000-0000-4000-8000-000000000000&lang=en');
    await page.waitForSelector('#payCard[data-state="disabled"]', { timeout: 15000 });
    const card = await text(page, '#payCard');
    check('trang cảm ơn báo "Pro isn’t on sale yet" (không phải lỗi kết nối)', /isn’t on sale yet/.test(card) && !/couldn’t check/.test(card), card.slice(0, 100));
    const ftEn = (await text(page, 'footer')).replace(/\s+/g, ' ');
    check('EN: chân trang "Voices: VieNeu-TTS by Phạm Nguyễn Ngọc Bảo" (một chữ "by")', /Voices: VieNeu-TTS by Phạm Nguyễn Ngọc Bảo/.test(ftEn) && (ftEn.match(/ by /g) || []).length === 1, ftEn.slice(0, 160));
    // Đổi ngôn ngữ tại chỗ: thông điệp của máy chủ được hỏi lại theo ngôn ngữ mới → không lẫn hai thứ tiếng
    await page.click('[data-lang="vi"]');
    await page.waitForFunction(() => document.querySelector('#payCard').dataset.state === 'disabled' && /sắp mở bán/i.test(document.querySelector('#payCard').textContent) && !/coming soon/i.test(document.querySelector('#payCard').textContent), null, { timeout: 10000 }).catch(() => {});
    const cardVi = await text(page, '#payCard');
    check('VI: thẻ "chưa mở bán" toàn tiếng Việt', /Gói Pro chưa mở bán/.test(cardVi) && /sắp mở bán/i.test(cardVi) && !/coming soon|If you paid|Pricing section/i.test(cardVi), cardVi.slice(0, 200));
    await page.click('[data-lang="en"]');
    await page.waitForFunction(() => document.querySelector('#payCard').dataset.state === 'disabled' && /coming soon/i.test(document.querySelector('#payCard').textContent), null, { timeout: 10000 }).catch(() => {});
    const cardEn = await text(page, '#payCard');
    check('EN: thẻ "not on sale" không còn tiếng Việt', /isn’t on sale yet/.test(cardEn) && /coming soon/i.test(cardEn) && !/[ăâđêôơư]/i.test(cardEn.replace(/support@dattts\.com/g, '')), cardEn.slice(0, 200));
    check('không có lỗi console (billing tắt)', errors.length === 0, errors.join(' | '));
    await ctx.close();
  });
}

let polar, srv;
const browser = await chromium.launch();
try {
  console.log('Dữ liệu tạm:', DATA);
  polar = await startPolar();
  srv = await startDatTTS({ billing: true, fake: true });
  await phaseOn(browser);
  await stop(srv);
  if (REAL) {
    srv = await startDatTTS({ billing: true, fake: false });
    await phaseReal();
    await stop(srv);
  } else console.log('(bỏ qua giai đoạn engine thật: E2E_REAL=0)');
  srv = await startDatTTS({ billing: false, fake: true });
  await phaseOff(browser);
} catch (e) { check('ngoại lệ', false, String(e && e.stack || e)); }
finally {
  await browser.close();
  for (const p of [...procs]) await stop(p);
}
const pass = results.filter(r => r.ok).length, fail = results.length - pass;
console.log(`\nPASS ${pass} FAIL ${fail}   (nhật ký máy chủ: ${DATA})`);
process.exit(fail ? 1 : 0);
