/* DatTTS — phòng thu web. Nói chuyện với API theo SPEC (tiền tố /api).
   Mọi chuỗi giao diện đi qua t() trong i18n.js (en + vi); chỉ văn bản mẫu để đọc là tiếng Việt cứng. */
(() => {
  'use strict';

  // ---------- tiện ích ----------
  const I = window.I18N, t = I.t;
  const API = String(window.DATTTS_API || '').replace(/\/+$/, '');
  const SYNC_MAX = 3000, JOB_MAX = 100000, DICT_MAX = 200, FREE_DAILY = 20000;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const fmtN = n => I.fmtNum(Math.round(n));
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const apiUrl = p => (/^(https?:|blob:|data:)/.test(p) ? p : API + p);
  const withDownload = u => u + (u.includes('?') ? '&' : '?') + 'download=1';
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const store = {
    get(k, d) { try { const v = localStorage.getItem('dattts.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('dattts.' + k, JSON.stringify(v)); return true; } catch (e) { return false; } },
    del(k) { try { localStorage.removeItem('dattts.' + k); } catch (e) { /* bỏ qua */ } }
  };
  // API key (License Key do Polar cấp, hoặc key nội bộ). Gửi qua header X-API-Key cho mọi request /api.
  let apiKey = String(store.get('apikey', '') || '').trim();
  const INVALID_KEY_CODES = ['invalid_api_key', 'bad_api_key'];
  function clock(s) {
    s = Math.max(0, Math.floor(s || 0));
    const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, ss = s % 60, p = x => String(x).padStart(2, '0');
    return (h ? h + ':' + p(m) : p(m)) + ':' + p(ss);
  }
  function humanDur(sec) {
    if (sec < 60) return t('dur.sec', { n: Math.max(1, Math.round(sec)) });
    if (sec < 3600) { const m = Math.floor(sec / 60), s = Math.round(sec % 60); return s ? t('dur.minSec', { m, s }) : t('dur.min', { m }); }
    return t('dur.hour', { h: I.fmtNum(sec / 3600, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) });
  }
  // Giống count_chars của máy chủ: NFC, bỏ ký tự điều khiển, gộp khoảng trắng, giữ xuống dòng đoạn.
  function cleanText(s) {
    return String(s || '').normalize('NFC').replace(/\r\n?/g, '\n')
      .replace(/[\u0000-\u0009\u000B-\u001F\u007F​-‍﻿]/g, ' ')
      .replace(/[ \t ]+/g, ' ').replace(/ *\n */g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  }
  const countChars = s => cleanText(s).length;
  const charsLabel = n => I.tp('count.chars', n, { n: fmtN(n) });

  class ApiError extends Error { constructor(message, code, status) { super(message); this.code = code; this.status = status; } }
  async function api(path, opts = {}) {
    // Accept-Language theo ngôn ngữ đang chọn → máy chủ trả error.message đúng ngôn ngữ.
    const init = { method: opts.method || 'GET', headers: { Accept: 'application/json', 'Accept-Language': I.lang }, signal: opts.signal };
    if (opts.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
    // opts.key: thử một key cụ thể (hộp nhập key); opts.key === '' → không gửi key.
    const sentKey = opts.key !== undefined ? opts.key : apiKey;
    if (sentKey) init.headers['X-API-Key'] = sentKey;
    let res;
    try { res = await fetch(apiUrl(path), init); }
    catch (e) {
      if (e && e.name === 'AbortError') throw new ApiError(t('err.timeout'), 'timeout', 0);
      throw new ApiError(t('err.network'), 'network', 0);
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* không phải JSON */ }
    if (!res.ok) {
      const er = data && data.error;
      if (res.status === 401 && sentKey && (!er || INVALID_KEY_CODES.includes(er.code))) {
        // Key hỏng/hết hạn: nếu là key đang lưu thì gỡ ra để khách biết và quay về lượt miễn phí.
        if (opts.key === undefined && sentKey === apiKey) {
          onInvalidKey();
          throw new ApiError(t('err.keyRemoved'), 'invalid_api_key', 401);
        }
        throw new ApiError(t('err.keyBad'), 'invalid_api_key', 401);
      }
      const fallback = res.status === 429 ? t('err.quota') : res.status >= 500 ? t('err.server') : t('err.badRequest', { code: res.status });
      throw new ApiError((er && er.message) || fallback, (er && er.code) || 'http_' + res.status, res.status);
    }
    if (data == null) throw new ApiError(t('err.badResponse'), 'bad_response', res.status);
    return data;
  }

  // ---------- neo cũ tiếng Việt → neo tiếng Anh ----------
  const OLD_ANCHORS = { 'thanh-dieu': 'tones', 'tinh-gia': 'calculator', 'bang-gia': 'pricing', 'hoi-dap': 'faq', 'phong-thu': 'studio' };
  function mapHash() {
    const h = decodeURIComponent(location.hash.slice(1));
    const to = OLD_ANCHORS[h];
    if (!to) return;
    try { history.replaceState(null, '', location.pathname + location.search + '#' + to); } catch (e) { /* bỏ qua */ }
    const el = document.getElementById(to);
    if (el) el.scrollIntoView({ block: 'start' });
  }
  addEventListener('hashchange', mapHash);
  mapHash();

  // ---------- phần tử ----------
  const txt = $('#txt'), studio = $('#studio'), audio = $('#audio');
  const genBtn = $('#genBtn'), genLabel = $('#genLabel');
  const player = $('#player'), transcript = $('#transcript'), seek = $('#seek');
  const statusEl = $('#status');
  const sampleAudio = new Audio(); sampleAudio.preload = 'none';

  let online = false, voices = [], voiceMap = new Map(), selectedId = store.get('voice', null), defaultId = 'truc-ly';
  let busy = false, current = null, showAllVoices = false;

  function announce(m) { statusEl.textContent = ''; setTimeout(() => { statusEl.textContent = m; }, 30); }
  function toast(m) { const el = $('#toast'); el.textContent = m; el.hidden = false; clearTimeout(el._h); el._h = setTimeout(() => { el.hidden = true; }, 3200); }
  let recKey = 'rec.connecting';
  function setRec(key, cls) {
    recKey = key;
    $('#recLabel').textContent = t(key);
    studio.classList.toggle('live', cls === 'live'); studio.classList.toggle('ready', cls === 'ready');
  }
  const idleRec = () => setRec(online ? 'rec.ready' : (document.body.classList.contains('offline') ? 'rec.preview' : 'rec.connecting'), online ? 'ready' : '');

  // ---------- thông báo lỗi trong trang ----------
  const alertEl = $('#alert');
  function showAlert(msg, kind) {
    alertEl.className = 'alert' + (kind === 'info' ? ' info' : '');
    alertEl.setAttribute('role', kind === 'info' ? 'status' : 'alert');
    alertEl.innerHTML = '<span>' + esc(msg) + '</span><button class="x" type="button" aria-label="' + esc(t('alert.close')) + '">×</button>';
    alertEl.hidden = false;
    $('.x', alertEl).onclick = hideAlert;
  }
  function hideAlert() { alertEl.hidden = true; alertEl.textContent = ''; }

  // ---------- chủ đề sáng/tối ----------
  const darkMQ = matchMedia('(prefers-color-scheme: dark)');
  let accent = '';
  function readAccent() { accent = getComputedStyle(document.documentElement).getPropertyValue('--lotus').trim() || '#D63A6E'; }
  $('#themeBtn').onclick = () => {
    const root = document.documentElement;
    const cur = root.dataset.theme || (darkMQ.matches ? 'dark' : 'light');
    const next = cur === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next; store.set('theme', next);
    $('meta[name="theme-color"]').content = next === 'dark' ? '#0C1119' : '#D63A6E';
    readAccent(); drawOnce();
  };
  (darkMQ.addEventListener ? darkMQ.addEventListener('change', () => { readAccent(); drawOnce(); }) : null);

  // ---------- giọng đọc ----------
  const genderKey = g => (/^(f|female|nu|nữ|woman)/i.test(String(g || '')) ? 'female' : 'male');
  const genderLabel = g => t('gender.' + genderKey(g));
  // Giá trị vùng giữ nguyên dạng API ("Bắc"/"Trung"/"Nam") để lọc; nhãn hiển thị dịch qua t().
  function regionKey(r) {
    const s = String(r || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
    return /bac|north/.test(s) ? 'Bắc' : /trung|central/.test(s) ? 'Trung' : /nam|south/.test(s) ? 'Nam' : String(r || '');
  }
  const REGION_ID = { 'Bắc': 'north', 'Trung': 'central', 'Nam': 'south' };
  const regionLabel = r => (REGION_ID[r] ? t('regionShort.' + REGION_ID[r]) : r);
  const regionClass = r => ({ 'Bắc': 'r-bac', 'Trung': 'r-trung', 'Nam': 'r-nam' }[r] || 'r-bac');
  const styleLabel = v => (I.has('style.' + v.style) ? t('style.' + v.style) : (v.style_label || ''));
  const voiceMeta = v => [genderLabel(v.gender), regionLabel(v.region), styleLabel(v)].filter(Boolean).join(' · ');
  // Mô tả giọng từ API là tiếng Việt → chỉ hiện ở chế độ VI.
  const voiceDesc = v => (I.lang === 'vi' && v.description ? v.description : '');
  function initials(name) {
    const w = String(name).trim().split(/\s+/);
    return (w[0][0] + (w.length > 1 ? w[w.length - 1][0] : '')).toUpperCase();
  }
  function voiceByAny(s) {
    if (!s) return null;
    if (voiceMap.has(s)) return voiceMap.get(s);
    const k = String(s).trim().toLowerCase();
    return voices.find(v => v.name.toLowerCase() === k) || null;
  }
  const voiceName = id => (voiceByAny(id) || { name: id }).name;

  function renderInsertOptions() {
    $('#insertVoice').innerHTML = '<option value="">' + esc(t('editor.insert')) + '</option>' +
      voices.map(v => `<option value="${esc(v.name)}">${esc(v.name)} (${esc(voiceMeta(v))})</option>`).join('');
  }
  async function loadVoices() {
    const data = await api('/api/voices');
    defaultId = data.default || defaultId;
    voices = (data.voices || []).map(v => Object.assign({}, v, { region: regionKey(v.region), gender: genderKey(v.gender) }));
    voices.sort((a, b) => (a.featured == null) - (b.featured == null) || (a.featured || 0) - (b.featured || 0));
    voiceMap = new Map(voices.map(v => [v.id, v]));
    if (!voiceMap.has(selectedId)) selectedId = voiceMap.has(defaultId) ? defaultId : (voices[0] && voices[0].id);
    renderInsertOptions(); renderVoices(); renderCurrentVoice();
  }

  function filteredVoices() {
    const r = $('#fRegion').value, g = $('#fGender').value, s = $('#fStyle').value;
    return { list: voices.filter(v => (!r || v.region === r) && (!g || v.gender === g) && (!s || v.style === s)), filtered: !!(r || g || s) };
  }
  function renderVoices() {
    const grid = $('#vgrid');
    const { list, filtered } = filteredVoices();
    let shown = list;
    const collapsible = !filtered && !showAllVoices;
    if (collapsible) shown = list.filter(v => v.featured != null || v.id === selectedId);
    if (collapsible && shown.length < 4) shown = list.slice(0, 10);
    grid.innerHTML = shown.map(v => {
      const sel = v.id === selectedId;
      return `<div class="vcard${sel ? ' sel' : ''}" role="listitem" data-id="${esc(v.id)}">
        <button class="vpick" type="button" aria-pressed="${sel}" title="${esc(voiceDesc(v))}">
          <span class="avatar ${regionClass(v.region)}" aria-hidden="true">${esc(initials(v.name))}</span>
          <span class="txt"><b>${esc(v.name)}</b><small>${esc(voiceMeta(v))}</small></span>
        </button>
        <button class="vplay" type="button" aria-label="${esc(t('voices.sample', { name: v.name }))}">
          <svg class="i-play" viewBox="0 0 14 14" aria-hidden="true"><path d="M3 1.5v11l9.5-5.5z"/></svg>
          <svg class="i-pause" viewBox="0 0 14 14" aria-hidden="true"><path d="M3 1.5h3v11H3zM8 1.5h3v11H8z"/></svg>
        </button>
        ${v.featured != null && v.featured <= 3 ? '<span class="star" aria-hidden="true">' + esc(t('voices.featured')) + '</span>' : ''}
      </div>`;
    }).join('');
    $('#voiceCount').textContent = filtered ? t('voices.countFiltered', { n: fmtN(list.length), total: fmtN(voices.length) }) : I.tp('voices.count', voices.length, { n: fmtN(voices.length) });
    $('#vEmpty').hidden = list.length > 0;
    const more = $('#moreVoices');
    const hiddenN = list.length - shown.length;
    more.hidden = filtered || (hiddenN <= 0 && !showAllVoices);
    more.textContent = showAllVoices ? t('voices.less') : I.tp('voices.more', hiddenN, { n: fmtN(hiddenN) });
    more.setAttribute('aria-expanded', String(showAllVoices));
    // giữ trạng thái nút nghe mẫu khi vẽ lại
    if (!sampleAudio.paused && sampleAudio.dataset.voice) {
      const b = $(`.vcard[data-id="${CSS.escape(sampleAudio.dataset.voice)}"] .vplay`, grid);
      if (b) setSampleBtn(b, true);
    }
  }
  function renderCurrentVoice() {
    const v = voiceMap.get(selectedId); if (!v) return;
    const d = voiceDesc(v);
    $('#currentVoice').innerHTML = `<span class="avatar ${regionClass(v.region)}" aria-hidden="true">${esc(initials(v.name))}</span>
      <span><b>${esc(v.name)}</b><small>${esc(voiceMeta(v))}${d && !/·/.test(d) ? ' — ' + esc(d) : ''}</small></span>`;
  }
  function selectVoice(id, quiet) {
    if (!voiceMap.has(id)) return;
    selectedId = id; store.set('voice', id);
    $$('.vcard', $('#vgrid')).forEach(c => {
      const on = c.dataset.id === id; c.classList.toggle('sel', on); $('.vpick', c).setAttribute('aria-pressed', String(on));
    });
    renderCurrentVoice();
    if (!quiet) announce(t('voices.selected', { name: voiceName(id) }));
  }
  function setSampleBtn(b, on) {
    b.classList.toggle('playing', on);
    const name = voiceName(b.closest('.vcard').dataset.id);
    b.setAttribute('aria-label', t(on ? 'voices.stopSample' : 'voices.sample', { name }));
  }
  function stopSample() {
    sampleAudio.pause();
    $$('.vplay.playing').forEach(b => setSampleBtn(b, false));
    $$('.tone.playing').forEach(b => b.classList.remove('playing'));
  }
  // Một nguồn âm thanh tại một thời điểm.
  function exclusive(who) {
    if (who !== 'main' && !audio.paused) audio.pause();
    if (who !== 'sample') stopSample();
    if (who !== 'speech') stopSpeech();
  }
  function playSample(btn, id) {
    if (btn.classList.contains('playing')) { stopSample(); return; }
    exclusive('sample'); stopSample();
    const v = voiceMap.get(id);
    sampleAudio.src = apiUrl((v && v.sample_url) || `/api/voices/${encodeURIComponent(id)}/sample.mp3`);
    sampleAudio.dataset.voice = id;
    setSampleBtn(btn, true);
    sampleAudio.play().catch(() => { setSampleBtn(btn, false); });
  }
  sampleAudio.addEventListener('ended', stopSample);
  sampleAudio.addEventListener('error', () => {
    if (!sampleAudio.getAttribute('src')) return;
    const playingVoice = $('.vplay.playing'); stopSample();
    if (playingVoice) toast(t('voices.sampleErr'));
  });

  $('#vgrid').addEventListener('click', e => {
    const card = e.target.closest('.vcard'); if (!card) return;
    if (e.target.closest('.vplay')) playSample(e.target.closest('.vplay'), card.dataset.id);
    else if (e.target.closest('.vpick')) selectVoice(card.dataset.id);
  });
  ['#fRegion', '#fGender', '#fStyle'].forEach(s => $(s).addEventListener('change', renderVoices));
  $('#clearFilters').onclick = () => { ['#fRegion', '#fGender', '#fStyle'].forEach(s => { $(s).value = ''; }); renderVoices(); $('#fRegion').focus(); };
  $('#moreVoices').onclick = () => { showAllVoices = !showAllVoices; renderVoices(); };

  // ---------- soạn thảo ----------
  function insertAtCursor(tag) {
    const s = txt.selectionStart != null ? txt.selectionStart : txt.value.length;
    const e = txt.selectionEnd != null ? txt.selectionEnd : s;
    const before = txt.value.slice(0, s), after = txt.value.slice(e);
    const pre = before && !/\s$/.test(before) ? (/[.!?…:;]$/.test(before) ? '\n' : ' ') : '';
    const post = after && !/^\s/.test(after) ? ' ' : (after ? '' : ' ');
    const ins = pre + tag + post;
    txt.value = before + ins + after;
    const caret = s + ins.length;
    txt.focus(); txt.setSelectionRange(caret, caret);
    updateCount();
  }
  $('#insertVoice').addEventListener('change', e => {
    const name = e.target.value; if (!name) return;
    e.target.value = '';
    insertAtCursor('[' + name + ']');
    announce(t('editor.inserted', { name }));
  });

  function pickVoiceName(pref, gender, avoid) {
    const v = voiceByAny(pref);
    if (v && v.name !== avoid) return v.name;
    const alt = voices.find(x => x.gender === gender && x.name !== avoid);
    return alt ? alt.name : pref;
  }
  // Văn bản mẫu để ĐỌC: luôn là tiếng Việt (sản phẩm đọc tiếng Việt), ở cả hai chế độ ngôn ngữ.
  const SAMPLES = {
    podcast() {
      const a = pickVoiceName('Trúc Ly', 'female'), b = pickVoiceName('Thiện Minh', 'male', a);
      return `[${a}] Chào mừng các bạn quay lại với Góc Công Nghệ. Hôm nay mình có một khách mời rất đặc biệt.\n` +
        `[${b}] Chào cả nhà! Hôm nay mình sẽ kể chuyện làm podcast mà không cần phòng thu.\n` +
        `[${a}] Nghe hấp dẫn quá. Vậy làm một tập như thế này mất bao lâu hả anh?\n` +
        `[${b}] Chỉ vài giây thôi. Gõ kịch bản, gắn tên giọng trong ngoặc vuông, bấm tạo là xong.`;
    },
    ad: () => 'Siêu sale cuối tuần! Giảm ngay 50% toàn bộ cửa hàng từ 20/11 đến 22/11. Đơn từ 299.000đ được miễn phí giao hàng. Nhanh tay lên, số lượng có hạn!',
    lesson: () => 'Bài 1: Thanh điệu trong tiếng Việt.\n\nTiếng Việt có sáu thanh: ngang, huyền, sắc, hỏi, ngã, nặng. Mỗi thanh làm thay đổi nghĩa của từ. Ví dụ, “ma”, “mà” và “má” là ba từ hoàn toàn khác nhau.',
    story: () => 'Ngày xửa ngày xưa, ở một làng nhỏ ven sông, có một cô bé tên là Tấm. Tấm hiền lành, chăm chỉ, sáng nào cũng dậy sớm ra đồng. Một hôm, khi đang bắt tép dưới ao, Tấm nghe thấy tiếng ai gọi khẽ sau lưng.',
    news: () => 'Hà Nội, sáng nay trời se lạnh, nhiệt độ thấp nhất khoảng 18 độ C. Theo Trung tâm Dự báo Khí tượng Thủy văn quốc gia, không khí lạnh sẽ tăng cường vào cuối tuần. Người dân ra đường nên mặc thêm áo ấm.'
  };
  $$('[data-sample]').forEach(b => b.addEventListener('click', () => {
    stopSpeech(); txt.value = SAMPLES[b.dataset.sample](); updateCount(); txt.focus();
    if (b.dataset.sample === 'podcast') announce(t('sample.podcastDone'));
  }));

  const rate = $('#rate');
  function syncRate() { $('#rateV').textContent = I.fmtNum(+rate.value, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + '×'; updateCount(); }
  rate.addEventListener('input', syncRate);
  rate.value = store.get('speed', 1); rate.addEventListener('change', () => store.set('speed', +rate.value));
  const savedFmt = store.get('format', 'mp3');
  $$('input[name="fmt"]').forEach(r => { r.checked = r.value === savedFmt; r.addEventListener('change', () => store.set('format', r.value)); });
  const getFmt = () => ($('input[name="fmt"]:checked') || { value: 'mp3' }).value;

  let quota = null;
  // Kỳ hạn mức: máy chủ trả period (day|week|month); bản cũ không có → suy ra từ việc có key hay không.
  const quotaPeriod = q => (q && /^(day|week|month)$/.test(q.period) ? q.period : (apiKey ? 'month' : 'day'));
  function updateCount() {
    const n = countChars(txt.value);
    $('#count').textContent = charsLabel(n);
    const mode = $('#mode');
    mode.className = '';
    if (!online) mode.textContent = '';
    else if (n > JOB_MAX) { mode.textContent = t('mode.overMax', { n: fmtN(JOB_MAX) }); mode.className = 'over'; }
    else if (quota && quota.remaining != null && n > quota.remaining) { mode.textContent = t('mode.overQuota.' + quotaPeriod(quota)); mode.className = 'over'; }
    else if (n > SYNC_MAX) mode.textContent = t('mode.long');
    else mode.textContent = t('mode.instant');
    const sec = n / 25 / parseFloat(rate.value || 1);
    $('#estTime').textContent = t('est.audio', { d: humanDur(sec) });
  }
  txt.addEventListener('input', updateCount);
  txt.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); generate(); } });

  // ---------- quota ----------
  function setQuota(q) {
    if (!q) return;
    quota = q;
    const box = $('#quotaBox'); box.hidden = false;
    const unlimited = q.limit == null || q.limit <= 0 || q.remaining == null;
    if (unlimited) { $('#quotaText').textContent = t('quota.unlimited'); $('#quotaFill').style.width = '100%'; box.className = 'quota'; box.title = ''; return; }
    const rem = Math.max(0, q.remaining);
    $('#quotaText').textContent = t('quota.left.' + quotaPeriod(q), { rem: fmtN(rem), limit: fmtN(q.limit) });
    $('#quotaFill').style.width = Math.max(0, Math.min(100, rem / q.limit * 100)) + '%';
    box.className = 'quota' + (rem === 0 ? ' out' : rem < q.limit * 0.15 ? ' low' : '');
    box.title = q.reset_at ? t('quota.resets', { when: fmtDateTime(q.reset_at) }) : '';
    updateCount();
  }
  // Hạn mức reset theo giờ Việt Nam → hiển thị theo giờ Việt Nam, định dạng theo ngôn ngữ đang chọn.
  const fmtDate = iso => I.fmtDate(iso, { day: 'numeric', month: 'short' });
  const fmtDateTime = iso => t('tz.vn', { when: I.fmtDate(iso, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', day: 'numeric', month: 'short' }) });
  async function loadQuota() { try { setQuota(await api('/api/quota')); } catch (e) { /* không bắt buộc */ } }

  // ---------- từ điển phát âm ----------
  const DICT_DEFAULT = [{ from: 'HLV', to: 'huấn luyện viên' }, { from: 'AI', to: 'ây ai' }];
  let dict = store.get('dict', null);
  if (!Array.isArray(dict)) dict = DICT_DEFAULT.map(x => Object.assign({}, x));
  function saveDict() { store.set('dict', dict); const n = dictEntries().length; $('#dictCount').textContent = n ? '(' + I.tp('dict.count', n, { n: fmtN(n) }) + ')' : ''; }
  function dictEntries() {
    return dict.map(d => ({ from: String(d.from || '').trim().slice(0, 100), to: String(d.to || '').trim().slice(0, 100) }))
      .filter(d => d.from && d.to).slice(0, DICT_MAX);
  }
  function renderDict() {
    const box = $('#dictRows');
    box.innerHTML = dict.map((d, i) => `<div class="drow" role="listitem">
      <input type="text" maxlength="100" value="${esc(d.from || '')}" data-i="${i}" data-k="from" aria-label="${esc(t('dict.fromAria', { i: i + 1 }))}" placeholder="${esc(t('dict.from'))}">
      <span class="arr" aria-hidden="true">→</span>
      <input type="text" maxlength="100" value="${esc(d.to || '')}" data-i="${i}" data-k="to" aria-label="${esc(t('dict.toAria', { i: i + 1 }))}" placeholder="${esc(t('dict.to'))}">
      <button class="iconbtn" type="button" data-del="${i}" aria-label="${esc(t('dict.del', { i: i + 1 }))}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg></button>
    </div>`).join('');
    $('#dictAdd').disabled = dict.length >= DICT_MAX;
    saveDict();
  }
  $('#dictRows').addEventListener('input', e => { const el = e.target; if (el.dataset.i == null) return; dict[+el.dataset.i][el.dataset.k] = el.value; saveDict(); });
  $('#dictRows').addEventListener('click', e => {
    const b = e.target.closest('[data-del]'); if (!b) return;
    const i = +b.dataset.del; dict.splice(i, 1); renderDict();
    const next = $$('#dictRows input')[Math.min(i, dict.length - 1) * 2]; (next || $('#dictAdd')).focus();
  });
  $('#dictAdd').onclick = () => { if (dict.length >= DICT_MAX) return; dict.push({ from: '', to: '' }); renderDict(); $$('#dictRows input').slice(-2)[0].focus(); };

  // ---------- tạo giọng đọc ----------
  const progressBox = $('#progress'), pfill = $('#pfill'), ptext = $('#progressText'), ptrack = $('#progress .ptrack');
  let lastMilestone = 0;
  function setProgress(kind, frac, label) {
    progressBox.hidden = kind === 'off';
    progressBox.classList.toggle('indet', kind === 'indet');
    const pct = Math.round(Math.max(0, Math.min(1, frac || 0)) * 100);
    if (kind === 'det') { pfill.style.width = pct + '%'; ptrack.setAttribute('aria-valuenow', String(pct)); }
    else { pfill.style.width = ''; ptrack.removeAttribute('aria-valuenow'); }
    ptrack.setAttribute('aria-valuetext', label || (kind === 'indet' ? t('progress.working') : pct + '%'));
    ptext.textContent = label || '';
    // Báo mốc 25/50/75% cho trình đọc màn hình khi job nền chạy lâu
    if (kind === 'off' || (kind === 'det' && pct < 25)) lastMilestone = 0;
    if (kind === 'det' && pct < 100) {
      const m = Math.floor(pct / 25) * 25;
      if (m >= 25 && m > lastMilestone) { lastMilestone = m; announce(t('progress.milestone', { p: m })); }
    }
  }
  let busyKey = 'gen.busy';
  function setBusy(on, key) {
    busy = on;
    if (key) busyKey = key;
    genBtn.classList.toggle('busy', on); genBtn.disabled = on;
    genBtn.setAttribute('aria-busy', String(on));
    genLabel.textContent = on ? t(busyKey) : (online ? t('gen.label') : t('gen.preview'));
    if (on) setRec('rec.generating', 'live'); else idleRec();
  }
  function buildBody(format) {
    const b = { text: txt.value, voice: selectedId || defaultId, speed: Math.round(+rate.value * 100) / 100, format: format || getFmt() };
    const d = dictEntries(); if (d.length) b.dictionary = d;
    return b;
  }
  // Ngắn → /api/tts; dài (hoặc máy chủ báo too_long) → /api/jobs + hỏi tiến độ mỗi giây.
  async function synthesize(body, onProgress) {
    if (countChars(body.text) <= SYNC_MAX) {
      onProgress && onProgress({ status: 'sync' });
      try { return await api('/api/tts', { method: 'POST', body }); }
      catch (e) { if (e.code !== 'too_long' && e.status !== 413) throw e; }
    }
    const job = await api('/api/jobs', { method: 'POST', body });
    onProgress && onProgress({ status: job.status || 'queued', progress: 0 });
    let misses = 0;
    for (;;) {
      await sleep(1000);
      let s;
      try { s = await api('/api/jobs/' + encodeURIComponent(job.job_id)); misses = 0; }
      catch (e) { if ((e.code === 'network' || e.status >= 500) && ++misses < 4) continue; throw e; }
      onProgress && onProgress(s);
      if (s.status === 'done' && s.result) return s.result;
      if (s.status === 'error') { const er = s.error || {}; throw new ApiError(er.message || t('err.jobFailed'), er.code || 'job_error', 500); }
    }
  }
  function progressLabel(s) {
    if (s.status === 'sync') return ['indet', 0, t('progress.reading')];
    if (s.status === 'queued') return ['det', 0.02, t('progress.queued')];
    const f = s.progress != null ? s.progress : (s.total ? s.done / s.total : 0);
    const pct = I.fmtNum(Math.round(f * 100)) + '%';
    return ['det', Math.max(0.03, f), s.total ? t('progress.sentences', { done: fmtN(s.done || 0), total: fmtN(s.total), pct }) : pct];
  }

  async function generate() {
    if (busy) return;
    if (!online) { speakOffline(); return; }
    const n = countChars(txt.value);
    if (!n) { showAlert(t('err.empty')); txt.focus(); return; }
    if (n > JOB_MAX) { showAlert(t('err.tooLong', { n: fmtN(n), max: fmtN(JOB_MAX) })); return; }
    hideAlert(); exclusive('main');
    primeAudio(); // trong cử chỉ người dùng (trước mọi await) — Safari/iOS bắt buộc
    const body = buildBody();
    setBusy(true, n > SYNC_MAX ? 'gen.busyLong' : 'gen.busy');
    const t0 = performance.now();
    try {
      const res = await synthesize(body, s => { const [k, f, l] = progressLabel(s); setProgress(k, f, l); });
      setProgress('det', 1, t('progress.done'));
      loadResult(res, body, true);
      addHistory(res, body);
      if (res.quota) setQuota(res.quota); else loadQuota();
      if (apiKey && me) loadMe();
      const secs = I.fmtNum((performance.now() - t0) / 1000, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
      announce(res.cached ? t('gen.doneCached', { d: humanDur(res.duration || 0) }) : t('gen.done', { d: humanDur(res.duration || 0), s: secs }));
      setTimeout(() => { if (!busy) setProgress('off'); }, 1200);
    } catch (e) {
      setProgress('off');
      showAlert(e.message);
      if (e.code === 'quota_exceeded') { loadQuota(); if (apiKey) loadMe(); }
      if (e.code === 'network') checkHealth(true);
    } finally { setBusy(false); }
  }
  genBtn.addEventListener('click', generate);

  // ---------- trình phát + karaoke ----------
  let segs = [], segEls = [], curIdx = -2, raf = 0, dragging = false, previewChars = 0;
  function renderPlayerHead() {
    if (!current) {
      if (!player.hidden && transcript.classList.contains('words')) {
        $('#playerTitle').textContent = t('player.preview');
        $('#playerMeta').textContent = t('player.previewMeta', { chars: charsLabel(previewChars) });
      }
      return;
    }
    const res = current.res;
    const voicesUsed = Array.from(new Set(segs.map(s => voiceName(s.voice)).filter(Boolean)));
    $('#playerTitle').textContent = res.cached ? t('player.titleCached') : t('player.title');
    $('#playerMeta').textContent = [clock(res.duration), charsLabel(res.chars || 0), voicesUsed.join(', '), (res.format || '').toUpperCase()].filter(Boolean).join(' · ');
  }
  function loadResult(res, body, autoplay) {
    current = { res, body, alts: {} };
    stopSpeech();
    transcript.classList.remove('words');
    $('#pcontrols').hidden = false;
    segs = Array.isArray(res.segments) && res.segments.length ? res.segments.slice().sort((a, b) => a.start - b.start)
      : [{ index: 0, voice: body && body.voice, text: res.text || (body && cleanText(body.text)) || '', start: 0, end: res.duration || 0 }];
    const multi = new Set(segs.map(s => s.voice)).size > 1;
    let html = '', lastVoice = null;
    segs.forEach((s, i) => {
      if (multi && s.voice !== lastVoice) {
        if (lastVoice !== null) html += '<span class="para"></span>';
        html += `<span class="vtag">${esc(voiceName(s.voice))}</span>`;
      }
      lastVoice = s.voice;
      html += `<span class="seg" data-i="${i}" role="button" tabindex="0">${esc(s.text)}</span> `;
    });
    transcript.innerHTML = html; transcript.scrollTop = 0;
    segEls = $$('.seg', transcript); curIdx = -2;
    renderPlayerHead();
    renderDownloads();
    player.hidden = false;
    audio.src = apiUrl(res.audio_url);
    audio.load();
    seek.value = 0; updateTime();
    paint(-1);
    if (autoplay) { exclusive('main'); audio.play().catch(() => {}); }
  }
  function renderDownloads() {
    if (!current) return;
    const r = current.res, f0 = r.format || 'mp3';
    const icon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11m-5-5 5 5 5-5M5 20h14"/></svg>';
    let h = '<span class="lbl">' + esc(t('dl.label')) + '</span>';
    ['mp3', 'wav', 'ogg'].forEach(f => {
      const F = f.toUpperCase();
      const ready = f === f0 ? r.audio_url : current.alts[f];
      if (ready) h += `<a class="dl" href="${esc(withDownload(apiUrl(ready)))}" download data-fmt="${f}" aria-label="${esc(t('dl.file', { f: F }))}">${icon}${F}</a>`;
      else if (current.body) h += `<button class="dl alt" type="button" data-alt="${f}" aria-label="${esc(t('dl.alt', { f: F }))}" title="${esc(t('dl.altTitle', { f: F }))}">${icon}${F}</button>`;
    });
    if (r.srt_url) h += `<a class="dl" href="${esc(withDownload(apiUrl(r.srt_url)))}" download data-fmt="srt" aria-label="${esc(t('dl.srt'))}">${icon}SRT</a>`;
    if (r.vtt_url) h += `<a class="dl" href="${esc(withDownload(apiUrl(r.vtt_url)))}" download data-fmt="vtt" aria-label="${esc(t('dl.vtt'))}">${icon}VTT</a>`;
    $('#downloads').innerHTML = h;
  }
  $('#downloads').addEventListener('click', async e => {
    const b = e.target.closest('[data-alt]'); if (!b || b.disabled || !current || !current.body) return;
    const f = b.dataset.alt, cur = current;
    if (cur.pending && cur.pending[f]) return; // đang tạo bản này rồi (bấm/Enter hai lần)
    (cur.pending = cur.pending || {})[f] = true;
    b.disabled = true; b.setAttribute('aria-busy', 'true');
    b.classList.add('busy'); b.lastChild.textContent = f.toUpperCase() + '…';
    try {
      const res = await synthesize(Object.assign({}, cur.body, { format: f }), s => {
        const [, fr] = progressLabel(s); if (s.status !== 'sync') b.lastChild.textContent = f.toUpperCase() + ' ' + Math.round(fr * 100) + '%';
      });
      if (res.quota) setQuota(res.quota);
      cur.alts[f] = res.audio_url;
      if (current !== cur) return;
      renderDownloads();
      const a = $(`#downloads a[data-fmt="${f}"]`); if (a) { a.click(); a.focus(); }
      announce(t('dl.altDone', { f: f.toUpperCase() }));
    } catch (err) {
      showAlert(err.message);
      if (current === cur) renderDownloads();
    } finally { delete cur.pending[f]; }
  });

  function findSeg(time) {
    let lo = 0, hi = segs.length - 1, ans = -1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (segs[mid].start <= time + 0.02) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
    return ans;
  }
  function paint(idx) {
    if (idx === curIdx) return;
    curIdx = idx;
    for (let i = 0; i < segEls.length; i++) {
      const c = i < idx ? 'seg said' : i === idx ? 'seg now' : 'seg';
      if (segEls[i].className !== c) segEls[i].className = c;
    }
    const el = segEls[idx];
    if (el) {
      el.setAttribute('aria-current', 'true');
      segEls.forEach(x => { if (x !== el) x.removeAttribute('aria-current'); });
      const top = el.offsetTop, bottom = top + el.offsetHeight;
      if (top < transcript.scrollTop || bottom > transcript.scrollTop + transcript.clientHeight) {
        const target = Math.max(0, top - transcript.clientHeight / 3);
        if (transcript.scrollTo) transcript.scrollTo({ top: target, behavior: reduceMotion.matches ? 'auto' : 'smooth' }); else transcript.scrollTop = target;
      }
    }
  }
  const audioDur = () => (isFinite(audio.duration) && audio.duration > 0 ? audio.duration : (current && current.res.duration) || 0);
  function updateTime() {
    const d = audioDur(), tm = audio.currentTime || 0;
    if (!dragging) seek.value = d ? Math.round(tm / d * 1000) : 0;
    $('#time').textContent = clock(tm) + ' / ' + clock(d);
    seek.setAttribute('aria-valuetext', t('player.of', { t: clock(tm), d: clock(d) }));
  }
  function sync() {
    updateTime();
    if (segs.length && current) paint(audio.ended ? segs.length : findSeg(audio.currentTime || 0));
  }
  function tick() { sync(); raf = audio.paused ? 0 : requestAnimationFrame(tick); }
  const playBtn = $('#playPause');
  const setPlayLabel = () => playBtn.setAttribute('aria-label', t(playBtn.classList.contains('playing') ? 'player.pause' : 'player.play'));
  audio.addEventListener('play', () => {
    exclusive('main');
    playBtn.classList.add('playing'); setPlayLabel();
    setRec('rec.playing', 'live'); speaking = true; ensureAnalyser();
    if (!raf) raf = requestAnimationFrame(tick);
    kickWave();
  });
  function onStop() {
    playBtn.classList.remove('playing'); setPlayLabel();
    if (!busy) idleRec();
    speaking = false; cancelAnimationFrame(raf); raf = 0; sync();
  }
  audio.addEventListener('pause', onStop);
  audio.addEventListener('ended', onStop);
  audio.addEventListener('timeupdate', sync);
  audio.addEventListener('seeked', sync);
  audio.addEventListener('loadedmetadata', updateTime);
  audio.addEventListener('durationchange', updateTime);
  audio.addEventListener('error', () => {
    if (!audio.getAttribute('src') || !current) return;
    showAlert(t('err.audio'));
  });
  playBtn.onclick = () => { if (audio.paused) { primeAudio(); audio.play().catch(() => {}); } else audio.pause(); };
  seek.addEventListener('input', () => { dragging = true; const d = audioDur(); if (d) { audio.currentTime = seek.value / 1000 * d; } sync(); });
  seek.addEventListener('change', () => { dragging = false; sync(); });
  function jumpTo(i, play) {
    if (!segs.length) return;
    i = Math.max(0, Math.min(segs.length - 1, i));
    audio.currentTime = segs[i].start + 0.001; curIdx = -2; sync();
    if (play && audio.paused) { primeAudio(); audio.play().catch(() => {}); }
  }
  transcript.addEventListener('click', e => {
    const s = e.target.closest('.seg'); if (!s || transcript.classList.contains('words')) return;
    jumpTo(+s.dataset.i, true);
  });
  transcript.addEventListener('keydown', e => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const s = e.target.closest('.seg[data-i]'); if (!s || transcript.classList.contains('words')) return;
    e.preventDefault(); jumpTo(+s.dataset.i, true);
  });
  $('#prevSeg').onclick = () => { const i = findSeg(audio.currentTime); const back = i >= 0 && audio.currentTime - segs[i].start > 1.2 ? i : i - 1; jumpTo(back, !audio.paused); };
  $('#nextSeg').onclick = () => jumpTo(findSeg(audio.currentTime) + 1, !audio.paused);

  // ---------- sóng âm ----------
  const cv = $('#wave'), cx = cv.getContext('2d');
  let speaking = false, energy = 0, waveRaf = 0, visible = true, analyser = null, freq = null, actx = null;
  const sameOrigin = (() => { if (!API) return true; try { return new URL(API, location.href).origin === location.origin; } catch (e) { return false; } })();
  // Tạo/đánh thức AudioContext ĐỒNG BỘ trong cử chỉ người dùng (click/phím). Safari/iOS giữ context
  // "suspended" nếu tạo ngoài cử chỉ → nếu nối thẻ audio vào đó thì phát ra không có tiếng.
  function primeAudio() {
    if (!sameOrigin || reduceMotion.matches) return;
    try {
      const AC = window.AudioContext || window.webkitAudioContext; if (!AC) return;
      if (!actx) actx = new AC();
      if (actx.state === 'suspended') actx.resume().catch(() => {});
    } catch (e) { actx = null; }
  }
  function ensureAnalyser() {
    if (analyser || !sameOrigin || reduceMotion.matches) return;
    // Chỉ nối khi context đã chạy; chưa chạy → dùng sóng mô phỏng, âm thanh vẫn phát thẳng từ thẻ audio.
    if (!actx || actx.state !== 'running') return;
    try {
      const src = actx.createMediaElementSource(audio);
      analyser = actx.createAnalyser(); analyser.fftSize = 256; analyser.smoothingTimeConstant = 0.75;
      src.connect(analyser); analyser.connect(actx.destination);
      freq = new Uint8Array(analyser.frequencyBinCount);
    } catch (e) { analyser = null; }
  }
  function sizeCanvas() { const r = cv.getBoundingClientRect(), d = devicePixelRatio || 1; cv.width = Math.max(1, r.width * d); cv.height = Math.max(1, r.height * d); cx.setTransform(d, 0, 0, d, 0, 0); }
  function draw(time) {
    const w = cv.clientWidth, h = cv.clientHeight; cx.clearRect(0, 0, w, h);
    energy += ((speaking ? 1 : 0.08) - energy) * (reduceMotion.matches ? 1 : 0.08);
    const bars = Math.floor(w / 6);
    const live = analyser && !audio.paused;
    if (live) analyser.getByteFrequencyData(freq);
    cx.fillStyle = accent;
    for (let i = 0; i < bars; i++) {
      const x = i * 6 + 2;
      let a;
      if (live) {
        const k = Math.floor(Math.abs(i - bars / 2) / (bars / 2) * freq.length * 0.7);
        a = (freq[k] / 255) * h * 0.46 + 1.5;
      } else {
        const n = Math.sin(i * 0.35 + time / 180) * Math.sin(i * 0.11 - time / 430) + Math.sin(i * 1.7 + time / 90) * 0.35;
        a = (Math.abs(n) * 0.8 + 0.12) * energy * (h * 0.42) + 1.5;
      }
      cx.globalAlpha = 0.35 + 0.65 * energy; cx.fillRect(x, h / 2 - a, 3, a * 2);
    }
    cx.globalAlpha = 1;
  }
  function loop(time) { draw(time); waveRaf = (visible && !reduceMotion.matches && (speaking || energy > 0.09)) ? requestAnimationFrame(loop) : 0; }
  function kickWave() { if (!waveRaf && !reduceMotion.matches) waveRaf = requestAnimationFrame(loop); else if (reduceMotion.matches) draw(0); }
  function drawOnce() { draw(performance.now()); kickWave(); }
  if ('IntersectionObserver' in window) new IntersectionObserver(es => { visible = es[0].isIntersecting; if (visible) kickWave(); }).observe(cv);
  addEventListener('resize', () => { sizeCanvas(); drawOnce(); });

  // ---------- chế độ thử trên trình duyệt (speechSynthesis) ----------
  const synth = window.speechSynthesis;
  let bVoices = [], viVoice = null, fallbackTimer = null, wordSpans = [], gotBoundary = false;
  function loadBrowserVoices() {
    const note = $('#voiceNote');
    if (!synth) { note.textContent = t('offline.noSynth'); note.className = 'note warn'; return; }
    bVoices = synth.getVoices();
    const sel = $('#voiceSel'), prev = sel.value; sel.innerHTML = '';
    const vi = bVoices.filter(v => /^vi/i.test(v.lang));
    const list = vi.length ? vi : bVoices;
    list.forEach(v => { const o = document.createElement('option'); o.value = bVoices.indexOf(v); o.textContent = v.name + ' (' + v.lang + ')'; sel.appendChild(o); });
    if (!list.length) { const o = document.createElement('option'); o.textContent = t('offline.defaultVoice'); o.value = ''; sel.appendChild(o); }
    viVoice = vi[0] || null;
    if (viVoice) { sel.value = prev && list.some(v => String(bVoices.indexOf(v)) === prev) ? prev : bVoices.indexOf(viVoice); note.className = 'note'; note.textContent = I.tp('offline.hasVi', vi.length, { n: vi.length }); }
    else { note.className = 'note warn'; note.textContent = t('offline.noVi'); }
  }
  if (synth) { loadBrowserVoices(); if ('onvoiceschanged' in synth) synth.onvoiceschanged = loadBrowserVoices; }
  function stopSpeech() {
    if (synth && (synth.speaking || synth.pending)) synth.cancel();
    clearInterval(fallbackTimer); fallbackTimer = null;
    if (!online) { speaking = false; idleRec(); }
  }
  function markWord(idx) { wordSpans.forEach((s, i) => { s.className = i < idx ? 'seg said' : i === idx ? 'seg now' : 'seg'; }); }
  function speakOffline() {
    const text = cleanText(txt.value.replace(/\[[^\]\n]{1,40}\]\s*/g, ''));
    if (!text) { showAlert(t('err.empty')); return; }
    if (!synth) { showAlert(t('offline.noSynth')); return; }
    hideAlert(); exclusive('speech'); synth.cancel(); clearInterval(fallbackTimer);
    // karaoke ước lượng theo từ
    current = null; segs = []; segEls = [];
    transcript.classList.add('words');
    transcript.innerHTML = ''; wordSpans = [];
    const re = /\S+|\s+/g; let m;
    while ((m = re.exec(text))) {
      const s = document.createElement('span'); s.textContent = m[0];
      if (/\S/.test(m[0])) { s.className = 'seg'; s.dataset.c = m.index; wordSpans.push(s); }
      transcript.appendChild(s);
    }
    $('#pcontrols').hidden = true; $('#downloads').innerHTML = '';
    previewChars = text.length;
    player.hidden = false;
    renderPlayerHead();
    const u = new SpeechSynthesisUtterance(text);
    const v = bVoices[+$('#voiceSel').value]; if (v) u.voice = v;
    u.lang = (v && v.lang) || 'vi-VN'; u.rate = +rate.value;
    gotBoundary = false;
    u.onstart = () => {
      speaking = true; kickWave(); setRec('rec.previewing', 'live'); markWord(0);
      const per = Math.max(180, 380 / u.rate); let k = 0;
      fallbackTimer = setInterval(() => { if (gotBoundary) { clearInterval(fallbackTimer); return; } k = Math.min(k + 1, wordSpans.length - 1); markWord(k); }, per);
    };
    u.onboundary = e => {
      if (e.name && e.name !== 'word') return; gotBoundary = true;
      let k = 0; for (let i = 0; i < wordSpans.length; i++) if (+wordSpans[i].dataset.c <= e.charIndex) k = i; markWord(k);
    };
    u.onend = () => { clearInterval(fallbackTimer); markWord(wordSpans.length); speaking = false; idleRec(); };
    u.onerror = () => { clearInterval(fallbackTimer); speaking = false; idleRec(); };
    synth.speak(u);
    announce(t('offline.speaking'));
  }
  $('#stopBtn').onclick = () => { stopSpeech(); markWord(-1); };

  // ---------- lịch sử ----------
  let history = store.get('history', []);
  if (!Array.isArray(history)) history = [];
  function addHistory(res, body) {
    const item = {
      id: res.id, format: res.format, duration: res.duration, chars: res.chars, at: Date.now(),
      audio_url: res.audio_url, srt_url: res.srt_url, vtt_url: res.vtt_url,
      voice: body.voice, speed: body.speed, dict: Array.isArray(body.dictionary) && body.dictionary.length ? body.dictionary : null,
      excerpt: cleanText(body.text).replace(/\[[^\]\n]{1,40}\]\s*/g, '').slice(0, 140),
      text: body.text.length <= 6000 ? body.text : null
    };
    if (Array.isArray(res.segments) && JSON.stringify(res.segments).length < 40000) item.segments = res.segments;
    history = [item].concat(history.filter(h => !(h.id === item.id && h.format === item.format))).slice(0, 10);
    if (!store.set('history', history)) { history.forEach(h => { delete h.segments; }); store.set('history', history); }
    renderHistory();
  }
  function renderHistory() {
    const ol = $('#history');
    $('#histEmpty').hidden = history.length > 0;
    $('#histCount').textContent = history.length ? `(${fmtN(history.length)})` : '';
    ol.innerHTML = history.map((h, i) => {
      const when = new Date(h.at).toLocaleString(I.locale, { hour: '2-digit', minute: '2-digit', day: 'numeric', month: 'short' });
      const F = (h.format || '').toUpperCase();
      return `<li class="hitem">
        <button class="iconbtn" type="button" data-replay="${i}" aria-label="${esc(t('hist.replay', { t: h.excerpt.slice(0, 40) }))}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4.5v15l13-7.5z"/></svg></button>
        <span class="htxt"><span lang="vi">${esc(h.excerpt)}</span><small>${esc(voiceName(h.voice))} · ${clock(h.duration)} · ${F} · ${esc(when)}</small></span>
        <a class="iconbtn" href="${esc(withDownload(apiUrl(h.audio_url)))}" download aria-label="${esc(t('hist.download', { f: F }))}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11m-5-5 5 5 5-5M5 20h14"/></svg></a>
      </li>`;
    }).join('');
  }
  $('#history').addEventListener('click', e => {
    const b = e.target.closest('[data-replay]'); if (!b) return;
    const h = history[+b.dataset.replay]; if (!h) return;
    if (!online) { showAlert(t('err.needServer')); return; }
    hideAlert();
    primeAudio();
    // Dùng đúng từ điển lúc tạo bản này (không phải từ điển hiện tại) để bản WAV/OGG khớp bản đã nghe
    const body = { text: h.text || h.excerpt, voice: h.voice, speed: h.speed || 1, format: h.format };
    if (h.dict) body.dictionary = h.dict;
    loadResult({ id: h.id, format: h.format, duration: h.duration, chars: h.chars, audio_url: h.audio_url, srt_url: h.srt_url, vtt_url: h.vtt_url, segments: h.segments, text: h.excerpt, cached: true }, h.text ? body : null, true);
    player.scrollIntoView({ block: 'nearest', behavior: reduceMotion.matches ? 'auto' : 'smooth' });
  });

  // ---------- thanh điệu ----------
  // syl là âm tiết tiếng Việt để đọc; tên thanh + nghĩa lấy từ i18n (tone.<id>.nm / tone.<id>.ex).
  const tones = [
    { id: 'ngang', syl: 'ma', p: 'M4 12 L96 12' },
    { id: 'huyen', syl: 'mà', p: 'M4 14 C40 16 70 20 96 24' },
    { id: 'sac', syl: 'má', p: 'M4 14 C40 14 70 10 96 3' },
    { id: 'hoi', syl: 'mả', p: 'M4 12 C35 22 60 24 96 12' },
    { id: 'nga', syl: 'mã', p: 'M4 12 C30 16 44 20 50 16 L56 22 C70 10 84 6 96 3' },
    { id: 'nang', syl: 'mạ', p: 'M4 14 C40 18 70 24 82 26 L84 26' }
  ];
  const toneCache = new Map();
  const tg = $('#tonesGrid');
  function renderTones() {
    tg.innerHTML = '';
    tones.forEach(tn => {
      const b = document.createElement('button'); b.className = 'tone'; b.type = 'button'; b.dataset.tone = tn.id;
      const nm = t('tone.' + tn.id + '.nm'), ex = t('tone.' + tn.id + '.ex');
      b.setAttribute('aria-label', t('tone.aria', { syl: tn.syl, nm, ex }));
      b.innerHTML = `<span class="syl" lang="vi" aria-hidden="true">${tn.syl}</span><svg viewBox="0 0 100 28" preserveAspectRatio="none" aria-hidden="true"><path d="${tn.p}"/></svg><span class="nm" aria-hidden="true">${esc(nm)}</span><span class="ex" aria-hidden="true">${esc(ex)}</span>`;
      b.onclick = () => playTone(tn, b);
      tg.appendChild(b);
    });
  }
  function speakTone(tn, b) {
    if (!synth) { toast(t('tone.noSynth')); return; }
    synth.cancel(); const u = new SpeechSynthesisUtterance(tn.syl); if (viVoice) u.voice = viVoice; u.lang = 'vi-VN'; u.rate = 0.8;
    b.classList.add('playing'); u.onend = u.onerror = () => b.classList.remove('playing'); synth.speak(u);
  }
  async function playTone(tn, b) {
    exclusive('sample');
    if (!online) { speakTone(tn, b); return; }
    b.classList.add('playing');
    const key = (selectedId || defaultId) + '|' + tn.syl;
    try {
      let url = toneCache.get(key);
      if (!url) {
        const res = await api('/api/tts', { method: 'POST', body: { text: tn.syl + '.', voice: selectedId || defaultId, format: 'mp3' } });
        url = apiUrl(res.audio_url); toneCache.set(key, url); if (res.quota) setQuota(res.quota);
      }
      if (!b.classList.contains('playing')) return;
      sampleAudio.src = url; sampleAudio.dataset.voice = '';
      await sampleAudio.play();
    } catch (e) { b.classList.remove('playing'); speakTone(tn, b); }
  }

  // ---------- giá: định dạng ----------
  const VND_PER_USD = Number(window.DATTTS_VND_PER_USD) > 0 ? Number(window.DATTTS_VND_PER_USD) : 25400;
  // Giá gói (cent) và hạn mức lấy từ /api/billing/plans; chưa có thì để null.
  let billing = { enabled: false, loaded: false, plans: [] };
  let buying = false; // đang tạo phiên thanh toán (xem buy())
  const planById = id => billing.plans.find(p => p && p.id === id) || null;
  const isUsd = p => String((p && p.currency) || billing.currency || 'usd').toLowerCase() === 'usd';
  const vndOfUsd = usd => Math.round(usd * VND_PER_USD / 1000) * 1000;
  const fmtVnd = v => I.fmtNum(v) + 'đ';
  const fmtMoney = p => I.fmtMoney(p.price, (p.currency || billing.currency || 'usd'));
  const fmtUsd = usd => I.fmtMoney(Math.round(usd * 100), 'usd', usd >= 100 ? 0 : undefined);
  const CYCLES = ['week', 'month', 'year'];
  const PLAN_OF = { week: 'pro_week', month: 'pro_month', year: 'pro_year' };
  // Hạn mức mặc định theo SPEC, dùng khi API chưa trả (hoặc bản cũ).
  const DEFAULT_CHARS = { pro_week: 250000, pro_month: 1000000, pro_year: 1000000 };
  const planChars = id => { const p = planById(id); const c = p && (p.chars != null ? p.chars : p.monthly_chars); return typeof c === 'number' && c > 0 ? c : DEFAULT_CHARS[id]; };
  const planPeriod = id => { const p = planById(id); return (p && /^(day|week|month)$/.test(p.period)) ? p.period : (id === 'pro_week' ? 'week' : 'month'); };

  // ---------- máy tính chi phí (thanh trượt log 500 → 2.000.000 ký tự / tháng) ----------
  const INTL_USD_PER_M = 16, HUMAN_USD_PER_MIN = 6, CHARS_PER_MIN = 1500;
  const cs = $('#chars');
  const toChars = v => Math.round(500 * Math.pow(4000, v / 100) / 100) * 100 || 500;
  const toSlider = c => Math.log(c / 500) / Math.log(4000) * 100;
  function calc() {
    const c = toChars(+cs.value); $('#charsV').textContent = fmtN(c);
    const min = c / CHARS_PER_MIN;
    $('#audioLen').textContent = t('calc.audio', { d: humanDur(min * 60) });
    $('#perMin').textContent = t('calc.perMin', { n: fmtN(CHARS_PER_MIN) });
    const pm = planById('pro_month'), monthCap = planChars('pro_month');
    let ours = null, sub = '', big;
    if (c <= FREE_DAILY) { ours = 0; sub = t('calc.freeDaily'); }
    else if (c <= FREE_DAILY * 30) { ours = 0; sub = t('calc.freeSpread', { n: fmtN(FREE_DAILY) }); }
    else if (c <= monthCap) {
      if (pm && pm.price != null && isUsd(pm)) { ours = pm.price / 100; sub = t('calc.pro', { plan: t('plan.name.pro_month') }); }
      else sub = t('plan.name.pro_month') + ' · ' + (billing.loaded ? t('calc.seePricing') : t('pricing.loading'));
    } else sub = t('calc.custom', { n: fmtN(monthCap) });
    if (ours != null) big = esc(fmtUsd(ours)) + (ours ? ' <small>' + esc(t('per.month')) + '</small>' : '');
    else big = esc(c > monthCap ? t('calc.customBig') : t('plan.name.pro_month'));
    $('#ourCost').innerHTML = big + ' <small class="sub">' + esc(sub) + '</small>';
    const intl = c / 1e6 * INTL_USD_PER_M, human = min * HUMAN_USD_PER_MIN;
    const rows = [[t('calc.bar.intl'), intl, ''], [t('calc.bar.human'), human, '']];
    if (ours != null) rows.unshift(['DatTTS', ours, 'us']);
    const max = Math.max(...rows.map(r => r[1]), 1);
    $('#cmp').innerHTML = rows
      .map(([n, v, k]) => `<div class="bar ${k}"><span>${esc(n)}</span><div class="track"><div class="fill" style="width:${Math.max(1.5, v / max * 100)}%"></div></div><span class="v">${esc(fmtUsd(v))}</span></div>`).join('');
    // Quy đổi VND tham khảo chỉ ở chế độ VI.
    const vnd = $('#calcVnd');
    vnd.hidden = !(I.lang === 'vi' && ours);
    if (!vnd.hidden) vnd.textContent = t('pricing.vnd', { v: fmtVnd(vndOfUsd(ours)), per: t('per.month') });
    $('#calcNote').textContent = t('calc.note', { intl: fmtUsd(INTL_USD_PER_M), human: fmtUsd(HUMAN_USD_PER_MIN), fx: I.fmtNum(VND_PER_USD) });
  }
  cs.addEventListener('input', calc);
  $$('[data-chars]').forEach(b => b.addEventListener('click', () => { cs.value = toSlider(+b.dataset.chars); calc(); }));
  cs.value = toSlider(15000);

  // ---------- bảng giá + mua gói qua Polar ----------
  let cycle = store.get('cycle', 'month');
  if (!CYCLES.includes(cycle)) cycle = 'month';
  $$('input[name="cycle"]').forEach(r => {
    r.checked = r.value === cycle;
    r.addEventListener('change', () => { if (!r.checked) return; cycle = r.value; store.set('cycle', cycle); setBuyMsg(''); renderPlans(); });
  });
  const buyMsg = $('#buyMsg'), buyBtn = $('#buyPro');
  let buyMsgKey = null, buyMsgKind = '';
  function setBuyMsg(text, kind, key) { buyMsgKey = key || null; buyMsgKind = kind || ''; buyMsg.className = 'buy-msg' + (kind ? ' ' + kind : ''); buyMsg.textContent = text || ''; }
  const planAvailable = p => !!p && p.available !== false;
  function renderPlans() {
    const id = PLAN_OF[cycle], p = planById(id);
    const card = $('.plan[data-plan-id="pro"]');
    $('#freePrice').textContent = I.fmtMoney(0, 'usd', 0);
    $('#freeQuota').textContent = t('plan.quota.day', { n: fmtN(FREE_DAILY) });
    // nhãn trên nút chọn chu kỳ
    CYCLES.forEach(c => {
      const pc = planById(PLAN_OF[c]), badge = $(`[data-cycle-badge="${c}"]`);
      const save = pc && pc.save_percent > 0 ? pc.save_percent : 0;
      badge.hidden = !save; badge.textContent = save ? t('badge.saveShort', { p: save }) : '';
      const input = $(`input[name="cycle"][value="${c}"]`);
      const off = billing.loaded && billing.enabled && pc != null && !planAvailable(pc);
      input.closest('label').classList.toggle('off', off);
    });
    const popular = $('#proPopular'), saveTag = $('#proSave');
    popular.hidden = !(p && p.popular); popular.textContent = t('badge.popular');
    saveTag.hidden = !(p && p.save_percent > 0); saveTag.textContent = p && p.save_percent > 0 ? t('badge.save', { p: p.save_percent }) : '';
    card.classList.toggle('hot', !!(p && p.popular) || !p);
    const priceEl = $('[data-price]', card), vndEl = $('[data-vnd]', card), billed = $('#proBilled');
    const per = t('per.' + cycle);
    if (p && p.price != null) {
      priceEl.innerHTML = esc(fmtMoney(p)) + ' <small>' + esc(per) + '</small>';
      if (cycle === 'year') billed.textContent = t('pricing.yearlyEq', { m: I.fmtMoney(Math.round(p.price / 12), p.currency || 'usd', 2) });
      else billed.textContent = t('pricing.billed.' + cycle);
      billed.hidden = false;
      const showVnd = I.lang === 'vi' && isUsd(p);
      vndEl.hidden = !showVnd;
      if (showVnd) vndEl.textContent = t('pricing.vnd', { v: fmtVnd(vndOfUsd(p.price / 100)), per });
    } else {
      priceEl.innerHTML = '<span class="price-ph">' + esc(!billing.loaded ? t('pricing.loading') : billing.enabled ? t('pricing.atCheckout') : t('pricing.soon')) + '</span>';
      vndEl.hidden = true; billed.hidden = true;
    }
    $('#proQuota').textContent = t('plan.quota.' + planPeriod(id), { n: fmtN(planChars(id)) });
    buyBtn.dataset.buy = id;
    buyBtn.dataset.plan = id;
    const lbl = $('.lbl', buyBtn), name = t('plan.name.' + id);
    if (!buyBtn.classList.contains('busy')) {
      // Chưa biết billing bật hay tắt → nút trung tính, bấm không làm gì (không rơi về danh sách chờ).
      const avail = !billing.enabled || p == null || planAvailable(p);
      lbl.textContent = !billing.loaded ? t('pricing.loadingBtn') : !billing.enabled ? t('pricing.soonBtn') : avail ? t('pricing.getPro') : t('pricing.unavailable');
      buyBtn.setAttribute('aria-label', !billing.loaded ? t('pricing.aria.loading', { plan: name }) : !billing.enabled ? t('pricing.aria.soon', { plan: name }) : avail ? t('pricing.aria.buy', { plan: name }) : t('pricing.unavailable'));
      if (!billing.loaded || buying || !avail) buyBtn.setAttribute('aria-disabled', 'true'); else buyBtn.removeAttribute('aria-disabled');
    }
    $('#waitlist').hidden = !billing.loaded || billing.enabled;
    $('#pricing').classList.toggle('billing-on', billing.enabled);
    $('#pricingLead').textContent = billing.loaded && !billing.enabled ? t('pricing.leadSoon') : t('pricing.lead');
    if (buyMsgKey) buyMsg.textContent = t(buyMsgKey);
    if (!billing.enabled && !$('#waitlist').hidden && wlPlan && !wlTouched) { wlPlan.value = id; }
    calc();
  }
  let plansReq = null; // gộp các lần gọi trùng nhau
  function loadPlans() {
    if (plansReq) return plansReq;
    plansReq = (async () => {
      try {
        const d = await api('/api/billing/plans', { key: '' });
        billing = { enabled: !!d.enabled, loaded: true, currency: d.currency || 'usd', plans: Array.isArray(d.plans) ? d.plans : [] };
      } catch (e) {
        billing = { enabled: false, loaded: true, plans: [] };
      }
      renderPlans();
    })().finally(() => { plansReq = null; });
    return plansReq;
  }
  const safeRedirect = u => { try { const x = new URL(u, location.href); return /^https?:$/.test(x.protocol) ? x.href : null; } catch (e) { return null; } };
  // Chỉ một phiên thanh toán được tạo mỗi lúc: khoá nút mua + chu kỳ cho tới khi xong/lỗi.
  function lockBuy(on) {
    buying = on;
    buyBtn.disabled = on; if (on) buyBtn.setAttribute('aria-disabled', 'true'); else buyBtn.removeAttribute('aria-disabled');
    $$('input[name="cycle"]').forEach(r => { r.disabled = on; });
  }
  async function buy(plan) {
    if (buying || buyBtn.classList.contains('busy')) return;
    const lbl = $('.lbl', buyBtn);
    lockBuy(true);
    buyBtn.classList.add('busy'); buyBtn.setAttribute('aria-busy', 'true');
    lbl.textContent = t('buy.opening'); setBuyMsg('');
    let leaving = false;
    try {
      const r = await api('/api/billing/checkout', { method: 'POST', body: { plan }, key: '' });
      const url = safeRedirect(r && r.url);
      if (!url) throw new ApiError(t('err.checkout'), 'bad_response', 200);
      leaving = true;
      setBuyMsg(t('buy.redirect'), 'ok', 'buy.redirect');
      location.href = url;
    } catch (e) {
      if (e.code === 'billing_disabled') { billing.enabled = false; setBuyMsg(t('buy.disabled'), 'err', 'buy.disabled'); }
      else if (e.code === 'network') setBuyMsg(t('buy.network'), 'err', 'buy.network');
      else setBuyMsg(e.message, 'err');
    } finally {
      // Khi đang chuyển trang thì giữ nút bận; nếu người dùng bấm Back (bfcache) thì mở lại nút.
      const reset = () => { buyBtn.classList.remove('busy'); buyBtn.removeAttribute('aria-busy'); lockBuy(false); renderPlans(); };
      if (!leaving) reset(); else addEventListener('pageshow', ev => { if (ev.persisted) { reset(); setBuyMsg(''); } }, { once: true });
    }
  }

  // ---------- API key + gói của tôi ----------
  let me = null;
  const keyDlg = $('#keyDlg'), keyInput = $('#keyInput'), keyMsg = $('#keyMsg'), keySave = $('#keySave');
  function setKeyMsg(text, kind) { keyMsg.className = 'wl-msg' + (kind ? ' ' + kind : ''); keyMsg.textContent = text || ''; }
  function maskKey(k) { k = String(k); return k.length <= 12 ? k.slice(0, 4) + '…' : k.slice(0, 10) + '…' + k.slice(-4); }
  const planName = info => (info && I.has('plan.name.' + info.plan) ? t('plan.name.' + info.plan) : (info && (info.plan_name || info.plan)) || '');
  function renderAcct() {
    $('#keyBtnLabel').textContent = apiKey ? t('key.btnChange') : t('key.btnEnter');
    const bar = $('#acctBar'); bar.hidden = !apiKey;
    if (!apiKey) return;
    const badge = $('#planBadge'), usage = $('#acctUsage'), portal = $('#portalBtn');
    if (!me) {
      badge.textContent = t('acct.key', { k: maskKey(apiKey) }); badge.className = 'plan-badge';
      usage.textContent = t('acct.unknown'); portal.hidden = true; return;
    }
    badge.textContent = planName(me); badge.className = 'plan-badge on';
    const period = quotaPeriod(me);
    let u;
    if (me.limit == null) u = t('acct.unlimited') + (me.used != null ? ' · ' + t('acct.usedOnly.' + period, { n: fmtN(me.used) }) : '');
    else u = t('acct.used.' + period, { used: fmtN(me.used || 0), limit: fmtN(me.limit) }) + (me.reset_at ? ' · ' + t('acct.resets', { d: fmtDate(me.reset_at) }) : '');
    if (me.email) u += ' · ' + me.email;
    usage.textContent = u;
    portal.hidden = me.plan === 'internal';
  }
  function setKey(k, info) {
    apiKey = k; me = info || null; store.set('apikey', k); renderAcct();
  }
  function onInvalidKey() {
    apiKey = ''; me = null; store.del('apikey'); renderAcct();
    showAlert(t('err.keyRemoved'));
    loadQuota(); updateCount();
  }
  async function loadMe() {
    if (!apiKey) { me = null; renderAcct(); return; }
    try { me = await api('/api/me'); }
    catch (e) {
      if (e.code === 'invalid_api_key') return; // onInvalidKey đã gỡ key và báo lỗi
      // Lỗi mạng / Polar tạm lỗi (503 billing_unavailable): giữ key, chỉ chưa hiện được gói.
    }
    renderAcct();
  }
  $('#keyBtn').onclick = () => {
    keyInput.value = ''; setKeyMsg('');
    keyInput.removeAttribute('aria-invalid');
    if (typeof keyDlg.showModal === 'function') keyDlg.showModal(); else keyDlg.setAttribute('open', '');
    keyInput.focus();
  };
  const closeKeyDlg = () => { if (typeof keyDlg.close === 'function') keyDlg.close(); else keyDlg.removeAttribute('open'); $('#keyBtn').focus(); };
  $('#keyCancel').onclick = closeKeyDlg;
  keyInput.addEventListener('input', () => { keyInput.removeAttribute('aria-invalid'); setKeyMsg(''); });
  $('#keyForm').addEventListener('submit', async e => {
    e.preventDefault();
    const k = keyInput.value.replace(/\s+/g, '');
    if (!k) { keyInput.setAttribute('aria-invalid', 'true'); setKeyMsg(t('key.empty'), 'err'); keyInput.focus(); return; }
    keySave.classList.add('busy'); keySave.disabled = true; setKeyMsg(t('key.checking'));
    try {
      const info = await api('/api/me', { key: k });
      setKey(k, info); closeKeyDlg(); hideAlert();
      toast(t('key.saved', { plan: planName(info) }));
      loadQuota(); updateCount();
    } catch (err) {
      keyInput.setAttribute('aria-invalid', String(err.code === 'invalid_api_key'));
      setKeyMsg(err.code === 'invalid_api_key' ? err.message
        : err.code === 'network' ? t('key.netErr')
        : t('key.checkErr', { m: err.message }), 'err');
      keyInput.focus();
    } finally { keySave.classList.remove('busy'); keySave.disabled = false; }
  });
  $('#logoutKey').onclick = () => {
    apiKey = ''; me = null; store.del('apikey'); renderAcct();
    loadQuota(); updateCount();
    toast(t('key.removed'));
    $('#keyBtn').focus();
  };
  $('#portalBtn').onclick = async () => {
    const b = $('#portalBtn'); if (b.disabled) return;
    b.classList.add('busy'); b.disabled = true;
    let leaving = false;
    try {
      const r = await api('/api/billing/portal', { method: 'POST' });
      const url = safeRedirect(r && r.url);
      if (!url) throw new ApiError(t('err.portal'), 'bad_response', 200);
      leaving = true; location.href = url;
    } catch (e) { showAlert(e.message); }
    finally {
      const reset = () => { b.classList.remove('busy'); b.disabled = false; };
      if (!leaving) reset(); else addEventListener('pageshow', ev => { if (ev.persisted) reset(); }, { once: true });
    }
  };

  // ---------- danh sách chờ ----------
  const wl = $('#waitlist'), wlEmail = $('#wlEmail'), wlPlan = $('#wlPlan'), wlMsg = $('#wlMsg'), wlBtn = $('#wlBtn');
  let wlTouched = false, wlDone = null;
  const wlPlanName = v => (I.has('wl.opt.' + v.replace(/^pro_/, '')) ? t('wl.opt.' + v.replace(/^pro_/, '')) : v);
  buyBtn.addEventListener('click', () => {
    if (!billing.loaded || buying || buyBtn.getAttribute('aria-disabled') === 'true') return; // chưa biết billing bật/tắt, đang tạo phiên, hoặc gói không bán
    if (billing.enabled) { buy(buyBtn.dataset.buy); return; }
    wlPlan.value = buyBtn.dataset.buy;
    wl.scrollIntoView({ block: 'center', behavior: reduceMotion.matches ? 'auto' : 'smooth' });
    wlEmail.focus({ preventScroll: true });
  });
  wlPlan.addEventListener('change', () => { wlTouched = true; });
  wlEmail.addEventListener('input', () => wlEmail.removeAttribute('aria-invalid'));
  function renderWlDone() {
    if (!wlDone) return;
    wlMsg.textContent = t('wl.ok', { n: fmtN(wlDone.position), plan: wlPlanName(wlDone.plan), email: wlDone.email });
  }
  wl.addEventListener('submit', async e => {
    e.preventDefault();
    const email = wlEmail.value.trim();
    wlMsg.className = 'wl-msg'; wlDone = null;
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(email)) {
      wlEmail.setAttribute('aria-invalid', 'true'); wlMsg.classList.add('err');
      wlMsg.textContent = t('wl.badEmail'); wlEmail.focus(); return;
    }
    wlBtn.classList.add('busy'); wlBtn.disabled = true;
    try {
      const r = await api('/api/waitlist', { method: 'POST', body: { email, plan: wlPlan.value } });
      wlMsg.classList.add('ok');
      wlDone = { email, plan: wlPlan.value, position: r.position }; renderWlDone();
      store.set('waitlist', wlDone);
    } catch (err) {
      wlMsg.classList.add('err');
      wlMsg.textContent = err.code === 'network' ? t('wl.netErr') : err.message;
    } finally { wlBtn.classList.remove('busy'); wlBtn.disabled = false; }
  });

  // ---------- API mẫu ----------
  $('#copyCurl').onclick = async () => {
    const s = $('#curl code').innerText.split('\n\n')[0];
    try { await navigator.clipboard.writeText(s); toast(t('api.copied')); } catch (e) { toast(t('api.copyFail')); }
  };
  if (/^https?:/.test(location.protocol) && !/dattts\.com$/.test(location.hostname)) {
    const base = API || location.origin;
    const code = $('#curl code'); code.innerHTML = code.innerHTML.replace('https://dattts.com', esc(base));
  }

  // ---------- kết nối máy chủ ----------
  function goOffline() {
    online = false;
    document.body.classList.add('offline');
    $('#offlineBanner').hidden = false;
    $('#quotaBox').hidden = true;
    setBusy(false);
    updateCount();
  }
  let voicesErr = null;
  async function goOnline() {
    const wasOffline = document.body.classList.contains('offline');
    online = true;
    document.body.classList.remove('offline');
    $('#offlineBanner').hidden = true;
    stopSpeech();
    setBusy(false);
    if (!voices.length) {
      try { await loadVoices(); voicesErr = null; }
      catch (e) { voicesErr = e.message; $('#vgrid').innerHTML = `<p class="note warn">${esc(t('voices.loadErr', { m: e.message }))}</p>`; }
    }
    loadQuota();
    if (apiKey) loadMe();
    if (!billing.enabled) loadPlans();
    updateCount();
    if (wasOffline) toast(t('offline.back'));
  }
  async function checkHealth(silent) {
    const ctl = 'AbortController' in window ? new AbortController() : null;
    const timer = ctl && setTimeout(() => ctl.abort(), 6000);
    try {
      const h = await api('/api/health', { signal: ctl && ctl.signal });
      if (h && h.ok === false) throw new ApiError('not ok', 'unhealthy', 503);
      await goOnline();
    } catch (e) { if (!silent || online) goOffline(); }
    finally { clearTimeout(timer); }
  }
  let retrying = false;
  $('#retryBtn').onclick = async () => {
    const b = $('#retryBtn'); b.disabled = true; retrying = true; b.textContent = t('offline.retrying');
    await checkHealth();
    b.disabled = false; retrying = false; b.textContent = t('offline.retry');
    if (!online) toast(t('offline.still'));
  };

  // ---------- đổi ngôn ngữ tại chỗ: vẽ lại mọi phần sinh động ----------
  function relocalize() {
    setRec(recKey, studio.classList.contains('live') ? 'live' : studio.classList.contains('ready') ? 'ready' : '');
    if (!busy) genLabel.textContent = online ? t('gen.label') : t('gen.preview'); else genLabel.textContent = t(busyKey);
    if (retrying) $('#retryBtn').textContent = t('offline.retrying');
    setPlayLabel(); syncRate(); updateTime();
    if (quota) setQuota(quota);
    if (voices.length) { renderInsertOptions(); renderVoices(); renderCurrentVoice(); }
    else if (voicesErr) $('#vgrid').innerHTML = `<p class="note warn">${esc(t('voices.loadErr', { m: voicesErr }))}</p>`;
    loadBrowserVoices();
    renderDict(); renderHistory(); renderTones(); renderPlans(); renderAcct(); renderWlDone();
    renderPlayerHead(); renderDownloads();
    updateCount();
  }
  document.addEventListener('dattts:lang', relocalize);

  // ---------- khởi động ----------
  readAccent(); sizeCanvas(); drawOnce();
  syncRate(); renderDict(); renderHistory(); renderTones(); renderAcct(); setRec('rec.connecting', ''); setPlayLabel();
  const wlSaved = store.get('waitlist', null);
  if (wlSaved && wlSaved.email) { wlEmail.value = wlSaved.email; }
  updateCount();
  if (location.protocol === 'file:' && !API) { goOffline(); billing.loaded = true; renderPlans(); }
  else { renderPlans(); loadPlans(); checkHealth(); } // bảng giá tải song song, không chờ health/giọng
})();
