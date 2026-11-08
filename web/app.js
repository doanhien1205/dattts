/* DatTTS — phòng thu web. Nói chuyện với API theo SPEC (tiền tố /api). */
(() => {
  'use strict';

  // ---------- tiện ích ----------
  const API = String(window.DATTTS_API || '').replace(/\/+$/, '');
  const SYNC_MAX = 3000, JOB_MAX = 100000, DICT_MAX = 200;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const fmtN = n => Math.round(n).toLocaleString('vi-VN');
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
  const INVALID_KEY_MSG = 'API key không còn hợp lệ (gói có thể đã hết hạn hoặc key đã được đổi) nên đã được gỡ khỏi trình duyệt này. Bạn đang dùng lượt miễn phí; hãy kiểm tra lại key trong email của Polar hoặc cổng khách hàng rồi nhập lại.';
  const VN_TZ = { timeZone: 'Asia/Ho_Chi_Minh' };
  function clock(s) {
    s = Math.max(0, Math.floor(s || 0));
    const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, ss = s % 60, p = x => String(x).padStart(2, '0');
    return (h ? h + ':' + p(m) : p(m)) + ':' + p(ss);
  }
  function humanDur(sec) {
    if (sec < 60) return Math.max(1, Math.round(sec)) + ' giây';
    if (sec < 3600) { const m = Math.floor(sec / 60), s = Math.round(sec % 60); return m + ' phút' + (s ? ' ' + s + ' giây' : ''); }
    return (sec / 3600).toFixed(1).replace('.', ',') + ' giờ';
  }
  // Giống count_chars của máy chủ: NFC, bỏ ký tự điều khiển, gộp khoảng trắng, giữ xuống dòng đoạn.
  function cleanText(t) {
    return String(t || '').normalize('NFC').replace(/\r\n?/g, '\n')
      .replace(/[\u0000-\u0009\u000B-\u001F\u007F​-‍﻿]/g, ' ')
      .replace(/[ \t ]+/g, ' ').replace(/ *\n */g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  }
  const countChars = t => cleanText(t).length;

  class ApiError extends Error { constructor(message, code, status) { super(message); this.code = code; this.status = status; } }
  async function api(path, opts = {}) {
    const init = { method: opts.method || 'GET', headers: { Accept: 'application/json' }, signal: opts.signal };
    if (opts.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
    // opts.key: thử một key cụ thể (hộp nhập key); opts.key === '' → không gửi key.
    const sentKey = opts.key !== undefined ? opts.key : apiKey;
    if (sentKey) init.headers['X-API-Key'] = sentKey;
    let res;
    try { res = await fetch(apiUrl(path), init); }
    catch (e) {
      if (e && e.name === 'AbortError') throw new ApiError('Máy chủ phản hồi quá lâu. Thử lại sau ít phút nhé.', 'timeout', 0);
      throw new ApiError('Không kết nối được máy chủ DatTTS. Kiểm tra mạng rồi thử lại.', 'network', 0);
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* không phải JSON */ }
    if (!res.ok) {
      const er = data && data.error;
      if (res.status === 401 && sentKey && (!er || INVALID_KEY_CODES.includes(er.code))) {
        // Key hỏng/hết hạn: nếu là key đang lưu thì gỡ ra để khách biết và quay về lượt miễn phí.
        if (opts.key === undefined && sentKey === apiKey) {
          onInvalidKey();
          throw new ApiError(INVALID_KEY_MSG, 'invalid_api_key', 401);
        }
        throw new ApiError('Key không đúng hoặc đã hết hạn. Kiểm tra lại key trong email Polar gửi bạn (bắt đầu bằng DATTTS).', 'invalid_api_key', 401);
      }
      const fallback = res.status === 429 ? 'Bạn đã dùng hết lượt miễn phí hôm nay.' : res.status >= 500 ? 'Máy chủ đang gặp sự cố. Thử lại sau ít phút nhé.' : 'Yêu cầu chưa hợp lệ (mã ' + res.status + ').';
      throw new ApiError((er && er.message) || fallback, (er && er.code) || 'http_' + res.status, res.status);
    }
    if (data == null) throw new ApiError('Máy chủ trả về dữ liệu không đọc được.', 'bad_response', res.status);
    return data;
  }

  // ---------- phần tử ----------
  const txt = $('#txt'), studio = $('#studio'), audio = $('#audio');
  const genBtn = $('#genBtn'), genLabel = $('#genLabel');
  const player = $('#player'), transcript = $('#transcript'), seek = $('#seek');
  const statusEl = $('#status');
  const sampleAudio = new Audio(); sampleAudio.preload = 'none';

  let online = false, voices = [], voiceMap = new Map(), selectedId = store.get('voice', null), defaultId = 'truc-ly';
  let busy = false, current = null, showAllVoices = false;

  function announce(m) { statusEl.textContent = ''; setTimeout(() => { statusEl.textContent = m; }, 30); }
  function toast(m) { const t = $('#toast'); t.textContent = m; t.hidden = false; clearTimeout(t._h); t._h = setTimeout(() => { t.hidden = true; }, 3200); }
  function setRec(label, cls) {
    $('#recLabel').textContent = label;
    studio.classList.toggle('live', cls === 'live'); studio.classList.toggle('ready', cls === 'ready');
  }

  // ---------- thông báo lỗi trong trang ----------
  const alertEl = $('#alert');
  function showAlert(msg, kind) {
    alertEl.className = 'alert' + (kind === 'info' ? ' info' : '');
    alertEl.setAttribute('role', kind === 'info' ? 'status' : 'alert');
    alertEl.innerHTML = '<span>' + esc(msg) + '</span><button class="x" type="button" aria-label="Đóng thông báo">×</button>';
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
  const genderLabel = g => (genderKey(g) === 'female' ? 'Nữ' : 'Nam');
  function regionKey(r) {
    const s = String(r || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
    return /bac|north/.test(s) ? 'Bắc' : /trung|central/.test(s) ? 'Trung' : /nam|south/.test(s) ? 'Nam' : String(r || '');
  }
  const regionClass = r => ({ 'Bắc': 'r-bac', 'Trung': 'r-trung', 'Nam': 'r-nam' }[r] || 'r-bac');
  const styleLabel = v => v.style_label || ({ tu_nhien: 'Tự nhiên', tin_tuc: 'Tin tức', doc_truyen: 'Kể chuyện' }[v.style] || '');
  const voiceMeta = v => [genderLabel(v.gender), v.region, styleLabel(v)].filter(Boolean).join(' · ');
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

  async function loadVoices() {
    const data = await api('/api/voices');
    defaultId = data.default || defaultId;
    voices = (data.voices || []).map(v => Object.assign({}, v, { region: regionKey(v.region), gender: genderKey(v.gender) }));
    voices.sort((a, b) => (a.featured == null) - (b.featured == null) || (a.featured || 0) - (b.featured || 0));
    voiceMap = new Map(voices.map(v => [v.id, v]));
    if (!voiceMap.has(selectedId)) selectedId = voiceMap.has(defaultId) ? defaultId : (voices[0] && voices[0].id);
    const ins = $('#insertVoice');
    ins.innerHTML = '<option value="">＋ Chèn giọng…</option>' +
      voices.map(v => `<option value="${esc(v.name)}">${esc(v.name)} (${esc(voiceMeta(v))})</option>`).join('');
    renderVoices(); renderCurrentVoice();
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
        <button class="vpick" type="button" aria-pressed="${sel}" title="${esc(v.description || '')}">
          <span class="avatar ${regionClass(v.region)}" aria-hidden="true">${esc(initials(v.name))}</span>
          <span class="txt"><b>${esc(v.name)}</b><small>${esc(voiceMeta(v))}</small></span>
        </button>
        <button class="vplay" type="button" aria-label="Nghe mẫu giọng ${esc(v.name)}">
          <svg class="i-play" viewBox="0 0 14 14" aria-hidden="true"><path d="M3 1.5v11l9.5-5.5z"/></svg>
          <svg class="i-pause" viewBox="0 0 14 14" aria-hidden="true"><path d="M3 1.5h3v11H3zM8 1.5h3v11H8z"/></svg>
        </button>
        ${v.featured != null && v.featured <= 3 ? '<span class="star" aria-hidden="true">Nổi bật</span>' : ''}
      </div>`;
    }).join('');
    $('#voiceCount').textContent = filtered ? `${list.length}/${voices.length} giọng` : `${voices.length} giọng`;
    $('#vEmpty').hidden = list.length > 0;
    const more = $('#moreVoices');
    const hiddenN = list.length - shown.length;
    more.hidden = filtered || (hiddenN <= 0 && !showAllVoices);
    more.textContent = showAllVoices ? 'Thu gọn' : `Xem thêm ${hiddenN} giọng`;
    more.setAttribute('aria-expanded', String(showAllVoices));
    // giữ trạng thái nút nghe mẫu khi vẽ lại
    if (!sampleAudio.paused && sampleAudio.dataset.voice) {
      const b = $(`.vcard[data-id="${CSS.escape(sampleAudio.dataset.voice)}"] .vplay`, grid);
      if (b) setSampleBtn(b, true);
    }
  }
  function renderCurrentVoice() {
    const v = voiceMap.get(selectedId); if (!v) return;
    $('#currentVoice').innerHTML = `<span class="avatar ${regionClass(v.region)}" aria-hidden="true">${esc(initials(v.name))}</span>
      <span><b>${esc(v.name)}</b><small>${esc(voiceMeta(v))}${v.description && !/·/.test(v.description) ? ' — ' + esc(v.description) : ''}</small></span>`;
  }
  function selectVoice(id, quiet) {
    if (!voiceMap.has(id)) return;
    selectedId = id; store.set('voice', id);
    $$('.vcard', $('#vgrid')).forEach(c => {
      const on = c.dataset.id === id; c.classList.toggle('sel', on); $('.vpick', c).setAttribute('aria-pressed', String(on));
    });
    renderCurrentVoice();
    if (!quiet) announce('Đã chọn giọng ' + voiceName(id));
  }
  function setSampleBtn(b, on) {
    b.classList.toggle('playing', on);
    const name = voiceName(b.closest('.vcard').dataset.id);
    b.setAttribute('aria-label', (on ? 'Dừng mẫu giọng ' : 'Nghe mẫu giọng ') + name);
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
    if (playingVoice) toast('Chưa nghe được giọng mẫu. Thử lại sau giây lát.');
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
    announce('Đã chèn thẻ giọng ' + name);
  });

  function pickVoiceName(pref, gender, avoid) {
    const v = voiceByAny(pref);
    if (v && v.name !== avoid) return v.name;
    const alt = voices.find(x => x.gender === gender && x.name !== avoid);
    return alt ? alt.name : pref;
  }
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
    if (b.dataset.sample === 'podcast') announce('Đã điền mẫu podcast hai giọng');
  }));

  const rate = $('#rate');
  function syncRate() { $('#rateV').textContent = (+rate.value).toFixed(2).replace('.', ',') + '×'; updateCount(); }
  rate.addEventListener('input', syncRate);
  rate.value = store.get('speed', 1); rate.addEventListener('change', () => store.set('speed', +rate.value));
  const savedFmt = store.get('format', 'mp3');
  $$('input[name="fmt"]').forEach(r => { r.checked = r.value === savedFmt; r.addEventListener('change', () => store.set('format', r.value)); });
  const getFmt = () => ($('input[name="fmt"]:checked') || { value: 'mp3' }).value;

  let quota = null;
  function updateCount() {
    const n = countChars(txt.value);
    $('#count').textContent = fmtN(n) + ' ký tự';
    const mode = $('#mode');
    mode.className = '';
    if (!online) mode.textContent = '';
    else if (n > JOB_MAX) { mode.textContent = 'vượt giới hạn ' + fmtN(JOB_MAX) + ' ký tự'; mode.className = 'over'; }
    else if (quota && quota.remaining != null && n > quota.remaining) { mode.textContent = apiKey ? 'vượt hạn mức còn lại tháng này' : 'vượt lượt còn lại hôm nay'; mode.className = 'over'; }
    else if (n > SYNC_MAX) mode.textContent = 'văn bản dài · chạy nền';
    else mode.textContent = 'tạo ngay';
    const sec = n / 25 / parseFloat(rate.value || 1);
    $('#estTime').textContent = '≈ ' + humanDur(sec) + ' âm thanh';
  }
  txt.addEventListener('input', updateCount);
  txt.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); generate(); } });

  // ---------- quota ----------
  function setQuota(q) {
    if (!q) return;
    quota = q;
    const box = $('#quotaBox'); box.hidden = false;
    const unlimited = q.limit == null || q.limit <= 0 || q.remaining == null;
    if (unlimited) { $('#quotaText').textContent = 'Không giới hạn ký tự'; $('#quotaFill').style.width = '100%'; box.className = 'quota'; return; }
    const rem = Math.max(0, q.remaining);
    $('#quotaText').textContent = `${apiKey ? 'Tháng này còn' : 'Hôm nay còn'} ${fmtN(rem)} / ${fmtN(q.limit)} ký tự`;
    $('#quotaFill').style.width = Math.max(0, Math.min(100, rem / q.limit * 100)) + '%';
    box.className = 'quota' + (rem === 0 ? ' out' : rem < q.limit * 0.15 ? ' low' : '');
    box.title = q.reset_at ? 'Làm mới lúc ' + fmtDateTime(q.reset_at) : '';
    updateCount();
  }
  // Hạn mức reset theo giờ Việt Nam → hiển thị theo giờ Việt Nam, dạng 00:00 01/12.
  const fmtDate = iso => { const p = new Intl.DateTimeFormat('en-GB', Object.assign({ day: '2-digit', month: '2-digit' }, VN_TZ)).formatToParts(new Date(iso)); const g = t => (p.find(x => x.type === t) || {}).value; return g('day') + '/' + g('month'); };
  const fmtDateTime = iso => new Intl.DateTimeFormat('en-GB', Object.assign({ hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }, VN_TZ)).format(new Date(iso)) + ' ' + fmtDate(iso) + ' (giờ VN)';
  async function loadQuota() { try { setQuota(await api('/api/quota')); } catch (e) { /* không bắt buộc */ } }

  // ---------- từ điển phát âm ----------
  const DICT_DEFAULT = [{ from: 'HLV', to: 'huấn luyện viên' }, { from: 'AI', to: 'ây ai' }];
  let dict = store.get('dict', null);
  if (!Array.isArray(dict)) dict = DICT_DEFAULT.map(x => Object.assign({}, x));
  function saveDict() { store.set('dict', dict); const n = dictEntries().length; $('#dictCount').textContent = n ? `(${n} mục)` : ''; }
  function dictEntries() {
    return dict.map(d => ({ from: String(d.from || '').trim().slice(0, 100), to: String(d.to || '').trim().slice(0, 100) }))
      .filter(d => d.from && d.to).slice(0, DICT_MAX);
  }
  function renderDict() {
    const box = $('#dictRows');
    box.innerHTML = dict.map((d, i) => `<div class="drow" role="listitem">
      <input type="text" maxlength="100" value="${esc(d.from || '')}" data-i="${i}" data-k="from" aria-label="Từ gốc, dòng ${i + 1}" placeholder="Từ gốc">
      <span class="arr" aria-hidden="true">→</span>
      <input type="text" maxlength="100" value="${esc(d.to || '')}" data-i="${i}" data-k="to" aria-label="Cách đọc, dòng ${i + 1}" placeholder="Cách đọc">
      <button class="iconbtn" type="button" data-del="${i}" aria-label="Xoá dòng ${i + 1}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg></button>
    </div>`).join('');
    $('#dictAdd').disabled = dict.length >= DICT_MAX;
    saveDict();
  }
  $('#dictRows').addEventListener('input', e => { const t = e.target; if (t.dataset.i == null) return; dict[+t.dataset.i][t.dataset.k] = t.value; saveDict(); });
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
    ptrack.setAttribute('aria-valuetext', label || (kind === 'indet' ? 'Đang xử lý' : pct + '%'));
    ptext.textContent = label || '';
    // Báo mốc 25/50/75% cho trình đọc màn hình khi job nền chạy lâu
    if (kind === 'off' || (kind === 'det' && pct < 25)) lastMilestone = 0;
    if (kind === 'det' && pct < 100) {
      const m = Math.floor(pct / 25) * 25;
      if (m >= 25 && m > lastMilestone) { lastMilestone = m; announce(`Đã tạo được ${m}%`); }
    }
  }
  function setBusy(on, label) {
    busy = on;
    genBtn.classList.toggle('busy', on); genBtn.disabled = on;
    genBtn.setAttribute('aria-busy', String(on));
    genLabel.textContent = on ? (label || 'Đang tạo…') : (online ? 'Tạo giọng đọc' : 'Đọc thử');
    if (on) setRec('ĐANG TẠO GIỌNG', 'live'); else setRec(online ? 'PHÒNG THU · SẴN SÀNG' : 'PHÒNG THU · CHẾ ĐỘ THỬ', online ? 'ready' : '');
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
      if (s.status === 'error') { const er = s.error || {}; throw new ApiError(er.message || 'Không tạo được giọng đọc. Thử lại nhé.', er.code || 'job_error', 500); }
    }
  }
  function progressLabel(s) {
    if (s.status === 'sync') return ['indet', 0, 'Đang đọc văn bản…'];
    if (s.status === 'queued') return ['det', 0.02, 'Đang xếp hàng…'];
    const f = s.progress != null ? s.progress : (s.total ? s.done / s.total : 0);
    return ['det', Math.max(0.03, f), (s.total ? `Đã đọc ${fmtN(s.done || 0)}/${fmtN(s.total)} câu · ` : '') + Math.round(f * 100) + '%'];
  }

  async function generate() {
    if (busy) return;
    if (!online) { speakOffline(); return; }
    const n = countChars(txt.value);
    if (!n) { showAlert('Bạn chưa nhập văn bản. Hãy gõ hoặc dán nội dung cần đọc.'); txt.focus(); return; }
    if (n > JOB_MAX) { showAlert(`Văn bản dài ${fmtN(n)} ký tự, vượt giới hạn ${fmtN(JOB_MAX)} ký tự mỗi lần. Hãy chia thành nhiều phần.`); return; }
    hideAlert(); exclusive('main');
    primeAudio(); // trong cử chỉ người dùng (trước mọi await) — Safari/iOS bắt buộc
    const body = buildBody();
    setBusy(true, n > SYNC_MAX ? 'Đang xử lý…' : 'Đang tạo…');
    const t0 = performance.now();
    try {
      const res = await synthesize(body, s => { const [k, f, l] = progressLabel(s); setProgress(k, f, l); });
      setProgress('det', 1, 'Xong!');
      loadResult(res, body, true);
      addHistory(res, body);
      if (res.quota) setQuota(res.quota); else loadQuota();
      if (apiKey && me) loadMe();
      const secs = ((performance.now() - t0) / 1000).toFixed(1).replace('.', ',');
      announce(`Đã tạo xong ${humanDur(res.duration || 0)} âm thanh` + (res.cached ? ' (bản có sẵn, không tính ký tự)' : ` trong ${secs} giây`));
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
  let segs = [], segEls = [], curIdx = -2, raf = 0, dragging = false;
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
    const voicesUsed = Array.from(new Set(segs.map(s => voiceName(s.voice)).filter(Boolean)));
    $('#playerTitle').textContent = res.cached ? 'Bản đọc · có sẵn' : 'Bản đọc';
    $('#playerMeta').textContent = [clock(res.duration), fmtN(res.chars || 0) + ' ký tự', voicesUsed.join(', '), (res.format || '').toUpperCase()].filter(Boolean).join(' · ');
    renderDownloads();
    player.hidden = false;
    audio.src = apiUrl(res.audio_url);
    audio.load();
    seek.value = 0; updateTime();
    paint(-1);
    if (autoplay) { exclusive('main'); audio.play().catch(() => {}); }
  }
  function renderDownloads() {
    const r = current.res, f0 = r.format || 'mp3';
    const icon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11m-5-5 5 5 5-5M5 20h14"/></svg>';
    let h = '<span class="lbl">Tải về</span>';
    ['mp3', 'wav', 'ogg'].forEach(f => {
      const ready = f === f0 ? r.audio_url : current.alts[f];
      if (ready) h += `<a class="dl" href="${esc(withDownload(apiUrl(ready)))}" download data-fmt="${f}" aria-label="Tải file ${f.toUpperCase()}">${icon}${f.toUpperCase()}</a>`;
      else if (current.body) h += `<button class="dl alt" type="button" data-alt="${f}" aria-label="Tạo bản ${f.toUpperCase()} rồi tải (không tính thêm ký tự)" title="Đổi sang ${f.toUpperCase()} từ bản vừa nghe rồi tải — không tính thêm ký tự">${icon}${f.toUpperCase()}</button>`;
    });
    if (r.srt_url) h += `<a class="dl" href="${esc(withDownload(apiUrl(r.srt_url)))}" download data-fmt="srt" aria-label="Tải phụ đề SRT">${icon}SRT</a>`;
    if (r.vtt_url) h += `<a class="dl" href="${esc(withDownload(apiUrl(r.vtt_url)))}" download data-fmt="vtt" aria-label="Tải phụ đề VTT">${icon}VTT</a>`;
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
      announce('Đã tạo bản ' + f.toUpperCase() + ', đang tải về');
    } catch (err) {
      showAlert(err.message);
      if (current === cur) renderDownloads();
    } finally { delete cur.pending[f]; }
  });

  function findSeg(t) {
    let lo = 0, hi = segs.length - 1, ans = -1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (segs[mid].start <= t + 0.02) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
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
    const d = audioDur(), t = audio.currentTime || 0;
    if (!dragging) seek.value = d ? Math.round(t / d * 1000) : 0;
    $('#time').textContent = clock(t) + ' / ' + clock(d);
    seek.setAttribute('aria-valuetext', clock(t) + ' trên ' + clock(d));
  }
  function sync() {
    updateTime();
    if (segs.length && current) paint(audio.ended ? segs.length : findSeg(audio.currentTime || 0));
  }
  function tick() { sync(); raf = audio.paused ? 0 : requestAnimationFrame(tick); }
  const playBtn = $('#playPause');
  audio.addEventListener('play', () => {
    exclusive('main');
    playBtn.classList.add('playing'); playBtn.setAttribute('aria-label', 'Tạm dừng');
    setRec('ĐANG PHÁT', 'live'); speaking = true; ensureAnalyser();
    if (!raf) raf = requestAnimationFrame(tick);
    kickWave();
  });
  function onStop() {
    playBtn.classList.remove('playing'); playBtn.setAttribute('aria-label', 'Phát');
    if (!busy) setRec(online ? 'PHÒNG THU · SẴN SÀNG' : 'PHÒNG THU · CHẾ ĐỘ THỬ', online ? 'ready' : '');
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
    showAlert('Không mở được file âm thanh. Có thể file đã hết hạn trên máy chủ — hãy bấm “Tạo giọng đọc” để tạo lại.');
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
  function draw(t) {
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
        const n = Math.sin(i * 0.35 + t / 180) * Math.sin(i * 0.11 - t / 430) + Math.sin(i * 1.7 + t / 90) * 0.35;
        a = (Math.abs(n) * 0.8 + 0.12) * energy * (h * 0.42) + 1.5;
      }
      cx.globalAlpha = 0.35 + 0.65 * energy; cx.fillRect(x, h / 2 - a, 3, a * 2);
    }
    cx.globalAlpha = 1;
  }
  function loop(t) { draw(t); waveRaf = (visible && !reduceMotion.matches && (speaking || energy > 0.09)) ? requestAnimationFrame(loop) : 0; }
  function kickWave() { if (!waveRaf && !reduceMotion.matches) waveRaf = requestAnimationFrame(loop); else if (reduceMotion.matches) draw(0); }
  function drawOnce() { draw(performance.now()); kickWave(); }
  if ('IntersectionObserver' in window) new IntersectionObserver(es => { visible = es[0].isIntersecting; if (visible) kickWave(); }).observe(cv);
  addEventListener('resize', () => { sizeCanvas(); drawOnce(); });

  // ---------- chế độ thử trên trình duyệt (speechSynthesis) ----------
  const synth = window.speechSynthesis;
  let bVoices = [], viVoice = null, fallbackTimer = null, wordSpans = [], gotBoundary = false;
  function loadBrowserVoices() {
    if (!synth) { $('#voiceNote').textContent = 'Trình duyệt này không hỗ trợ đọc thử. Hãy dùng Chrome, Edge hoặc Safari.'; $('#voiceNote').className = 'note warn'; return; }
    bVoices = synth.getVoices();
    const sel = $('#voiceSel'); sel.innerHTML = '';
    const vi = bVoices.filter(v => /^vi/i.test(v.lang));
    const list = vi.length ? vi : bVoices;
    list.forEach(v => { const o = document.createElement('option'); o.value = bVoices.indexOf(v); o.textContent = v.name + ' (' + v.lang + ')'; sel.appendChild(o); });
    if (!list.length) { const o = document.createElement('option'); o.textContent = 'Giọng mặc định của máy'; o.value = ''; sel.appendChild(o); }
    viVoice = vi[0] || null;
    const note = $('#voiceNote');
    if (viVoice) { sel.value = bVoices.indexOf(viVoice); note.className = 'note'; note.textContent = `Máy bạn có ${vi.length} giọng tiếng Việt. Giọng AI của DatTTS tự nhiên hơn nhiều khi máy chủ hoạt động.`; }
    else { note.className = 'note warn'; note.textContent = 'Máy bạn chưa cài giọng tiếng Việt nên bản thử sẽ đọc lơ lớ. Mở bằng Chrome hoặc Edge để nghe giọng Việt chuẩn hơn.'; }
  }
  if (synth) { loadBrowserVoices(); if ('onvoiceschanged' in synth) synth.onvoiceschanged = loadBrowserVoices; }
  function stopSpeech() {
    if (synth && (synth.speaking || synth.pending)) synth.cancel();
    clearInterval(fallbackTimer); fallbackTimer = null;
    if (!online) { speaking = false; setRec('PHÒNG THU · CHẾ ĐỘ THỬ', ''); }
  }
  function markWord(idx) { wordSpans.forEach((s, i) => { s.className = i < idx ? 'seg said' : i === idx ? 'seg now' : 'seg'; }); }
  function speakOffline() {
    const text = cleanText(txt.value.replace(/\[[^\]\n]{1,40}\]\s*/g, ''));
    if (!text) { showAlert('Bạn chưa nhập văn bản. Hãy gõ hoặc dán nội dung cần đọc.'); return; }
    if (!synth) { showAlert('Trình duyệt này không hỗ trợ đọc thử. Hãy dùng Chrome, Edge hoặc Safari.'); return; }
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
    $('#playerTitle').textContent = 'Đọc thử trên trình duyệt';
    $('#playerMeta').textContent = fmtN(text.length) + ' ký tự · karaoke ước lượng';
    player.hidden = false;
    const u = new SpeechSynthesisUtterance(text);
    const v = bVoices[+$('#voiceSel').value]; if (v) u.voice = v;
    u.lang = (v && v.lang) || 'vi-VN'; u.rate = +rate.value;
    gotBoundary = false;
    u.onstart = () => {
      speaking = true; kickWave(); setRec('ĐANG ĐỌC THỬ', 'live'); markWord(0);
      const per = Math.max(180, 380 / u.rate); let k = 0;
      fallbackTimer = setInterval(() => { if (gotBoundary) { clearInterval(fallbackTimer); return; } k = Math.min(k + 1, wordSpans.length - 1); markWord(k); }, per);
    };
    u.onboundary = e => {
      if (e.name && e.name !== 'word') return; gotBoundary = true;
      let k = 0; for (let i = 0; i < wordSpans.length; i++) if (+wordSpans[i].dataset.c <= e.charIndex) k = i; markWord(k);
    };
    u.onend = () => { clearInterval(fallbackTimer); markWord(wordSpans.length); speaking = false; setRec('PHÒNG THU · CHẾ ĐỘ THỬ', ''); };
    u.onerror = () => { clearInterval(fallbackTimer); speaking = false; setRec('PHÒNG THU · CHẾ ĐỘ THỬ', ''); };
    synth.speak(u);
    announce('Đang đọc thử bằng giọng của trình duyệt');
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
    $('#histCount').textContent = history.length ? `(${history.length})` : '';
    ol.innerHTML = history.map((h, i) => {
      const when = new Date(h.at).toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' });
      return `<li class="hitem">
        <button class="iconbtn" type="button" data-replay="${i}" aria-label="Nghe lại: ${esc(h.excerpt.slice(0, 40))}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4.5v15l13-7.5z"/></svg></button>
        <span class="htxt"><span>${esc(h.excerpt)}</span><small>${esc(voiceName(h.voice))} · ${clock(h.duration)} · ${(h.format || '').toUpperCase()} · ${when}</small></span>
        <a class="iconbtn" href="${esc(withDownload(apiUrl(h.audio_url)))}" download aria-label="Tải ${(h.format || '').toUpperCase()}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11m-5-5 5 5 5-5M5 20h14"/></svg></a>
      </li>`;
    }).join('') + (history.length ? '' : '');
  }
  $('#history').addEventListener('click', e => {
    const b = e.target.closest('[data-replay]'); if (!b) return;
    const h = history[+b.dataset.replay]; if (!h) return;
    if (!online) { showAlert('Cần kết nối máy chủ để nghe lại bản đã tạo.'); return; }
    hideAlert();
    primeAudio();
    // Dùng đúng từ điển lúc tạo bản này (không phải từ điển hiện tại) để bản WAV/OGG khớp bản đã nghe
    const body = { text: h.text || h.excerpt, voice: h.voice, speed: h.speed || 1, format: h.format };
    if (h.dict) body.dictionary = h.dict;
    loadResult({ id: h.id, format: h.format, duration: h.duration, chars: h.chars, audio_url: h.audio_url, srt_url: h.srt_url, vtt_url: h.vtt_url, segments: h.segments, text: h.excerpt, cached: true }, h.text ? body : null, true);
    player.scrollIntoView({ block: 'nearest', behavior: reduceMotion.matches ? 'auto' : 'smooth' });
  });

  // ---------- thanh điệu ----------
  const tones = [
    { syl: 'ma', nm: 'Ngang', ex: 'con ma', p: 'M4 12 L96 12' },
    { syl: 'mà', nm: 'Huyền', ex: 'nhưng mà', p: 'M4 14 C40 16 70 20 96 24' },
    { syl: 'má', nm: 'Sắc', ex: 'mẹ, má', p: 'M4 14 C40 14 70 10 96 3' },
    { syl: 'mả', nm: 'Hỏi', ex: 'mồ mả', p: 'M4 12 C35 22 60 24 96 12' },
    { syl: 'mã', nm: 'Ngã', ex: 'ngựa, mã số', p: 'M4 12 C30 16 44 20 50 16 L56 22 C70 10 84 6 96 3' },
    { syl: 'mạ', nm: 'Nặng', ex: 'cây mạ', p: 'M4 14 C40 18 70 24 82 26 L84 26' }
  ];
  const toneCache = new Map();
  const tg = $('#tones');
  tones.forEach(t => {
    const b = document.createElement('button'); b.className = 'tone'; b.type = 'button';
    b.setAttribute('aria-label', `Nghe “${t.syl}”, thanh ${t.nm.toLowerCase()}, như trong “${t.ex}”`);
    b.innerHTML = `<span class="syl" aria-hidden="true">${t.syl}</span><svg viewBox="0 0 100 28" preserveAspectRatio="none" aria-hidden="true"><path d="${t.p}"/></svg><span class="nm" aria-hidden="true">${t.nm}</span><span class="ex" aria-hidden="true">${t.ex}</span>`;
    b.onclick = () => playTone(t, b);
    tg.appendChild(b);
  });
  function speakTone(t, b) {
    if (!synth) { toast('Trình duyệt này không đọc được âm thanh.'); return; }
    synth.cancel(); const u = new SpeechSynthesisUtterance(t.syl); if (viVoice) u.voice = viVoice; u.lang = 'vi-VN'; u.rate = 0.8;
    b.classList.add('playing'); u.onend = u.onerror = () => b.classList.remove('playing'); synth.speak(u);
  }
  async function playTone(t, b) {
    exclusive('sample');
    if (!online) { speakTone(t, b); return; }
    b.classList.add('playing');
    const key = (selectedId || defaultId) + '|' + t.syl;
    try {
      let url = toneCache.get(key);
      if (!url) {
        const res = await api('/api/tts', { method: 'POST', body: { text: t.syl + '.', voice: selectedId || defaultId, format: 'mp3' } });
        url = apiUrl(res.audio_url); toneCache.set(key, url); if (res.quota) setQuota(res.quota);
      }
      if (!b.classList.contains('playing')) return;
      sampleAudio.src = url; sampleAudio.dataset.voice = '';
      await sampleAudio.play();
    } catch (e) { b.classList.remove('playing'); speakTone(t, b); }
  }

  // ---------- máy tính chi phí (thanh trượt log 500 → 2.000.000 ký tự) ----------
  const VND_PER_USD = Number(window.DATTTS_VND_PER_USD) > 0 ? Number(window.DATTTS_VND_PER_USD) : 25400;
  // Giá gói (đơn vị nhỏ nhất, ví dụ cent) và hạn mức lấy từ /api/billing/plans; chưa có thì để null.
  let billing = { enabled: false, loaded: false, plans: [] };
  const planById = id => billing.plans.find(p => p && p.id === id) || null;
  const toVnd = p => (p && p.price != null && String(p.currency || 'usd').toLowerCase() === 'usd') ? Math.round(p.price / 100 * VND_PER_USD / 1000) * 1000 : null;
  function fmtMoney(p) {
    const cur = String(p.currency || 'usd').toUpperCase();
    try { return new Intl.NumberFormat('en-US', { style: 'currency', currency: cur, minimumFractionDigits: p.price % 100 ? 2 : 0 }).format(p.price / 100); }
    catch (e) { return (p.price / 100).toFixed(2) + ' ' + cur; }
  }
  const intervalLabel = i => (i === 'year' ? 'năm' : i === 'month' ? 'tháng' : '');
  const cs = $('#chars');
  $('#fxRate').textContent = fmtN(VND_PER_USD);
  const toChars = v => Math.round(500 * Math.pow(4000, v / 100) / 100) * 100 || 500;
  const toSlider = c => Math.log(c / 500) / Math.log(4000) * 100;
  function calc() {
    const c = toChars(+cs.value); $('#charsV').textContent = fmtN(c);
    const min = c / 1500;
    $('#audioLen').textContent = '≈ ' + (min < 1 ? Math.max(1, Math.round(min * 60)) + ' giây' : (min < 60 ? Math.round(min) + ' phút' : (min / 60).toFixed(1).replace('.', ',') + ' giờ')) + ' âm thanh';
    const pers = planById('personal'), unl = planById('unlimited');
    const persChars = (pers && pers.monthly_chars) || 1000000;
    let ours, plan, planObj = null;
    // Miễn phí 20.000 ký tự MỖI NGÀY ≈ 600.000 ký tự/tháng nếu chia đều
    if (c <= 20000 * 30) { ours = 0; plan = c <= 20000 ? 'nằm trong lượt miễn phí hằng ngày' : 'miễn phí nếu chia đều ≤ 20.000 ký tự/ngày'; }
    else if (c <= persChars) { planObj = pers; ours = toVnd(pers); plan = 'gói Cá nhân'; }
    else { planObj = unl; ours = toVnd(unl); plan = 'gói Không giới hạn'; }
    const intl = c / 1e6 * 16 * VND_PER_USD, human = min * 150000;
    if (ours == null) {
      $('#ourCost').innerHTML = esc(plan.charAt(0).toUpperCase() + plan.slice(1)) + ' <small>' + (billing.loaded ? 'giá xem ở bảng giá bên dưới' : 'đang tải giá…') + '</small>';
    } else {
      const usd = planObj && planObj.price != null ? ' · ' + fmtMoney(planObj) + ' / ' + intervalLabel(planObj.interval || 'month') : '';
      $('#ourCost').innerHTML = (ours ? '≈ ' : '') + fmtN(ours) + 'đ <small>' + esc(plan + usd) + '</small>';
    }
    const rows = [['TTS quốc tế', intl, ''], ['Thuê người đọc', human, '']];
    if (ours != null) rows.unshift(['DatTTS', ours, 'us']);
    const max = Math.max(...rows.map(r => r[1]), 1);
    $('#cmp').innerHTML = rows
      .map(([n, v, k]) => `<div class="bar ${k}"><span>${n}</span><div class="track"><div class="fill" style="width:${Math.max(1.5, v / max * 100)}%"></div></div><span class="v">${fmtN(v)}đ</span></div>`).join('');
  }
  cs.addEventListener('input', calc);
  $$('[data-chars]').forEach(b => b.addEventListener('click', () => { cs.value = toSlider(+b.dataset.chars); calc(); }));
  cs.value = toSlider(15000); calc();

  // ---------- bảng giá + mua gói qua Polar ----------
  const PLAN_LABEL = { personal: 'Cá nhân', unlimited: 'Không giới hạn' };
  const buyMsg = $('#buyMsg');
  function setBuyMsg(text, kind) { buyMsg.className = 'buy-msg' + (kind ? ' ' + kind : ''); buyMsg.textContent = text || ''; }
  function renderPlans() {
    $$('.plan[data-plan-id]').forEach(card => {
      const id = card.dataset.planId; if (id === 'free') return;
      const p = planById(id), priceEl = $('[data-price]', card), vndEl = $('[data-vnd]', card), q = $('[data-quota]', card);
      if (p && p.price != null) {
        const per = intervalLabel(p.interval);
        priceEl.innerHTML = esc(fmtMoney(p)) + (per ? ' <small>/ ' + per + '</small>' : '');
        const v = toVnd(p);
        vndEl.hidden = v == null;
        if (v != null) vndEl.textContent = '≈ ' + fmtN(v) + 'đ' + (per ? ' / ' + per : '') + ' · quy đổi tham khảo';
      } else {
        priceEl.innerHTML = '<span class="price-ph">' + (!billing.loaded ? 'Đang tải giá…' : billing.enabled ? 'Giá hiện ở bước thanh toán' : 'Sắp mở bán') + '</span>';
        vndEl.hidden = true;
      }
      if (q && p && p.monthly_chars) q.textContent = (id === 'unlimited' ? 'Dùng hợp lý tới ' : '') + fmtN(p.monthly_chars) + ' ký tự mỗi tháng';
      const btn = $('[data-buy]', card), lbl = $('.lbl', btn);
      lbl.textContent = billing.enabled ? 'Mua gói ' + PLAN_LABEL[id] : 'Sắp mở bán · Giữ chỗ';
      btn.setAttribute('aria-label', billing.enabled ? `Mua gói ${PLAN_LABEL[id]}, chuyển sang trang thanh toán của Polar` : `Gói ${PLAN_LABEL[id]} sắp mở bán, giữ chỗ trong danh sách chờ`);
    });
    const unl = planById('unlimited');
    if (unl && unl.monthly_chars) $('#plansNote').innerHTML = `“Không giới hạn” nghĩa là dùng hợp lý: tối đa ${fmtN(unl.monthly_chars)} ký tự mỗi tháng cho một key, đủ cho hầu hết người làm nội dung. Cần nhiều hơn, hãy viết cho <a href="mailto:support@dattts.com">support@dattts.com</a>.`;
    $('#waitlist').hidden = billing.enabled;
    $('#bang-gia').classList.toggle('billing-on', billing.enabled);
    if (billing.loaded && !billing.enabled) $('#pricingLead').textContent = 'Gói miễn phí dùng được ngay trong phòng thu, không cần đăng ký. Gói trả phí sắp mở bán qua Polar (thẻ quốc tế, Apple Pay, Google Pay) — để lại email để được báo khi mở.';
  }
  async function loadPlans() {
    try {
      const d = await api('/api/billing/plans', { key: '' });
      billing = { enabled: !!d.enabled, loaded: true, plans: Array.isArray(d.plans) ? d.plans : [] };
    } catch (e) {
      billing = { enabled: false, loaded: true, plans: [] };
    }
    renderPlans(); calc();
  }
  const safeRedirect = u => { try { const x = new URL(u, location.href); return /^https?:$/.test(x.protocol) ? x.href : null; } catch (e) { return null; } };
  async function buy(plan, btn) {
    if (btn.classList.contains('busy')) return;
    const lbl = $('.lbl', btn), old = lbl.textContent;
    btn.classList.add('busy'); btn.disabled = true; btn.setAttribute('aria-busy', 'true');
    lbl.textContent = 'Đang mở trang thanh toán…'; setBuyMsg('');
    let leaving = false;
    try {
      const r = await api('/api/billing/checkout', { method: 'POST', body: { plan }, key: '' });
      const url = safeRedirect(r && r.url);
      if (!url) throw new ApiError('Chưa tạo được phiên thanh toán. Thử lại sau ít phút nhé.', 'bad_response', 200);
      leaving = true;
      setBuyMsg('Đang chuyển sang trang thanh toán an toàn của Polar…', 'ok');
      location.href = url;
    } catch (e) {
      if (e.code === 'billing_disabled') { billing.enabled = false; renderPlans(); setBuyMsg('Gói trả phí tạm thời chưa mở bán. Bạn có thể để lại email trong danh sách chờ bên dưới.', 'err'); }
      else setBuyMsg(e.code === 'network' ? 'Không kết nối được máy chủ để tạo phiên thanh toán. Kiểm tra mạng rồi thử lại.' : e.message, 'err');
    } finally {
      // Khi đang chuyển trang thì giữ nút bận; nếu người dùng bấm Back (bfcache) thì mở lại nút.
      const reset = () => { btn.classList.remove('busy'); btn.disabled = false; btn.removeAttribute('aria-busy'); lbl.textContent = old; };
      if (!leaving) reset(); else addEventListener('pageshow', ev => { if (ev.persisted) { reset(); setBuyMsg(''); } }, { once: true });
    }
  }

  // ---------- API key + gói của tôi ----------
  let me = null;
  const keyDlg = $('#keyDlg'), keyInput = $('#keyInput'), keyMsg = $('#keyMsg'), keySave = $('#keySave');
  function setKeyMsg(text, kind) { keyMsg.className = 'wl-msg' + (kind ? ' ' + kind : ''); keyMsg.textContent = text || ''; }
  function maskKey(k) { k = String(k); return k.length <= 12 ? k.slice(0, 4) + '…' : k.slice(0, 10) + '…' + k.slice(-4); }
  function renderAcct() {
    $('#keyBtnLabel').textContent = apiKey ? 'Đổi API key' : 'Nhập API key';
    const bar = $('#acctBar'); bar.hidden = !apiKey;
    if (!apiKey) return;
    const badge = $('#planBadge'), usage = $('#acctUsage'), portal = $('#portalBtn');
    if (!me) {
      badge.textContent = 'Key ' + maskKey(apiKey); badge.className = 'plan-badge';
      usage.textContent = 'Chưa kiểm tra được gói của key này.'; portal.hidden = true; return;
    }
    badge.textContent = 'Gói ' + (me.plan_name || PLAN_LABEL[me.plan] || me.plan); badge.className = 'plan-badge on';
    let u;
    if (me.limit == null) u = `Không giới hạn ký tự${me.used != null ? ' · tháng này đã dùng ' + fmtN(me.used) + ' ký tự' : ''}`;
    else u = `Tháng này đã dùng ${fmtN(me.used || 0)} / ${fmtN(me.limit)} ký tự` + (me.reset_at ? ` · làm mới ${fmtDate(me.reset_at)}` : '');
    if (me.email) u += ' · ' + me.email;
    usage.textContent = u;
    portal.hidden = me.plan === 'internal';
  }
  function setKey(k, info) {
    apiKey = k; me = info || null; store.set('apikey', k); renderAcct();
  }
  function onInvalidKey() {
    apiKey = ''; me = null; store.del('apikey'); renderAcct();
    showAlert(INVALID_KEY_MSG);
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
    if (!k) { keyInput.setAttribute('aria-invalid', 'true'); setKeyMsg('Hãy dán API key vào ô trên.', 'err'); keyInput.focus(); return; }
    keySave.classList.add('busy'); keySave.disabled = true; setKeyMsg('Đang kiểm tra key…');
    try {
      const info = await api('/api/me', { key: k });
      setKey(k, info); closeKeyDlg(); hideAlert();
      toast('Đã lưu key · gói ' + (info.plan_name || PLAN_LABEL[info.plan] || info.plan));
      loadQuota(); updateCount();
    } catch (err) {
      keyInput.setAttribute('aria-invalid', String(err.code === 'invalid_api_key'));
      setKeyMsg(err.code === 'invalid_api_key' ? err.message
        : err.code === 'network' ? 'Chưa kiểm tra được key vì không kết nối được máy chủ. Thử lại sau ít phút.'
        : 'Chưa kiểm tra được key: ' + err.message, 'err');
      keyInput.focus();
    } finally { keySave.classList.remove('busy'); keySave.disabled = false; }
  });
  $('#logoutKey').onclick = () => {
    apiKey = ''; me = null; store.del('apikey'); renderAcct();
    loadQuota(); updateCount();
    toast('Đã gỡ API key khỏi trình duyệt này. Bạn đang dùng lượt miễn phí.');
    $('#keyBtn').focus();
  };
  $('#portalBtn').onclick = async () => {
    const b = $('#portalBtn'); if (b.disabled) return;
    b.classList.add('busy'); b.disabled = true;
    let leaving = false;
    try {
      const r = await api('/api/billing/portal', { method: 'POST' });
      const url = safeRedirect(r && r.url);
      if (!url) throw new ApiError('Chưa mở được cổng khách hàng. Thử lại sau ít phút nhé.', 'bad_response', 200);
      leaving = true; location.href = url;
    } catch (e) { showAlert(e.message); }
    finally {
      const reset = () => { b.classList.remove('busy'); b.disabled = false; };
      if (!leaving) reset(); else addEventListener('pageshow', ev => { if (ev.persisted) reset(); }, { once: true });
    }
  };

  // ---------- danh sách chờ ----------
  const PLAN_NAMES = { 'ca-nhan': 'Cá nhân', 'khong-gioi-han': 'Không giới hạn', 'doanh-nghiep': 'Doanh nghiệp' };
  const wl = $('#waitlist'), wlEmail = $('#wlEmail'), wlPlan = $('#wlPlan'), wlMsg = $('#wlMsg'), wlBtn = $('#wlBtn');
  function markPlan() { $$('[data-plan]').forEach(b => b.closest('.plan').classList.toggle('picked', b.dataset.plan === wlPlan.value)); }
  $$('[data-plan]').forEach(b => b.addEventListener('click', () => {
    if (billing.enabled && b.dataset.buy) { buy(b.dataset.buy, b); return; }
    wlPlan.value = b.dataset.plan; markPlan();
    wl.scrollIntoView({ block: 'center', behavior: reduceMotion.matches ? 'auto' : 'smooth' });
    wlEmail.focus({ preventScroll: true });
  }));
  wlPlan.addEventListener('change', markPlan);
  wlEmail.addEventListener('input', () => wlEmail.removeAttribute('aria-invalid'));
  wl.addEventListener('submit', async e => {
    e.preventDefault();
    const email = wlEmail.value.trim();
    wlMsg.className = 'wl-msg';
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(email)) {
      wlEmail.setAttribute('aria-invalid', 'true'); wlMsg.classList.add('err');
      wlMsg.textContent = 'Email chưa đúng, ví dụ: ten@gmail.com'; wlEmail.focus(); return;
    }
    wlBtn.classList.add('busy'); wlBtn.disabled = true;
    try {
      const r = await api('/api/waitlist', { method: 'POST', body: { email, plan: wlPlan.value } });
      wlMsg.classList.add('ok');
      wlMsg.textContent = `Đã giữ chỗ! Bạn là người thứ ${fmtN(r.position)} trong danh sách chờ gói ${PLAN_NAMES[wlPlan.value] || wlPlan.value}. Chúng tôi sẽ báo qua ${email} khi gói mở.`;
      store.set('waitlist', { email, plan: wlPlan.value, position: r.position });
    } catch (err) {
      wlMsg.classList.add('err');
      wlMsg.textContent = err.code === 'network' ? 'Chưa gửi được vì không kết nối được máy chủ. Thử lại sau ít phút nhé.' : err.message;
    } finally { wlBtn.classList.remove('busy'); wlBtn.disabled = false; }
  });

  // ---------- API mẫu ----------
  $('#copyCurl').onclick = async () => {
    const t = $('#curl code').innerText.split('\n\n')[0];
    try { await navigator.clipboard.writeText(t); toast('Đã chép lệnh curl.'); } catch (e) { toast('Không chép được, hãy bôi đen và chép thủ công.'); }
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
  async function goOnline() {
    const wasOffline = document.body.classList.contains('offline');
    online = true;
    document.body.classList.remove('offline');
    $('#offlineBanner').hidden = true;
    stopSpeech();
    setBusy(false);
    if (!voices.length) {
      try { await loadVoices(); }
      catch (e) { $('#vgrid').innerHTML = `<p class="note warn">Chưa tải được danh sách giọng: ${esc(e.message)}</p>`; }
    }
    loadQuota();
    if (apiKey) loadMe();
    if (!billing.enabled) loadPlans();
    updateCount();
    if (wasOffline) toast('Đã kết nối lại máy chủ DatTTS.');
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
  $('#retryBtn').onclick = async () => {
    const b = $('#retryBtn'); b.disabled = true; b.textContent = 'Đang thử…';
    await checkHealth();
    b.disabled = false; b.textContent = 'Thử kết nối lại';
    if (!online) toast('Vẫn chưa kết nối được máy chủ.');
  };

  // ---------- khởi động ----------
  readAccent(); sizeCanvas(); drawOnce();
  syncRate(); renderDict(); renderHistory(); markPlan(); renderAcct();
  const wlSaved = store.get('waitlist', null);
  if (wlSaved && wlSaved.email) { wlEmail.value = wlSaved.email; }
  updateCount();
  if (location.protocol === 'file:' && !API) { goOffline(); billing.loaded = true; renderPlans(); calc(); }
  else checkHealth().then(() => { if (!online) loadPlans(); });
})();
