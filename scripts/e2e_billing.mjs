// Kiểm thử đầu-cuối phần THANH TOÁN (Polar) của DatTTS — dùng Polar GIẢ (scripts/fake_polar.py), không gọi polar.sh.
//
// Script tự bật/tắt máy chủ: Polar giả ở cổng 8899, DatTTS (server_local.py) ở cổng 8765 với dữ liệu tạm.
//   PYTHON=backend/.venv/bin/python node scripts/e2e_billing.mjs
// Các giai đoạn:
//   1. billing BẬT, engine giả: bảng giá, mua → trang trả tiền giả → trang cảm ơn, nhập key, tạo giọng bằng
//      hạn mức tháng, cổng khách hàng, key bị thu hồi, webhook subscription.revoked xoá cache ngay.
//   2. billing BẬT, engine VieNeu thật: một lần /api/tts bằng key trả phí (bỏ qua nếu E2E_REAL=0).
//   3. billing TẮT (không có POLAR_ACCESS_TOKEN): bảng giá "Sắp mở bán", checkout 503.
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
async function startDatTTS({ billing, fake }) {
  const env = { PUBLIC_BASE_URL: BASE, ADMIN_TOKEN: 'e2e-admin', POLAR_ACCESS_TOKEN: '', POLAR_API_BASE: '' };
  if (billing) Object.assign(env, polarEnv());
  const args = ['server_local.py', '--port', String(PORT), '--data-dir', path.join(DATA, billing ? (fake ? 'on' : 'on-real') : 'off')];
  if (fake) args.push('--fake');
  const p = start('dattts', args, env, path.join(REPO, 'backend'), path.join(DATA, 'dattts.log'));
  await waitUp(BASE + '/api/health');
  return p;
}

// ---------- tiện ích trình duyệt ----------
async function newPage(browser, { viewport = { width: 1280, height: 900 } } = {}) {
  const ctx = await browser.newContext({ viewport });
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
  return { status: r.status, body };
};
const me = key => j(BASE + '/api/me', { headers: { 'X-API-Key': key } });
const freeQuota = () => j(BASE + '/api/quota').then(r => r.body);
const polarPost = (p, body) => j(POLAR + p, { method: 'POST', body: JSON.stringify(body) });
const shot = (page, name) => SHOTS ? page.screenshot({ path: path.join(SHOTS, name), fullPage: false }).catch(() => {}) : null;
const text = (page, sel) => page.textContent(sel).then(t => (t || '').replace(/\s+/g, ' ').trim());

// ---------- giai đoạn 1: billing bật, engine giả ----------
async function phaseOn(browser) {
  await polarPost('/_fake/reset', {});

  await step('bảng giá không chờ giọng / không rơi về danh sách chờ khi đang tải', async () => {
    // /api/voices chậm 5 s (Modal khởi động lạnh): bảng giá vẫn phải hiện nút mua ngay.
    {
      const { ctx, page, errors } = await newPage(browser);
      await page.route('**/api/voices', async r => { await sleep(5000); await r.continue().catch(() => {}); });
      const t0 = Date.now();
      await page.goto(BASE + '/#bang-gia');
      await page.waitForFunction(() => /Mua gói/.test(document.querySelector('[data-buy="personal"] .lbl').textContent), null, { timeout: 4000 });
      check('giọng tải chậm 5 s: nút "Mua gói" hiện trước khi giọng xong', Date.now() - t0 < 4500 && !(await page.$('.vcard')), `${Date.now() - t0} ms`);
      check('giọng tải chậm: form chờ ẩn', await page.isHidden('#waitlist'));
      await page.unrouteAll({ behavior: 'ignoreErrors' }).catch(() => {});
      await ctx.close();
    }
    // /api/billing/plans chậm: nút ở trạng thái "Đang tải…", bấm không đưa sang danh sách chờ.
    {
      const { ctx, page, errors } = await newPage(browser);
      await page.route('**/api/billing/plans', async r => { await sleep(3000); await r.continue().catch(() => {}); });
      await page.goto(BASE + '/#bang-gia');
      const lbl = await text(page, '[data-buy="personal"] .lbl');
      check('khi chưa biết billing: nút "Đang tải…" + aria-disabled', /Đang tải/.test(lbl) && (await page.getAttribute('[data-buy="personal"]', 'aria-disabled')) === 'true', lbl);
      check('khi chưa biết billing: form chờ ẩn', await page.isHidden('#waitlist'));
      // Playwright coi aria-disabled là chưa bấm được và sẽ chờ → bấm thẳng bằng DOM như người dùng.
      await page.evaluate(() => document.querySelector('[data-buy="personal"]').click());
      await page.waitForTimeout(300);
      check('bấm khi đang tải: không focus form chờ, không rời trang', page.url().startsWith(BASE) && !(await page.evaluate(() => document.activeElement && document.activeElement.id === 'wlEmail')), page.url());
      await page.waitForFunction(() => /Mua gói/.test(document.querySelector('[data-buy="personal"] .lbl').textContent), null, { timeout: 10000 });
      check('sau khi tải: nút "Mua gói", hết aria-disabled', (await page.getAttribute('[data-buy="personal"]', 'aria-disabled')) === null);
      check('không có lỗi console (tải bảng giá)', errors.length === 0, errors.join(' | '));
      await ctx.close();
    }
  });

  await step('bảng giá', async () => {
    const plans = (await j(BASE + '/api/billing/plans')).body;
    check('/api/billing/plans enabled + giá từ Polar', plans.enabled === true && plans.plans.find(p => p.id === 'personal').price === 299 && plans.plans.find(p => p.id === 'unlimited').price === 999, JSON.stringify(plans.plans.map(p => [p.id, p.price])));
    const { ctx, page, errors } = await newPage(browser);
    await page.goto(BASE + '/#bang-gia');
    await page.waitForFunction(() => /\$/.test(document.querySelector('[data-plan-id="personal"] [data-price]').textContent), null, { timeout: 15000 });
    const pp = await text(page, '[data-plan-id="personal"] [data-price]'), pu = await text(page, '[data-plan-id="unlimited"] [data-price]');
    check('thẻ Cá nhân hiện $2.99 / tháng', /\$2\.99/.test(pp) && /tháng/.test(pp), pp);
    check('thẻ Không giới hạn hiện $9.99 / tháng', /\$9\.99/.test(pu), pu);
    const vnd = await text(page, '[data-plan-id="personal"] [data-vnd]');
    check('quy đổi tham khảo ≈ 76.000đ', /76\.000đ/.test(vnd) && /tham khảo/.test(vnd), vnd);
    const lbl = await text(page, '[data-buy="personal"] .lbl');
    check('nút "Mua gói Cá nhân"', lbl === 'Mua gói Cá nhân', lbl);
    check('form chờ ẩn khi đã mở bán', await page.isHidden('#waitlist'));
    const q = await text(page, '[data-plan-id="personal"] [data-quota]');
    check('hạn mức gói Cá nhân 1.000.000 ký tự/tháng', /1\.000\.000/.test(q), q);
    await shot(page, 'billing-pricing.png');

    // ----- mua: trang Polar giả → trang cảm ơn -----
    await page.click('[data-buy="personal"]');
    await page.waitForURL(u => u.href.startsWith(POLAR + '/checkout/'), { timeout: 15000 });
    check('bấm mua → chuyển sang trang thanh toán Polar (giả)', page.url().startsWith(POLAR + '/checkout/'), page.url());
    check('trang thanh toán đúng giá $2.99', /\$2\.99/.test(await text(page, '#fakeAmount')));
    const buyer = `mua-${Date.now()}@example.vn`;
    await page.fill('#fakeEmail', buyer);
    await page.click('#fakePay');
    await page.waitForURL(/\/thanh-toan\.html\?checkout_id=[0-9a-f-]{36}$/, { timeout: 15000 });
    check('Polar chuyển về success_url với {CHECKOUT_ID} đã thay', /thanh-toan\.html\?checkout_id=[0-9a-f-]{36}$/.test(page.url()), page.url());
    await page.waitForSelector('#payCard[data-state="succeeded"]', { timeout: 20000 });
    const card = await text(page, '#payCard');
    check('trang cảm ơn báo thành công', /Thanh toán thành công/.test(card), card.slice(0, 80));
    check('trang cảm ơn nêu gói Cá nhân + email che bớt', /Cá nhân/.test(card) && card.includes('m***@example.vn'), card.slice(0, 200));
    await shot(page, 'billing-success.png');

    // key mới Polar "gửi qua email" → dán ngay tại trang cảm ơn
    const outbox = (await j(POLAR + '/_fake/outbox')).body.emails;
    const mail = outbox.find(m => m.to === buyer);
    check('Polar giả đã cấp key DATTTS cho người mua', mail && /^DATTTS-/.test(mail.key), mail && mail.key.slice(0, 7));
    if (mail) {
      await page.fill('#keyInput', mail.key);
      await page.click('#keySave');
      await page.waitForSelector('#keyMsg.ok', { timeout: 10000 });
      const msg = await text(page, '#keyMsg');
      check('dán key ở trang cảm ơn → lưu, gói Cá nhân 1.000.000 ký tự', /Cá nhân/.test(msg) && /1\.000\.000/.test(msg), msg);
    }
    // webhook order.paid/subscription.created từ Polar giả tới DatTTS
    let ev = {};
    for (let i = 0; i < 20; i++) { ev = (await j(BASE + '/api/admin/billing', { headers: { Authorization: 'Bearer e2e-admin' } })).body || {}; if ((ev.events_by_type || {})['subscription.created']) break; await sleep(250); }
    check('DatTTS nhận webhook order.paid + subscription.created (chữ ký đúng)', (ev.events_by_type || {})['order.paid'] >= 1 && (ev.events_by_type || {})['subscription.created'] >= 1, JSON.stringify(ev.events_by_type));
    check('không có lỗi console (mua hàng)', errors.length === 0, errors.join(' | '));
    await ctx.close();
  });

  await step('nhập key trong phòng thu + tạo giọng', async () => {
    const { ctx, page, errors } = await newPage(browser);
    await page.goto(BASE + '/#studio');
    await page.waitForSelector('.vcard', { timeout: 20000 });
    await page.click('#keyBtn');
    await page.fill('#keyInput', 'DATTTS-PERSONAL-TEST');
    await page.click('#keySave');
    await page.waitForSelector('#acctBar:not([hidden]) #planBadge.on', { timeout: 10000 });
    const badge = await text(page, '#planBadge'), usage = await text(page, '#acctUsage');
    check('phòng thu hiện "Gói Cá nhân"', badge === 'Gói Cá nhân', badge);
    check('phòng thu hiện hạn mức tháng 1.000.000', /\/ 1\.000\.000 ký tự/.test(usage) && /a\*\*\*@example\.com/.test(usage), usage);
    check('nút "Quản lý gói" hiện', await page.isVisible('#portalBtn'));
    const stored = await page.evaluate(() => localStorage.getItem('dattts.apikey'));
    check('key lưu trong localStorage', stored === JSON.stringify('DATTTS-PERSONAL-TEST'), stored);
    await page.waitForFunction(() => /Tháng này còn/.test(document.querySelector('#quotaText').textContent), null, { timeout: 10000 });
    check('ô quota chuyển sang hạn mức tháng', true, await text(page, '#quotaText'));

    const freeBefore = await freeQuota(), meBefore = (await me('DATTTS-PERSONAL-TEST')).body;
    const t = `Gói trả phí đọc thử lúc ${Date.now()}. Cảm ơn bạn đã ủng hộ DatTTS.`;
    await page.fill('#txt', t);
    const respP = page.waitForResponse(r => r.url().endsWith('/api/tts') && r.request().method() === 'POST', { timeout: 60000 });
    await page.click('#genBtn');
    const resp = await respP;
    const data = await resp.json();
    check('tạo giọng bằng key trả phí 200', resp.status() === 200, String(resp.status()));
    check('request mang header X-API-Key', resp.request().headers()['x-api-key'] === 'DATTTS-PERSONAL-TEST');
    const freeAfter = await freeQuota(), meAfter = (await me('DATTTS-PERSONAL-TEST')).body;
    check('hạn mức tháng của key tăng đúng số ký tự', meAfter.used - meBefore.used === data.chars && data.chars > 0, `${meBefore.used} → ${meAfter.used} (${data.chars} ký tự)`);
    check('quota miễn phí theo IP không đổi', freeAfter.used === freeBefore.used, `${freeBefore.used} → ${freeAfter.used}`);
    await page.waitForFunction(n => new RegExp('đã dùng ' + n.toLocaleString('de-DE')).test(document.querySelector('#acctUsage').textContent), meAfter.used, { timeout: 10000 }).catch(() => {});
    check('thanh tài khoản cập nhật số đã dùng', (await text(page, '#acctUsage')).includes('đã dùng ' + meAfter.used.toLocaleString('de-DE')), await text(page, '#acctUsage'));
    await shot(page, 'billing-studio.png');

    // ----- Quản lý gói → cổng khách hàng Polar -----
    await page.click('#portalBtn');
    await page.waitForURL(u => u.href.startsWith(POLAR + '/portal?'), { timeout: 15000 });
    check('"Quản lý gói" mở customer_portal_url của Polar', page.url().startsWith(POLAR + '/portal?customer_session_token='), page.url());
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
    check('key thu hồi → thông báo rõ', /không còn hợp lệ/.test(al), al.slice(0, 90));
    check('key thu hồi bị gỡ khỏi localStorage', (await page.evaluate(() => localStorage.getItem('dattts.apikey'))) === null);
    check('thanh tài khoản ẩn, quay về lượt miễn phí', await page.isHidden('#acctBar') && /Hôm nay còn/.test(await text(page, '#quotaText')), await text(page, '#quotaText'));
    // Nhập trực tiếp key thu hồi trong hộp → báo lỗi, không lưu
    await page.click('#keyBtn');
    await page.fill('#keyInput', 'DATTTS-REVOKED-TEST');
    await page.click('#keySave');
    await page.waitForSelector('#keyMsg.err', { timeout: 10000 });
    check('hộp nhập key báo key sai', /không đúng|hết hạn/.test(await text(page, '#keyMsg')), await text(page, '#keyMsg'));
    check('không có lỗi console (key thu hồi)', errors.length === 0, errors.join(' | '));
    await ctx.close();
  });

  await step('webhook subscription.revoked', async () => {
    const K = 'DATTTS-PERSONAL-TEST';
    check('key còn hợp lệ trước khi huỷ', (await me(K)).status === 200);
    // Polar thu hồi key nhưng CHƯA báo → DatTTS vẫn dùng cache 10 phút
    await polarPost('/_fake/revoke', { key: K });
    check('chưa có webhook → cache vẫn cho qua', (await me(K)).status === 200);
    const bad = (await polarPost('/_fake/webhook', { type: 'subscription.revoked', key: K, bad_signature: true })).body;
    check('webhook ký sai → 403', bad.status_code === 403, JSON.stringify(bad.body));
    check('webhook ký sai không xoá cache', (await me(K)).status === 200);
    const wh = (await polarPost('/_fake/webhook', { type: 'subscription.revoked', key: K, revoke: true })).body;
    check('webhook subscription.revoked → 202', wh.status_code === 202, JSON.stringify(wh.body));
    const after = await me(K);
    check('key ngừng hoạt động ngay sau webhook', after.status === 401 && after.body.error.code === 'invalid_api_key', JSON.stringify(after.body));
    // Phòng thu đang giữ key này → lần tạo kế tiếp báo lỗi và gỡ key
    const { ctx, page } = await newPage(browser);
    await ctx.addInitScript(k => { if (!sessionStorage.getItem('seeded')) { localStorage.setItem('dattts.apikey', JSON.stringify(k)); sessionStorage.setItem('seeded', '1'); } }, K);
    await page.goto(BASE + '/#studio');
    await page.waitForSelector('#alert:not([hidden])', { timeout: 15000 });
    check('phòng thu báo key hết hiệu lực và gỡ key', (await page.evaluate(() => localStorage.getItem('dattts.apikey'))) === null, await text(page, '#alert'));
    await ctx.close();
    const repeat = await j(BASE + '/api/billing/webhook', { method: 'POST', body: '{}' });
    check('webhook không chữ ký → 403', repeat.status === 403, String(repeat.status));
  });

  await step('checkout API', async () => {
    const bad = await j(BASE + '/api/billing/checkout/khong-phai-uuid');
    check('checkout_id sai dạng → 400', bad.status === 400, String(bad.status));
    const nf = await j(BASE + '/api/billing/checkout/00000000-0000-4000-8000-000000000000');
    check('checkout không tồn tại → 404', nf.status === 404, String(nf.status));
    const r = await j(BASE + '/api/billing/checkout', { method: 'POST', body: JSON.stringify({ plan: 'unlimited' }) });
    check('POST checkout unlimited → url Polar', r.status === 200 && r.body.url.startsWith(POLAR + '/checkout/'), JSON.stringify(r.body));
  });

  await step('mua: khoá mọi nút khi đang tạo phiên', async () => {
    const { ctx, page, errors } = await newPage(browser);
    let posts = 0;
    await page.route('**/api/billing/checkout', async r => { posts++; await sleep(1500); await r.continue().catch(() => {}); });
    await page.goto(BASE + '/#bang-gia');
    await page.waitForFunction(() => /Mua gói/.test(document.querySelector('[data-buy="personal"] .lbl').textContent), null, { timeout: 15000 });
    await page.click('[data-buy="personal"]');
    await page.waitForTimeout(150);
    check('đang tạo phiên: nút gói kia bị khoá', await page.isDisabled('[data-buy="unlimited"]') && (await page.getAttribute('[data-buy="unlimited"]', 'aria-disabled')) === 'true');
    await page.click('[data-buy="unlimited"]', { force: true }).catch(() => {});
    await page.click('[data-buy="personal"]', { force: true }).catch(() => {});
    await page.waitForURL(u => u.href.startsWith(POLAR + '/checkout/'), { timeout: 15000 });
    check('chỉ MỘT POST /api/billing/checkout dù bấm 3 lần', posts === 1, String(posts));
    check('đến đúng phiên gói Cá nhân ($2.99)', /\$2\.99/.test(await text(page, '#fakeAmount')), await text(page, '#fakeAmount'));
    check('không có lỗi console (khoá nút mua)', errors.length === 0, errors.join(' | '));
    await ctx.close();
  });

  await step('trang cảm ơn với phiên chưa trả tiền', async () => {
    const r = await j(BASE + '/api/billing/checkout', { method: 'POST', body: JSON.stringify({ plan: 'personal' }) });
    const cid = r.body && r.body.url ? r.body.url.split('/').pop() : '';
    const { ctx, page, errors } = await newPage(browser);
    await page.goto(BASE + '/thanh-toan.html?checkout_id=' + cid);
    await page.waitForSelector('#payCard[data-state="unpaid"]', { timeout: 15000 });
    const card = await text(page, '#payCard');
    check('phiên "open" → "Phiên này chưa được thanh toán"', /chưa được thanh toán/.test(card) && !/không cần thanh toán lại/.test(card), card.slice(0, 120));
    check('có nút chọn gói lại', await page.isVisible('#payCard a[href="./#bang-gia"]'));
    check('không có lỗi console (phiên chưa trả)', errors.length === 0, errors.join(' | '));
    await ctx.close();
  });

  await step('email Polar không nhận → 400 bad_email (không phải "gián đoạn")', async () => {
    const r = await j(BASE + '/api/billing/checkout', { method: 'POST', body: JSON.stringify({ plan: 'personal', email: 'an@gmail.con' }) });
    check('checkout email tên miền hỏng → 400 bad_email', r.status === 400 && r.body.error.code === 'bad_email', `${r.status} ${JSON.stringify(r.body)}`);
  });

  await step('chu kỳ giá lấy từ sản phẩm', async () => {
    const prod = (await j(POLAR + '/_fake/state')).body.products;
    const anyPriceInterval = Object.keys(prod).length && (await Promise.all(Object.keys(prod).map(id =>
      j(POLAR + '/v1/products/' + id, { headers: { Authorization: 'Bearer ' + polarEnv().POLAR_ACCESS_TOKEN } }).then(x => x.body)))).some(p => p.prices.some(pr => 'recurring_interval' in pr));
    check('Polar giả: giá không còn recurring_interval (schema hiện tại)', !anyPriceInterval);
    const plans = (await j(BASE + '/api/billing/plans')).body;
    check('/api/billing/plans interval = "month" (từ Product.recurring_interval)', plans.plans.filter(p => p.id !== 'free').every(p => p.interval === 'month'), JSON.stringify(plans.plans.map(p => p.interval)));
  });

  await step('key dò ngẫu nhiên: sai dạng không hỏi Polar, giới hạn theo IP', async () => {
    const bad = await j(BASE + '/api/quota', { headers: { 'X-API-Key': 'khong-phai-key' } });
    check('key sai dạng → 401 invalid_api_key', bad.status === 401 && bad.body.error.code === 'invalid_api_key', String(bad.status));
    const codes = [];
    for (let i = 0; i < 25; i++) codes.push((await j(BASE + '/api/quota', { headers: { 'X-API-Key': 'DATTTS-DO-' + i + '-' + Date.now() } })).status);
    // Các bước trước cũng đã hỏi Polar trong phút này (cùng IP) nên ngưỡng có thể đến sớm hơn 20.
    const first429 = codes.indexOf(429);
    check('quá 20 key chưa cache/phút/IP → 429 rate_limited', first429 > 0 && first429 <= 20 && codes.slice(first429).every(c => c === 429) && codes.slice(0, first429).every(c => c === 401), codes.join(','));
    const free = await j(BASE + '/api/quota');
    check('không gửi key vẫn dùng thử bình thường', free.status === 200, String(free.status));
  });

  await step('trang pháp lý', async () => {
    const pages = ['dieu-khoan.html', 'bao-mat.html', 'hoan-tien.html'];
    const htmls = await Promise.all(pages.map(p => fetch(BASE + '/' + p).then(r => r.text())));
    check('bao-mat không còn hứa "xoá sau 30 ngày" / "24 tháng"', !/30 ngày|30 days|24 tháng|24 months/.test(htmls[1]));
    const ph = pages.filter((p, i) => htmls[i].includes('[Tên chủ sở hữu / doanh nghiệp]'));
    if (process.env.E2E_RELEASE === '1') check('trang pháp lý đã điền tên pháp lý', ph.length === 0, ph.join(', '));
    else if (ph.length) console.log('WARN  trang pháp lý còn chỗ trống "[Tên chủ sở hữu / doanh nghiệp]": ' + ph.join(', ') + ' (E2E_RELEASE=1 để coi là lỗi)');
  });
}

// ---------- giai đoạn 2: engine thật + key trả phí ----------
async function phaseReal() {
  await polarPost('/_fake/reset', {});
  await step('engine thật + key trả phí', async () => {
    const h = (await j(BASE + '/api/health')).body;
    check('/api/health engine thật', h.engine === 'vieneu-v3-turbo', JSON.stringify(h));
    const K = 'DATTTS-UNLIMITED-TEST';
    const freeBefore = await freeQuota(), m0 = (await me(K)).body;
    const r = await j(BASE + '/api/tts', { method: 'POST', headers: { 'X-API-Key': K }, body: JSON.stringify({ text: `Xin chào, đây là bản đọc thử bằng gói Không giới hạn lúc ${new Date().toISOString().slice(11, 19)}.`, voice: 'truc-ly' }) });
    check('POST /api/tts (VieNeu) bằng key Không giới hạn 200', r.status === 200 && r.body.duration > 1, `${r.status} ${r.body && (r.body.duration || JSON.stringify(r.body.error))}`);
    const audio = r.body && r.body.audio_url ? await fetch(new URL(r.body.audio_url, BASE)).then(x => x.arrayBuffer()) : new ArrayBuffer(0);
    check('file âm thanh tải được', audio.byteLength > 5000, audio.byteLength + 'B');
    const m1 = (await me(K)).body, freeAfter = await freeQuota();
    check('/api/me: gói Không giới hạn, used tăng đúng', m1.plan === 'unlimited' && m1.limit === 20000000 && m1.used - m0.used === r.body.chars, `${m0.used} → ${m1.used}`);
    check('quota IP không đổi (engine thật)', freeAfter.used === freeBefore.used);
  });
}

// ---------- giai đoạn 3: billing tắt ----------
async function phaseOff(browser) {
  await step('billing tắt', async () => {
    const plans = (await j(BASE + '/api/billing/plans')).body;
    check('/api/billing/plans enabled=false', plans.enabled === false, JSON.stringify(plans).slice(0, 120));
    const co = await j(BASE + '/api/billing/checkout', { method: 'POST', body: JSON.stringify({ plan: 'personal' }) });
    check('checkout → 503 billing_disabled', co.status === 503 && co.body.error.code === 'billing_disabled', String(co.status));
    const { ctx, page, errors } = await newPage(browser);
    await page.goto(BASE + '/#bang-gia');
    await page.waitForFunction(() => /Sắp mở bán/.test(document.querySelector('[data-plan-id="personal"] [data-price]').textContent), null, { timeout: 15000 });
    const lbl = await text(page, '[data-buy="personal"] .lbl');
    check('nút hiện "Sắp mở bán"', /Sắp mở bán/.test(lbl), lbl);
    check('giá hiện "Sắp mở bán"', /Sắp mở bán/.test(await text(page, '[data-plan-id="unlimited"] [data-price]')));
    check('form chờ hiện', await page.isVisible('#waitlist'));
    await page.click('[data-buy="unlimited"]');
    await page.waitForTimeout(600);
    check('bấm nút không rời trang, chọn gói trong form chờ', page.url().startsWith(BASE) && (await page.inputValue('#wlPlan')) === 'khong-gioi-han', page.url());
    await shot(page, 'billing-off.png');
    await page.goto(BASE + '/thanh-toan.html?checkout_id=00000000-0000-4000-8000-000000000000');
    await page.waitForSelector('#payCard[data-state="disabled"]', { timeout: 15000 });
    check('trang cảm ơn báo chưa mở bán (không phải lỗi kết nối)', /Gói trả phí chưa mở bán/.test(await text(page, '#payCard')) && !/Lỗi kết nối/.test(await text(page, '#payCard')), (await text(page, '#payCard')).slice(0, 100));
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
