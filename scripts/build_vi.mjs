#!/usr/bin/env node
// Dựng web/vi/index.html: bản tiếng Việt TĨNH của trang chủ, để công cụ tìm kiếm lập chỉ mục tiếng Việt
// ở https://dattts.com/vi/ (trang gốc "/" là tiếng Anh, đổi ngôn ngữ bằng JS nên bot chỉ thấy tiếng Anh).
//
//   node scripts/build_vi.mjs           # ghi web/vi/index.html
//   node scripts/build_vi.mjs --check   # thoát mã 1 nếu file đang lệch so với index.html + i18n.js (dùng trong CI)
//
// Nguồn: web/index.html (đánh dấu data-i18n / data-i18n-html / data-i18n-attr / data-href-vi / data-ssg-vi) + chuỗi VI
// trong web/i18n.js. Sửa một trong hai file đó thì chạy lại script này. Không phụ thuộc gói ngoài.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const SRC = path.join(ROOT, 'web', 'index.html');
const OUT = path.join(ROOT, 'web', 'vi', 'index.html');
const SITE = 'https://dattts.com';

// ── Lấy từ điển từ i18n.js (chạy trong sandbox với window/document giả) ──
function loadDict() {
  const noop = () => {};
  const el = { getAttribute: () => null, setAttribute: noop, lang: 'en' };
  const sandbox = {
    window: {}, navigator: { language: 'en-US', languages: ['en-US'] },
    location: { href: SITE + '/', search: '', hash: '', pathname: '/', replace: noop },
    history: { replaceState: noop, state: null },
    localStorage: { getItem: () => null, setItem: noop },
    document: { documentElement: el, querySelectorAll: () => [], addEventListener: noop, dispatchEvent: noop },
    URL, Intl, CustomEvent: function () {},
  };
  sandbox.window = sandbox;
  vm.runInNewContext(fs.readFileSync(path.join(ROOT, 'web', 'i18n.js'), 'utf8'), sandbox, { filename: 'i18n.js' });
  const { en, vi } = sandbox.I18N || {};
  if (!en || !vi) throw new Error('không đọc được I18N từ web/i18n.js');
  return { en, vi };
}

const escText = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const escAttr = s => escText(s).replace(/"/g, '&quot;');
const isRelative = u => !!u && !/^(#|\/|[a-z][a-z0-9+.-]*:)/i.test(u);
const toAbs = u => (isRelative(u) ? '/' + u.replace(/^\.\//, '') : u);

function getAttr(tag, name) {
  const m = new RegExp(`\\s${name}="([^"]*)"`).exec(tag);
  return m ? m[1] : null;
}
function setAttr(tag, name, value) {
  const re = new RegExp(`(\\s${name}=")[^"]*(")`);
  if (re.test(tag)) return tag.replace(re, (_, a, b) => a + escAttr(value) + b);
  return tag.replace(/(\s*\/?>)$/, ` ${name}="${escAttr(value)}"$1`);
}
// Đường dẫn tương đối trong một đoạn HTML (chuỗi data-i18n-html) → tuyệt đối, và link hai bản → bản VI.
function fixInnerLinks(html) {
  return html.replace(/<a\b[^>]*>/g, tag => fixTag(tag));
}
function fixTag(tag) {
  const viHref = getAttr(tag, 'data-href-vi');
  if (viHref != null) {
    const enHref = getAttr(tag, 'data-href-en') ?? getAttr(tag, 'href') ?? '';
    tag = setAttr(tag, 'data-href-en', toAbs(enHref));
    tag = setAttr(tag, 'data-href-vi', toAbs(viHref));
    tag = setAttr(tag, 'href', toAbs(viHref));
  }
  for (const a of ['href', 'src']) {
    const v = getAttr(tag, a);
    if (v != null && isRelative(v)) tag = setAttr(tag, a, toAbs(v));
  }
  return tag;
}

const VOID = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr']);
const RAW = new Set(['script', 'style']);

function build() {
  const { en, vi } = loadDict();
  const missing = [];
  const t = key => {
    if (Object.prototype.hasOwnProperty.call(vi, key)) return vi[key];
    missing.push(key);
    return Object.prototype.hasOwnProperty.call(en, key) ? en[key] : `[missing:${key}]`;
  };
  const src = fs.readFileSync(SRC, 'utf8');
  const tagRe = /<!--[\s\S]*?-->|<(\/?)([a-zA-Z][a-zA-Z0-9]*)((?:"[^"]*"|'[^']*'|[^>"'])*)>/g;
  let out = '', last = 0, m;
  const stack = [];           // {name, replace: string|null} — replace: nội dung thay cho phần ruột
  let skipDepth = -1;         // đang ở trong phần tử có ruột bị thay → bỏ qua mọi thứ tới thẻ đóng
  while ((m = tagRe.exec(src))) {
    const [raw, closing, nameRaw] = m;
    const name = (nameRaw || '').toLowerCase();
    if (skipDepth < 0) out += src.slice(last, m.index);
    last = tagRe.lastIndex;
    if (!nameRaw) { if (skipDepth < 0) out += raw; continue; }       // chú thích
    if (closing) {
      let i = stack.length - 1;
      while (i >= 0 && stack[i].name !== name) i--;
      if (i < 0) { if (skipDepth < 0) out += raw; continue; }
      stack.length = i + 1;
      const top = stack.pop();
      if (skipDepth === stack.length) { out += top.replace + raw; skipDepth = -1; }
      else if (skipDepth < 0) out += raw;
      continue;
    }
    let tag = raw;
    let replace = null;
    if (skipDepth < 0) {
      const k = getAttr(tag, 'data-i18n');
      const kh = getAttr(tag, 'data-i18n-html');
      if (k != null) replace = escText(t(k));
      else if (kh != null) replace = fixInnerLinks(t(kh));
      // data-ssg-vi="key;n=20.000": chuỗi có biến mà trình duyệt điền bằng JS → điền sẵn giá trị mặc định cho bản tĩnh.
      const ks = getAttr(tag, 'data-ssg-vi');
      if (ks != null && replace == null) {
        const [key, ...pairs] = ks.split(';');
        const vars = Object.fromEntries(pairs.map(x => { const j = x.indexOf('='); return [x.slice(0, j).trim(), x.slice(j + 1)]; }));
        replace = escText(t(key.trim()).replace(/\{(\w+)\}/g, (mm, k2) => (k2 in vars ? vars[k2] : mm)));
      }
      const ka = getAttr(tag, 'data-i18n-attr');
      if (ka) for (const pair of ka.split(';')) {
        const j = pair.indexOf(':'); if (j < 0) continue;
        tag = setAttr(tag, pair.slice(0, j).trim(), t(pair.slice(j + 1).trim()));
      }
      tag = fixTag(tag);
      if (name === 'html') {
        tag = setAttr(tag, 'lang', 'vi');
        tag = setAttr(tag, 'data-page-lang', 'vi');   // i18n.js: ngôn ngữ cố định theo địa chỉ
        tag = setAttr(tag, 'data-alt-href', '/');     // bấm EN → về trang gốc
        tag = setAttr(tag, 'data-root', '/');         // link tương đối trong chuỗi i18n tính từ gốc site
      }
      if (name === 'button' && /\sdata-lang="/.test(tag)) tag = setAttr(tag, 'aria-pressed', String(getAttr(tag, 'data-lang') === 'vi'));
      out += tag;
    }
    const selfClosed = /\/\s*>$/.test(raw) || VOID.has(name);
    if (RAW.has(name)) {                                  // script/style: chép nguyên tới thẻ đóng
      const end = src.toLowerCase().indexOf(`</${name}`, last);
      const stop = end < 0 ? src.length : end;
      if (skipDepth < 0) out += src.slice(last, stop);
      last = stop; tagRe.lastIndex = stop;
      stack.push({ name, replace: null });
      continue;
    }
    if (selfClosed) continue;
    stack.push({ name, replace });
    if (skipDepth < 0 && replace != null) skipDepth = stack.length - 1;
  }
  out += src.slice(last);
  if (skipDepth >= 0 || stack.length) throw new Error('HTML không cân thẻ: ' + stack.map(s => s.name).join(' > '));
  if (missing.length) throw new Error('Thiếu chuỗi tiếng Việt cho: ' + [...new Set(missing)].join(', '));

  // ── phần <head> riêng của bản /vi/ ──
  const swap = (re, to, what) => { if (!re.test(out)) throw new Error('không tìm thấy ' + what); out = out.replace(re, to); };
  swap(/<link rel="canonical" href="[^"]*">/, `<link rel="canonical" href="${SITE}/vi/">`, 'canonical');
  swap(/(<meta property="og:url" content=")[^"]*(">)/, `$1${SITE}/vi/$2`, 'og:url');
  swap(/(<meta property="og:title" content=")[^"]*(">)/, `$1${escAttr(vi['meta.index.title'])}$2`, 'og:title');
  swap(/(<meta property="og:description" content=")[^"]*(">)/, `$1${escAttr(vi['meta.index.desc'])}$2`, 'og:description');
  swap(/(<meta property="og:type" content="website">)/, `$1\n<meta property="og:locale" content="vi_VN">`, 'og:type');
  out = out.replace(/<!doctype html>\n?/i, d => d + '<!-- TỆP SINH TỰ ĐỘNG từ web/index.html + web/i18n.js bằng scripts/build_vi.mjs. Đừng sửa tay. -->\n');
  return out;
}

const html = build();
if (process.argv.includes('--check')) {
  const cur = fs.existsSync(OUT) ? fs.readFileSync(OUT, 'utf8') : '';
  if (cur !== html) { console.error('web/vi/index.html đang lệch. Chạy: node scripts/build_vi.mjs'); process.exit(1); }
  console.log('OK: web/vi/index.html khớp với nguồn');
} else {
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, html);
  console.log(`Đã ghi ${path.relative(ROOT, OUT)} (${html.length} ký tự)`);
}
