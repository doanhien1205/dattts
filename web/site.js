/* DatTTS — script chung cho trang phụ (thanh toán, pháp lý): nút đổi giao diện sáng/tối. */
(() => {
  'use strict';
  const btn = document.getElementById('themeBtn');
  if (!btn) return;
  const darkMQ = matchMedia('(prefers-color-scheme: dark)');
  btn.addEventListener('click', () => {
    const root = document.documentElement;
    const cur = root.dataset.theme || (darkMQ.matches ? 'dark' : 'light');
    const next = cur === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    try { localStorage.setItem('dattts.theme', next); } catch (e) { /* bỏ qua */ }
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = next === 'dark' ? '#0C1119' : '#D63A6E';
  });
})();
