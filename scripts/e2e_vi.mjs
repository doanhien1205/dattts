// Kiểm tra trang chủ tiếng Việt tĩnh /vi/ (SEO). Cần máy chủ đang chạy:
//   cd backend && FREE_REQUESTS_PER_MIN=0 python server_local.py --fake --port 8765 &
//   node scripts/e2e_vi.mjs            (E2E_BASE mặc định http://127.0.0.1:8765)
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const BASE = (process.env.E2E_BASE || 'http://127.0.0.1:8765').replace(/\/+$/, '');
let pass = 0, fail = 0;
const ok = (c, name, extra = '') => { c ? pass++ : fail++; console.log(`${c ? 'PASS' : 'FAIL'}  ${name}${extra ? '  — ' + extra : ''}`); };
const b = await chromium.launch();

// 1) Bot không chạy JS vẫn thấy tiếng Việt
{
  const ctx = await b.newContext({ javaScriptEnabled: false, locale: 'en-US' });
  const p = await ctx.newPage();
  await p.goto(BASE + '/vi/');
  ok(await p.getAttribute('html', 'lang') === 'vi', 'không JS: <html lang="vi">');
  const h1 = await p.textContent('h1');
  ok(/giọng Việt/.test(h1), 'không JS: tiêu đề tiếng Việt', h1.trim());
  ok(/Phòng thu giọng đọc tiếng Việt/.test(await p.title()), 'không JS: <title> tiếng Việt');
  ok(await p.getAttribute('link[rel=canonical]', 'href') === 'https://dattts.com/vi/', 'canonical trỏ /vi/');
  ok(await p.locator('link[hreflang]').count() === 3, 'đủ 3 thẻ hreflang');
  const body = await p.innerText('body');
  const leftovers = ['Simple pricing', 'Selected voice', 'Most popular', 'Get Pro', 'How do I', 'Generate voice', 'Choose a voice', 'Download'].filter(x => body.includes(x));
  ok(leftovers.length === 0, 'không JS: không sót chuỗi tiếng Anh', leftovers.join(', '));
  ok(await p.getAttribute('footer a[data-href-vi]', 'href') === '/dieu-khoan.html', 'link pháp lý trỏ bản VI, đường dẫn tuyệt đối');
  const css = await p.getAttribute('link[rel=stylesheet][href$="styles.css"]', 'href');
  ok(css === '/styles.css', 'styles.css dùng đường dẫn tuyệt đối', css);
  await ctx.close();
  const en = await b.newContext({ javaScriptEnabled: false });
  const q = await en.newPage(); await q.goto(BASE + '/');
  ok(await q.getAttribute('html', 'lang') === 'en' && await q.locator('link[hreflang="vi"]').getAttribute('href') === 'https://dattts.com/vi/', 'trang gốc: tiếng Anh + hreflang vi');
  await en.close();
}
// 2) Có JS, trình duyệt tiếng Anh: /vi/ vẫn là tiếng Việt và dùng được
{
  const ctx = await b.newContext({ locale: 'en-US' });
  const p = await ctx.newPage();
  const errs = []; p.on('pageerror', e => errs.push(e.message)); p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  await p.goto(BASE + '/vi/');
  await p.waitForFunction(() => /25/.test(document.querySelector('#voiceCount')?.textContent || ''), null, { timeout: 15000 }).catch(() => {});
  await p.waitForTimeout(1500);
  ok(await p.evaluate(() => window.I18N.lang) === 'vi', 'có JS: I18N.lang = vi dù trình duyệt en-US');
  ok(await p.getAttribute('.langsw [data-lang="vi"]', 'aria-pressed') === 'true', 'nút VI đang bật');
  const refund = await p.getAttribute('#faq a[data-href-vi]', 'href');
  ok(refund === '/hoan-tien.html', 'link trong câu hỏi thường gặp trỏ /hoan-tien.html', refund);
  const [resp] = await Promise.all([
    p.waitForResponse(r => r.url().includes('/api/tts') && r.request().method() === 'POST', { timeout: 30000 }),
    p.click('#genBtn'),
  ]).catch(() => [null]);
  ok(resp && resp.status() === 200, 'tạo giọng đọc từ /vi/', resp ? String(resp.status()) : 'không thấy request');
  ok(resp && /^vi/.test(resp.request().headers()['accept-language'] || ''), 'API nhận Accept-Language: vi');
  ok(errs.length === 0, 'không có lỗi console', errs.join(' | ').slice(0, 200));
  await Promise.all([p.waitForURL(u => new URL(u).pathname === '/'), p.click('.langsw [data-lang="en"]')]);
  await p.waitForTimeout(500);
  ok(await p.evaluate(() => window.I18N.lang) === 'en' && await p.getAttribute('html', 'lang') === 'en', 'bấm EN → về "/" bằng tiếng Anh');
  await p.goto(BASE + '/vi/?lang=en#pricing'); await p.waitForTimeout(800);
  ok(new URL(p.url()).pathname === '/' && p.url().endsWith('#pricing'), '/vi/?lang=en chuyển về "/" giữ #pricing', p.url());
  await p.goto(BASE + '/dieu-khoan.html');
  const nav = await p.getAttribute('.navlinks a', 'href');
  ok(nav === 'vi/#studio', 'trang pháp lý tiếng Việt trỏ về /vi/', nav);
  await ctx.close();
}
// 3) Điện thoại 360px không tràn ngang
{
  const ctx = await b.newContext({ viewport: { width: 360, height: 740 } });
  const p = await ctx.newPage(); await p.goto(BASE + '/vi/'); await p.waitForTimeout(1200);
  ok(await p.evaluate(() => document.documentElement.scrollWidth) <= 360, '360px không tràn ngang');
  await ctx.close();
}
await b.close();
console.log(`\nPASS ${pass} FAIL ${fail}`);
process.exit(fail ? 1 : 0);
