/* DatTTS — trang sau thanh toán: hỏi trạng thái phiên Polar, hướng dẫn nhận API key, ô dán key. */
(() => {
  'use strict';
  const API = String(window.DATTTS_API || '').replace(/\/+$/, '');
  const $ = s => document.querySelector(s);
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const PLAN_LABEL = { personal: 'Cá nhân', unlimited: 'Không giới hạn', internal: 'Nội bộ' };
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const POLL_MS = 2000, POLL_MAX_MS = 60000;
  // Polar chỉ chuyển về trang này SAU khi phiên được xác nhận → vẫn "open" sau vài lần hỏi nghĩa là chưa ai trả tiền
  // (mở lại từ dấu trang, nút Back, đường dẫn chia sẻ…).
  const OPEN_POLLS_MAX = 3;

  class ApiError extends Error { constructor(m, code, status) { super(m); this.code = code; this.status = status; } }
  async function api(path, opts = {}) {
    const headers = { Accept: 'application/json' };
    if (opts.key) headers['X-API-Key'] = opts.key;
    let res;
    try { res = await fetch(API + path, { method: opts.method || 'GET', headers }); }
    catch (e) { throw new ApiError('Không kết nối được máy chủ DatTTS. Kiểm tra mạng rồi thử lại.', 'network', 0); }
    let data = null;
    try { data = await res.json(); } catch (e) { /* không phải JSON */ }
    if (!res.ok) {
      const er = (data && data.error) || {};
      throw new ApiError(er.message || (res.status >= 500 ? 'Máy chủ đang gặp sự cố. Thử lại sau ít phút nhé.' : 'Yêu cầu chưa hợp lệ (mã ' + res.status + ').'), er.code || 'http_' + res.status, res.status);
    }
    if (data == null) throw new ApiError('Máy chủ trả về dữ liệu không đọc được.', 'bad_response', res.status);
    return data;
  }

  // ---------- trạng thái thanh toán ----------
  const card = $('#payCard'), live = $('#payLive');
  let lastState = '';
  function render(state, d = {}) {
    const plan = d.plan && PLAN_LABEL[d.plan] ? 'gói ' + PLAN_LABEL[d.plan] : 'gói của bạn';
    const email = d.email ? '<b>' + esc(d.email) + '</b>' : 'email bạn dùng khi thanh toán';
    const home = '<a class="btn btn-ghost" href="./#bang-gia">Xem bảng giá</a>';
    const studio = '<a class="btn btn-primary" href="./#studio">Mở phòng thu</a>';
    const support = '<a href="mailto:support@dattts.com?subject=' + encodeURIComponent('Thanh toán ' + (checkoutId || '')) + '">support@dattts.com</a>';
    const V = {
      missing: ['bad', 'Không có mã thanh toán', 'Không tìm thấy phiên thanh toán',
        `<p class="lead">Đường dẫn này thiếu mã <code>checkout_id</code> nên chưa kiểm tra được thanh toán. Nếu bạn vừa trả tiền xong, hãy kiểm tra email: Polar gửi biên nhận và API key ngay khi thanh toán thành công. Bạn cũng có thể dán key vào ô bên dưới.</p>`, home],
      checking: ['wait', 'Đang xác nhận', 'Đang xác nhận thanh toán…',
        `<p class="lead">Polar đang xác nhận giao dịch với ngân hàng của bạn. Thường chỉ mất vài giây — đừng đóng trang này.</p>`, ''],
      confirmed: ['wait', 'Đang xác nhận', 'Đã nhận thông tin thanh toán…',
        `<p class="lead">Polar đã nhận thông tin thẻ và đang chờ ngân hàng duyệt. Trang sẽ tự cập nhật khi xong.</p>`, ''],
      succeeded: ['ok', 'Thành công', 'Thanh toán thành công!',
        `<p class="lead">Cảm ơn bạn đã ủng hộ DatTTS. ${esc(plan.charAt(0).toUpperCase() + plan.slice(1))} đã được kích hoạt.</p>
         <ol class="paysteps">
           <li>Polar đã gửi API key (bắt đầu bằng <code>DATTTS</code>) tới ${email}. Không thấy thư thì xem thêm mục Spam hoặc Quảng cáo.</li>
           <li>Dán key vào ô bên dưới, hoặc bấm “Nhập API key” trong phòng thu.</li>
           <li>Hoá đơn và việc quản lý, huỷ gói nằm trong cổng khách hàng Polar (nút “Quản lý gói” trong phòng thu).</li>
         </ol>`, studio],
      unpaid: ['bad', 'Chưa thanh toán', 'Phiên này chưa được thanh toán',
        `<p class="lead">Phiên thanh toán này vẫn đang mở và chưa có khoản nào bị trừ, nên Polar chưa gửi API key. Nếu bạn đã đóng trang thanh toán giữa chừng, hãy chọn gói lại ở bảng giá. Nếu bạn vừa trả tiền xong, bấm “Kiểm tra lại” sau ít giây.</p>`,
        '<a class="btn btn-primary" href="./#bang-gia">Chọn gói</a> <button class="btn btn-ghost" type="button" data-retry>Kiểm tra lại</button>'],
      expired: ['bad', 'Hết hạn', 'Phiên thanh toán đã hết hạn',
        `<p class="lead">Phiên này đã quá thời gian chờ và chưa có khoản nào bị trừ. Bạn có thể tạo phiên mới từ bảng giá.</p>`, '<a class="btn btn-primary" href="./#bang-gia">Mua lại</a>'],
      failed: ['bad', 'Thất bại', 'Thanh toán chưa thành công',
        `<p class="lead">Ngân hàng hoặc Polar đã từ chối giao dịch. Hãy thử lại bằng thẻ khác, hoặc thẻ Visa/Mastercard quốc tế (thẻ ATM nội địa chưa được hỗ trợ). Nếu thấy tài khoản đã bị trừ tiền, hãy gửi email tới ${support} kèm mã phiên bên dưới.</p>`, '<a class="btn btn-primary" href="./#bang-gia">Thử lại</a>'],
      timeout: ['wait', 'Chưa xong', 'Thanh toán vẫn đang được xác nhận',
        `<p class="lead">Việc xác nhận lâu hơn bình thường. Bạn không cần thanh toán lại: Polar sẽ gửi email kèm API key ngay khi giao dịch hoàn tất. Bấm “Kiểm tra lại” sau ít phút, hoặc liên hệ ${support} nếu quá 30 phút chưa có email.</p>`, '<button class="btn btn-primary" type="button" data-retry>Kiểm tra lại</button>'],
      notfound: ['bad', 'Không tìm thấy', 'Không tìm thấy phiên thanh toán',
        `<p class="lead">Mã phiên trong đường dẫn không khớp phiên nào. Nếu bạn đã thanh toán, API key vẫn nằm trong email của Polar; cần hỗ trợ thì viết cho ${support}.</p>`, home],
      disabled: ['bad', 'Chưa mở bán', 'Gói trả phí chưa mở bán',
        `<p class="lead">${esc(d.message || 'Gói trả phí sắp mở bán.')} Nếu bạn đã thanh toán ở đâu đó mà chưa nhận được API key, hãy viết cho ${support}.</p>`, home],
      error: ['bad', 'Lỗi kết nối', 'Chưa kiểm tra được thanh toán',
        `<p class="lead">${esc(d.message || 'Không kết nối được máy chủ DatTTS.')} Nếu bạn đã thanh toán, đừng lo: Polar vẫn gửi API key qua email.</p>`, '<button class="btn btn-primary" type="button" data-retry>Thử lại</button>']
    };
    const [tone, label, title, body, actions] = V[state];
    card.innerHTML = `<div class="pstate ${tone}"><i aria-hidden="true"></i><span>${esc(label)}</span></div>
      <h1 tabindex="-1">${esc(title)}</h1>${body}
      ${actions ? '<div class="actions">' + actions + '</div>' : ''}
      ${checkoutId && state !== 'missing' ? '<p class="note">Mã phiên: <code>' + esc(checkoutId) + '</code></p>' : ''}`;
    card.dataset.state = state;
    if (state !== lastState) {
      live.textContent = title;
      if (lastState && tone !== 'wait') $('h1', card)?.focus?.();
      lastState = state;
    }
    const retry = card.querySelector('[data-retry]');
    if (retry) retry.onclick = () => poll();
  }

  const checkoutId = (new URLSearchParams(location.search).get('checkout_id') || '').trim();
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
  function setMsg(html, kind) { msg.className = 'wl-msg' + (kind ? ' ' + kind : ''); msg.innerHTML = html || ''; }
  input.addEventListener('input', () => { input.removeAttribute('aria-invalid'); setMsg(''); });
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const k = input.value.replace(/\s+/g, '');
    if (!k) { input.setAttribute('aria-invalid', 'true'); setMsg('Hãy dán API key (bắt đầu bằng DATTTS) vào ô trên.', 'err'); input.focus(); return; }
    btn.classList.add('busy'); btn.disabled = true; setMsg('Đang kiểm tra key…');
    try {
      const me = await api('/api/me', { key: k });
      input.value = k;
      try { localStorage.setItem('dattts.apikey', JSON.stringify(k)); }
      catch (er) { setMsg('Key hợp lệ nhưng trình duyệt không cho lưu (chế độ ẩn danh?). Hãy mở phòng thu và nhập key ở đó.', 'err'); return; }
      const name = me.plan_name || PLAN_LABEL[me.plan] || me.plan;
      const lim = me.limit == null ? 'không giới hạn ký tự' : `${Number(me.limit).toLocaleString('vi-VN')} ký tự mỗi tháng`;
      setMsg(`Đã lưu key trên trình duyệt này · gói ${esc(name)}, ${esc(lim)}. <a href="./#studio">Mở phòng thu →</a>`, 'ok');
    } catch (er) {
      const bad = er.status === 401;
      input.setAttribute('aria-invalid', String(bad));
      setMsg(esc(bad ? 'Key không đúng hoặc đã hết hạn. Kiểm tra lại key trong email Polar gửi bạn.' : er.code === 'network' ? 'Chưa kiểm tra được key vì không kết nối được máy chủ. Thử lại sau ít phút.' : 'Chưa kiểm tra được key: ' + er.message), 'err');
      input.focus();
    } finally { btn.classList.remove('busy'); btn.disabled = false; }
  });
})();
