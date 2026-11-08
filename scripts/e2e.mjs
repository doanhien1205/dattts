// Kiểm thử đầu-cuối DatTTS với máy chủ thật (engine VieNeu).
//   cd backend && python server_local.py --port 8765 &
//   node scripts/e2e.mjs                      # E2E_BASE mặc định http://127.0.0.1:8765
// Biến môi trường: E2E_BASE, E2E_OUT (ghi danh sách id file đã tạo ra JSON), E2E_SHOTS (thư mục ảnh chụp).
import { createRequire } from 'node:module';
import fs from 'node:fs';

const require = createRequire(import.meta.url);
let chromium;
try { ({ chromium } = require('playwright')); }
catch { ({ chromium } = require(require.resolve('playwright', { paths: [process.env.NODE_PATH || '', '/opt/node-tools/node_modules'] }))); }

const BASE = (process.env.E2E_BASE || 'http://127.0.0.1:8765').replace(/\/$/, '');
const OUT = process.env.E2E_OUT || '';
const SHOTS = process.env.E2E_SHOTS || '';
const results = [];
const generated = [];
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond, detail });
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
}
async function step(name, fn) {
  try { await fn(); } catch (e) { check(name + ' (ngoại lệ)', false, String(e && e.message || e).split('\n')[0]); }
}

async function newPage(browser, { viewport = { width: 1280, height: 900 }, scheme = 'light' } = {}) {
  const ctx = await browser.newContext({ viewport, colorScheme: scheme, acceptDownloads: true });
  // Phông Google có thể bị chặn trong môi trường kiểm thử; trả rỗng để khỏi nhiễu console.
  await ctx.route(/fonts\.(googleapis|gstatic)\.com/, r => r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
  // Ghi lại mọi src được phát (kể cả new Audio() không gắn vào DOM).
  await ctx.addInitScript(() => {
    window.__played = [];
    const orig = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function () { window.__played.push(this.currentSrc || this.src); return orig.apply(this, arguments); };
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  const reqs = [];
  page.on('request', r => { if (r.url().includes('/api/')) reqs.push({ url: r.url(), method: r.method(), body: r.postData() }); });
  return { ctx, page, errors, reqs };
}
const getJSON = async (page, path) => page.evaluate(async p => (await fetch(p)).json(), path);
const noOverflow = page => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 0.5);

// Bấm "Tạo giọng đọc" và đợi phản hồi /api/tts (hoặc job done) — trả JSON kết quả.
async function generate(page, text, { timeout = 120000 } = {}) {
  await page.fill('#txt', text);
  const respP = page.waitForResponse(r => r.url().endsWith('/api/tts') && r.request().method() === 'POST', { timeout });
  await page.click('#genBtn');
  const resp = await respP;
  const data = await resp.json();
  await page.waitForFunction(() => !document.querySelector('#genBtn').disabled, null, { timeout });
  return { status: resp.status(), data };
}

async function main(browser) {
  const { ctx, page, errors, reqs } = await newPage(browser);

  await step('trang tải', async () => {
    const r = await page.goto(BASE + '/');
    check('trang tải 200', r && r.status() === 200, String(r && r.status()));
    await page.waitForSelector('.vcard', { timeout: 20000 });
    check('không có banner ngoại tuyến', await page.isHidden('#offlineBanner'));
  });

  await step('25 giọng', async () => {
    const v = await getJSON(page, '/api/voices');
    check('/api/voices trả 25 giọng', v.voices && v.voices.length === 25, String(v.voices && v.voices.length));
    if (await page.isVisible('#moreVoices')) await page.click('#moreVoices');
    const n = (await page.$$('.vcard')).length;
    check('lưới hiển thị 25 thẻ giọng', n === 25, String(n));
  });

  await step('nghe mẫu', async () => {
    const respP = page.waitForResponse(r => /\/api\/voices\/[^/]+\/sample\.mp3/.test(r.url()), { timeout: 60000 });
    await page.click('.vcard[data-id="truc-ly"] .vplay');
    const r = await respP;
    check('mẫu giọng 200 audio/mpeg', [200, 206].includes(r.status()) && /audio\/mpeg/.test(r.headers()['content-type'] || ''), `${r.status()} ${r.headers()['content-type']}`);
    const src = await page.evaluate(() => (window.__played || []).find(s => /sample\.mp3/.test(s)) || '');
    await page.waitForTimeout(800);
    check('nút mẫu ở trạng thái đang phát', (await page.$$('.vcard[data-id="truc-ly"] .vplay.playing')).length === 1);
    check('audio có src mẫu', /sample\.mp3/.test(src), src);
    if (src) {
      const st = await page.evaluate(async u => { const r = await fetch(u); return [r.status, r.headers.get('content-type'), (await r.arrayBuffer()).byteLength]; }, src);
      check('src mẫu tải lại 200 audio/mpeg', st[0] === 200 && /audio\/mpeg/.test(st[1]) && st[2] > 1000, st.join(' '));
    }
    await page.click('.vcard[data-id="truc-ly"] .vplay').catch(() => {});
  });

  let quotaBefore = null;
  await step('quota ban đầu', async () => { quotaBefore = await getJSON(page, '/api/quota'); check('/api/quota đọc được', typeof quotaBefore.used === 'number', JSON.stringify(quotaBefore)); });

  let single;
  await step('tạo giọng (số, tiền tệ)', async () => {
    await page.click('.vcard[data-id="truc-ly"] .vpick');
    // Văn bản có số, tiền, %, ngày — thêm dấu thời gian để tránh cache giữa các lần chạy.
    const stamp = new Date().toISOString().slice(11, 19).replace(/:/g, ' giờ ');
    const text = `Doanh thu quý ba đạt 1.250.000.000 đồng, tăng 12,5% so với cùng kỳ. Giá vé chỉ 99.000đ, khoảng 5 USD. Ngày 2/9/2026 lúc 7h30 chúng tôi khai trương. Bản thử lúc ${stamp}.`;
    const { status, data } = await generate(page, text);
    single = data;
    check('POST /api/tts 200', status === 200, `${status} ${data.error ? data.error.code : ''}`);
    check('kết quả có ≥3 câu', data.segments && data.segments.length >= 3, String(data.segments && data.segments.length));
    generated.push({ id: data.id, format: data.format, duration: data.duration, kind: 'single' });
    await page.waitForSelector('#player:not([hidden])');
    await page.waitForFunction(() => { const a = document.querySelector('#audio'); return a && isFinite(a.duration) && a.duration > 0; }, null, { timeout: 15000 });
    const dur = await page.evaluate(() => document.querySelector('#audio').duration);
    check('audio nạp với duration > 0', dur > 0, `audio ${dur.toFixed(2)} s, API ${data.duration} s`);
    check('duration audio ≈ API (±0,5 s)', Math.abs(dur - data.duration) < 0.5);
    // Karaoke: tua tới giữa từng câu → đúng câu được tô.
    await page.evaluate(() => document.querySelector('#audio').pause());
    let okAll = true; const got = [];
    for (const s of data.segments) {
      const mid = (s.start + s.end) / 2;
      await page.evaluate(t => { document.querySelector('#audio').currentTime = t; }, mid);
      await page.waitForTimeout(300);
      const now = await page.$eval('#transcript .seg.now', e => +e.dataset.i).catch(() => -1);
      got.push(`${mid.toFixed(1)}s→${now}`);
      if (now !== s.index) okAll = false;
    }
    check('karaoke tô đúng câu sau khi tua', okAll, got.join(', '));
  });

  await step('tải xuống', async () => {
    const hrefs = Object.fromEntries(await page.$$eval('#downloads a', as => as.map(a => [a.dataset.fmt, a.getAttribute('href')])));
    for (const f of ['mp3', 'srt', 'vtt']) {
      const h = hrefs[f];
      if (!h) { check(`liên kết tải ${f}`, false, 'thiếu'); continue; }
      const st = await page.evaluate(async u => { const r = await fetch(u); return [r.status, r.headers.get('content-disposition') || '', (await r.arrayBuffer()).byteLength]; }, h);
      check(`tải ${f}: 200 + attachment`, st[0] === 200 && /attachment;\s*filename="dattts-[a-f0-9]{20}\.\w+"/.test(st[1]) && st[2] > 0, `${st[0]} ${st[1]} ${st[2]}B`);
    }
    const [dl] = await Promise.all([page.waitForEvent('download', { timeout: 60000 }).catch(() => null), page.click('#downloads [data-alt="wav"]')]);
    check('tải WAV (render lại định dạng khác)', !!dl && /dattts-[a-f0-9]{20}\.wav/.test(dl.suggestedFilename()), dl ? dl.suggestedFilename() : 'không có download');
    const wav = hrefs.wav || await page.getAttribute('#downloads a[data-fmt="wav"]', 'href');
    if (wav) { const m = wav.match(/([a-f0-9]{20})\.wav/); if (m) generated.push({ id: m[1], format: 'wav', kind: 'wav' }); }
  });

  await step('quota giảm', async () => {
    const q = await getJSON(page, '/api/quota');
    check('quota used tăng sau khi tạo', quotaBefore && q.used > quotaBefore.used, `${quotaBefore && quotaBefore.used} → ${q.used}`);
    check('remaining = limit − used', q.limit == null || q.remaining === q.limit - q.used, JSON.stringify(q));
    const shown = await page.textContent('#quotaText');
    check('giao diện hiện quota', /còn/.test(shown || ''), (shown || '').trim());
  });

  await step('nhiều giọng', async () => {
    const text = '[Trúc Ly] Chào anh, hôm nay mình nói về trí tuệ nhân tạo nhé. [Thiện Minh] Chào em, chủ đề này đang rất nóng. [Trúc Ly] Vậy mình bắt đầu thôi.';
    const { status, data } = await generate(page, text);
    const voices = [...new Set((data.segments || []).map(s => s.voice))];
    check('multi-voice 200', status === 200, String(status));
    check('segments có 2 voice id khác nhau', voices.length === 2 && voices.includes('truc-ly') && voices.includes('thien-minh'), voices.join(','));
    check('giao diện hiện nhãn đổi giọng', (await page.$$('#transcript .vtag')).length >= 2);
    generated.push({ id: data.id, format: data.format, duration: data.duration, kind: 'multi' });
  });

  await step('từ điển phát âm', async () => {
    await page.click('#dictPanel summary');
    await page.click('#dictAdd');
    const ins = await page.$$('#dictRows input');
    await ins[ins.length - 2].fill('KPI');
    await ins[ins.length - 1].fill('chỉ số hiệu suất');
    const before = reqs.length;
    const { status, data } = await generate(page, 'Năm nay KPI của nhóm tăng gấp đôi.');
    const req = reqs.slice(before).find(r => r.url.endsWith('/api/tts') && r.method === 'POST');
    const body = req ? JSON.parse(req.body) : {};
    const has = (body.dictionary || []).some(d => d.from === 'KPI' && d.to === 'chỉ số hiệu suất');
    check('request mang mục từ điển', has, JSON.stringify(body.dictionary));
    check('tạo với từ điển 200', status === 200, String(status));
    // Cùng văn bản, không từ điển → id khác (từ điển ảnh hưởng âm thanh).
    const other = await page.evaluate(async t => (await fetch('/api/tts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: t, voice: 'truc-ly' }) })).json(), 'Năm nay KPI của nhóm tăng gấp đôi.');
    check('từ điển đổi id kết quả', other.id && other.id !== data.id, `${data.id} vs ${other.id}`);
    // dọn mục vừa thêm
    const dels = await page.$$('#dictRows [data-del]');
    await dels[dels.length - 1].click();
  });

  await step('văn bản dài → job', async () => {
    const parts = [
      'Hà Nội mùa thu có những buổi sáng se lạnh, sương mỏng phủ trên mặt hồ.',
      'Người bán hàng rong đạp xe qua những con phố nhỏ, mang theo hương cốm làng Vòng.',
      'Ở Sài Gòn, nắng vàng rực rỡ và những cơn mưa chiều đến rồi đi rất nhanh.',
      'Đà Nẵng có bãi biển dài, cát trắng mịn, nước trong xanh quanh năm.',
      'Miền Tây sông nước với chợ nổi, vườn trái cây và những câu hò ngọt ngào.',
      'Tây Nguyên đại ngàn, cà phê nở trắng đồi vào mùa xuân.',
    ];
    // Mở đầu khác nhau mỗi lần chạy để không trúng cache (cache → job xong ngay, không có tiến độ).
    let long = `Bản thử số ${Date.now() % 100000}.\n\n`; let i = 1;
    while (long.length < 3300) { long += `Đoạn ${i}. ` + parts[i % parts.length] + ' ' + parts[(i + 2) % parts.length] + '\n\n'; i++; }
    await page.fill('#txt', long);
    check('chế độ chạy nền hiển thị', /chạy nền/.test(await page.textContent('#mode')), (await page.textContent('#mode')).trim());
    const before = reqs.length;
    const jobRespP = page.waitForResponse(r => r.url().endsWith('/api/jobs') && r.request().method() === 'POST', { timeout: 30000 });
    await page.click('#genBtn');
    const jr = await jobRespP;
    check('POST /api/jobs 202', jr.status() === 202, String(jr.status()));
    const seen = new Set(); let last = '';
    const t0 = Date.now();
    while (Date.now() - t0 < 480000) {
      await page.waitForTimeout(1000);
      const s = await page.evaluate(() => [document.querySelector('#progress').hidden, document.querySelector('#progressText').textContent]);
      if (!s[0]) { seen.add(s[1]); last = s[1]; }
      if (!(await page.evaluate(() => document.querySelector('#genBtn').disabled))) break;
    }
    const polls = reqs.slice(before).filter(r => /\/api\/jobs\/[\w-]+$/.test(r.url));
    check('job được hỏi tiến độ nhiều lần', polls.length >= 3, String(polls.length));
    check('thấy tiến độ tăng dần', seen.size >= 3, [...seen].slice(0, 4).join(' ; ') + ' … ' + last);
    const jid = polls.length && polls[polls.length - 1].url.split('/').pop();
    const js = jid ? await getJSON(page, '/api/jobs/' + jid) : {};
    check('job status done, progress 1', js.status === 'done' && js.progress === 1, `${js.status} ${js.progress} ${js.done}/${js.total}`);
    const nseg = (await page.$$('#transcript .seg')).length;
    check('transcript job có đủ câu', js.result && nseg === js.result.segments.length && nseg > 20, String(nseg));
    if (js.result) generated.push({ id: js.result.id, format: js.result.format, duration: js.result.duration, kind: 'job' });
    console.log(`      job ${js.chars || (js.result && js.result.chars)} ký tự, ${js.result && js.result.duration}s audio, ${((Date.now() - t0) / 1000).toFixed(0)}s`);
  });

  await step('waitlist', async () => {
    // Billing bật (có POLAR_ACCESS_TOKEN) thì nút mua dẫn sang Polar, form chờ ẩn — phần đó do e2e_billing.mjs kiểm.
    const plans = await getJSON(page, '/api/billing/plans').catch(() => ({}));
    if (plans.enabled) {
      check('billing bật: nút "Mua gói" thay cho form chờ', /Mua gói/.test(await page.textContent('[data-buy="unlimited"] .lbl')) && await page.isHidden('#waitlist'));
      return;
    }
    await page.click('[data-plan="khong-gioi-han"]');
    const email = `e2e-${Date.now()}@example.vn`;
    await page.fill('#wlEmail', email);
    await page.click('#wlBtn');
    await page.waitForSelector('#wlMsg.ok', { timeout: 10000 });
    check('đăng ký chờ thành công', /người thứ \d+/.test(await page.textContent('#wlMsg')), (await page.textContent('#wlMsg')).trim());
  });

  if (SHOTS) await page.screenshot({ path: SHOTS + '/e2e-desktop.png', fullPage: false }).catch(() => {});
  const errs = errors.filter(e => !/favicon/.test(e));
  check('không có lỗi console (desktop)', errs.length === 0, errs.join(' | '));
  await ctx.close();
}

async function mobileDark(browser) {
  await step('mobile 390px', async () => {
    const { ctx, page, errors } = await newPage(browser, { viewport: { width: 390, height: 844 }, scheme: 'light' });
    await page.goto(BASE + '/');
    await page.waitForSelector('.vcard', { timeout: 20000 });
    check('mobile 390px không tràn ngang', await noOverflow(page));
    await page.evaluate(() => scrollTo(0, document.body.scrollHeight));
    check('mobile cuối trang không tràn ngang', await noOverflow(page));
    if (SHOTS) await page.screenshot({ path: SHOTS + '/e2e-mobile.png', fullPage: true }).catch(() => {});
    check('không có lỗi console (mobile)', errors.length === 0, errors.join(' | '));
    await ctx.close();
  });
  await step('dark mode', async () => {
    const { ctx, page, errors } = await newPage(browser, { scheme: 'dark' });
    await page.goto(BASE + '/');
    await page.waitForSelector('.vcard', { timeout: 20000 });
    const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    const lum = (() => { const m = bg.match(/\d+(\.\d+)?/g).map(Number); return 0.2126 * m[0] + 0.7152 * m[1] + 0.0722 * m[2]; })();
    check('dark mode: nền tối', lum < 60, bg);
    const fg = await page.evaluate(() => getComputedStyle(document.querySelector('h1') || document.body).color);
    const fl = (() => { const m = fg.match(/\d+(\.\d+)?/g).map(Number); return 0.2126 * m[0] + 0.7152 * m[1] + 0.0722 * m[2]; })();
    check('dark mode: chữ sáng', fl > 150, fg);
    await page.click('#themeBtn');
    const bg2 = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    check('nút đổi giao diện chuyển sang sáng', bg2 !== bg, `${bg} → ${bg2}`);
    if (SHOTS) await page.screenshot({ path: SHOTS + '/e2e-dark.png' }).catch(() => {});
    check('không có lỗi console (dark)', errors.length === 0, errors.join(' | '));
    await ctx.close();
  });
}

const browser = await chromium.launch();
try {
  const h = await fetch(BASE + '/api/health').then(r => r.json());
  check('/api/health ok, engine thật', h.ok && h.engine === 'vieneu-v3-turbo' && h.voices === 25, JSON.stringify(h));
  await main(browser);
  await mobileDark(browser);
} catch (e) { check('ngoại lệ', false, String(e && e.stack || e)); }
await browser.close();
if (OUT) fs.writeFileSync(OUT, JSON.stringify(generated, null, 2));
const pass = results.filter(r => r.ok).length, fail = results.length - pass;
console.log(`\nPASS ${pass} FAIL ${fail}`);
process.exit(fail ? 1 : 0);
