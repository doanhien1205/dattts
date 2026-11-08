/* DatTTS — trang sau thanh toán (song ngữ): hỏi trạng thái phiên Polar, hướng dẫn nhận API key, ô dán key. */
(() => {
  'use strict';
  const I = window.I18N, t = I.t;
  const API = String(window.DATTTS_API || '').replace(/\/+$/, '');
  const $ = s => document.querySelector(s);
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const POLL_MS = 2000, POLL_MAX_MS = 60000;
  // Polar chỉ chuyển về trang này SAU khi phiên được xác nhận → vẫn "open" sau vài lần hỏi nghĩa là chưa ai trả tiền
  // (mở lại từ dấu trang, nút Back, đường dẫn chia sẻ…).
  const OPEN_POLLS_MAX = 3;
  const planName = (plan, fallback) => (plan && I.has('plan.name.' + plan) ? t('plan.name.' + plan) : (fallback || plan || ''));

  class ApiError extends Error { constructor(m, code, status) { super(m); this.code = code; this.status = status; } }
  async function api(path, opts = {}) {
    const headers = { Accept: 'application/json', 'Accept-Language': I.lang };
    if (opts.key) headers['X-API-Key'] = opts.key;
    let res;
    try { res = await fetch(API + path, { method: opts.method || 'GET', headers }); }
    catch (e) { throw new ApiError(t('err.network'), 'network', 0); }
    let data = null;
    try { data = await res.json(); } catch (e) { /* không phải JSON */ }
    if (!res.ok) {
      const er = (data && data.error) || {};
      throw new ApiError(er.message || (res.status >= 500 ? t('err.server') : t('err.badRequest', { code: res.status })), er.code || 'http_' + res.status, res.status);
    }
    if (data == null) throw new ApiError(t('err.badResponse'), 'bad_response', res.status);
    return data;
  }

  // ---------- trạng thái thanh toán ----------
  const card = $('#payCard'), live = $('#payLive');
  const checkoutId = (new URLSearchParams(location.search).get('checkout_id') || '').trim();
  let lastState = '', lastData = {};
  const TONE = { missing: 'bad', checking: 'wait', confirmed: 'wait', succeeded: 'ok', unpaid: 'bad', expired: 'bad', failed: 'bad', timeout: 'wait', notfound: 'bad', disabled: 'bad', error: 'bad' };
  function render(state, d = {}) {
    lastData = d;
    const plan = planName(d.plan) || t('cs.plan.default');
    const email = d.email ? '<b>' + esc(d.email) + '</b>' : esc(t('cs.email.default'));
    const support = '<a href="mailto:support@dattts.com?subject=' + encodeURIComponent(t('cs.supportSubject', { id: checkoutId || '' })) + '">support@dattts.com</a>';
    const btn = (href, key, primary) => `<a class="btn ${primary ? 'btn-primary' : 'btn-ghost'}" href="${href}">${esc(t(key))}</a>`;
    const retry = key => `<button class="btn btn-primary" type="button" data-retry>${esc(t(key))}</button>`;
    const P = s => `<p class="lead">${s}</p>`;
    const vars = { support, plan: esc(plan), email, message: '' };
    let body, actions = '';
    switch (state) {
      case 'succeeded':
        body = P(t('cs.succeeded.body', vars)) + `<ol class="paysteps"><li>${t('cs.succeeded.s1', vars)}</li><li>${esc(t('cs.succeeded.s2'))}</li><li>${esc(t('cs.succeeded.s3'))}</li></ol>`;
        actions = btn('./#studio', 'cs.btn.studio', true); break;
      case 'unpaid':
        body = P(esc(t('cs.unpaid.body')));
        actions = btn('./#pricing', 'cs.btn.choose', true) + ' <button class="btn btn-ghost" type="button" data-retry>' + esc(t('cs.btn.recheck')) + '</button>'; break;
      case 'expired': body = P(esc(t('cs.expired.body'))); actions = btn('./#pricing', 'cs.btn.buyAgain', true); break;
      case 'failed': body = P(t('cs.failed.body', vars)); actions = btn('./#pricing', 'cs.btn.tryAgain', true); break;
      case 'timeout': body = P(t('cs.timeout.body', vars)); actions = retry('cs.btn.recheck'); break;
      case 'notfound': body = P(t('cs.notfound.body', vars)); actions = btn('./#pricing', 'cs.btn.pricing'); break;
      case 'disabled': body = P(t('cs.disabled.body', Object.assign(vars, { message: esc(d.message || t('cs.disabled.default')) }))); actions = btn('./#pricing', 'cs.btn.pricing'); break;
      case 'error': body = P(t('cs.error.body', Object.assign(vars, { message: esc(d.message || t('cs.error.default')) }))); actions = retry('cs.btn.retry'); break;
      case 'missing': body = P(t('cs.missing.body')); actions = btn('./#pricing', 'cs.btn.pricing'); break;
      default: body = P(esc(t('cs.' + state + '.body')));
    }
    const title = t('cs.' + state + '.title'), tone = TONE[state];
    card.innerHTML = `<div class="pstate ${tone}"><i aria-hidden="true"></i><span>${esc(t('cs.' + state + '.label'))}</span></div>
      <h1 tabindex="-1">${esc(title)}</h1>${body}
      ${actions ? '<div class="actions">' + actions + '</div>' : ''}
      ${checkoutId && state !== 'missing' ? '<p class="note">' + esc(t('cs.session')) + ' <code>' + esc(checkoutId) + '</code></p>' : ''}`;
    card.dataset.state = state;
    if (state !== lastState) {
      live.textContent = title;
      if (lastState && tone !== 'wait') $('h1', card)?.focus?.();
      lastState = state;
    }
    const r = card.querySelector('[data-retry]');
    if (r) r.onclick = () => poll();
  }

  let polling = false;
  async function poll() {
    if (polling) return;
    polling = true;
    const t0 = Date.now();
    let misses = 0, opens = 0;
    render('checking');
    try {
      for (;;) {
        let d = null;
        try { d = await api('/api/billing/checkout/' + encodeURIComponent(checkoutId)); misses = 0; }
        catch (e) {
          if (e.status === 404) { render('notfound'); return; }
          if (e.status === 400 || e.status === 422) { render('missing'); return; }
          if (e.code === 'billing_disabled') { render('disabled', { message: e.message }); return; }
          if (++misses >= 4) { render('error', { message: e.message }); return; }
        }
        if (d) {
          const st = String(d.status || '');
          if (st === 'succeeded') { render('succeeded', d); $('#keyInput')?.focus?.({ preventScroll: true }); return; }
          if (st === 'expired' || st === 'failed') { render(st, d); return; }
          if (st === 'open' && ++opens >= OPEN_POLLS_MAX) { render('unpaid', d); return; }
          if (st !== 'open') opens = 0;
          render(st === 'confirmed' ? 'confirmed' : 'checking', d);
        }
        if (Date.now() - t0 + POLL_MS > POLL_MAX_MS) { render('timeout'); return; }
        await sleep(POLL_MS);
      }
    } finally { polling = false; }
  }
  if (!checkoutId || !UUID.test(checkoutId)) render('missing'); else poll();

  // ---------- ô dán API key ----------
  const form = $('#keyForm'), input = $('#keyInput'), msg = $('#keyMsg'), btn = $('#keySave');
  let msgState = null; // để dịch lại khi đổi ngôn ngữ
  function setMsg(html, kind, st) { msgState = st || null; msg.className = 'wl-msg' + (kind ? ' ' + kind : ''); msg.innerHTML = html || ''; }
  function okMsg(me) {
    const lim = me.limit == null ? t('cs.limit.unlimited') : t('plan.quota.' + (/^(day|week|month)$/.test(me.period) ? me.period : 'month'), { n: I.fmtNum(Number(me.limit)) });
    return t('cs.key.ok', { plan: esc(planName(me.plan, me.plan_name)), limit: esc(lim) });
  }
  input.addEventListener('input', () => { input.removeAttribute('aria-invalid'); setMsg(''); });
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const k = input.value.replace(/\s+/g, '');
    if (!k) { input.setAttribute('aria-invalid', 'true'); setMsg(esc(t('cs.key.empty')), 'err', { key: 'cs.key.empty' }); input.focus(); return; }
    btn.classList.add('busy'); btn.disabled = true; setMsg(esc(t('key.checking')), '', { key: 'key.checking' });
    try {
      const me = await api('/api/me', { key: k });
      input.value = k;
      try { localStorage.setItem('dattts.apikey', JSON.stringify(k)); }
      catch (er) { setMsg(esc(t('cs.key.storage')), 'err', { key: 'cs.key.storage' }); return; }
      setMsg(okMsg(me), 'ok', { me });
    } catch (er) {
      const bad = er.status === 401;
      input.setAttribute('aria-invalid', String(bad));
      const key = bad ? 'err.keyBad' : er.code === 'network' ? 'key.netErr' : null;
      // Lỗi khác: có thông điệp máy chủ (theo ngôn ngữ lúc gửi) → đánh dấu để gỡ khi đổi ngôn ngữ.
      setMsg(esc(key ? t(key) : t('key.checkErr', { m: er.message })), 'err', key ? { key } : { server: true });
      input.focus();
    } finally { btn.classList.remove('busy'); btn.disabled = false; }
  });

  // ---------- đổi ngôn ngữ tại chỗ ----------
  document.addEventListener('dattts:lang', () => {
    // 'disabled'/'error' chứa thông điệp của máy chủ bằng ngôn ngữ cũ → hỏi lại máy chủ (Accept-Language mới)
    // thay vì ghép chuỗi cũ vào khung câu mới (lẫn hai thứ tiếng).
    if ((lastState === 'disabled' || lastState === 'error') && checkoutId && UUID.test(checkoutId) && !polling) poll();
    else if (lastState) render(lastState, lastData);
    if (msgState) {
      if (msgState.me) msg.innerHTML = okMsg(msgState.me);
      else if (msgState.key) msg.textContent = t(msgState.key);
      else if (msgState.server) setMsg('');
    }
  });
})();
