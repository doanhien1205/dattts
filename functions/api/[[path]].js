/**
 * DatTTS — Cloudflare Pages Function: proxy /api/* sang backend Modal (SPEC Phần 4).
 *
 * Biến môi trường Pages (đặt dạng Secret / "Encrypt"):
 *   MODAL_ORIGIN         https://<workspace>--dattts-web.modal.run   (không có dấu / cuối cũng được)
 *   PROXY_SHARED_SECRET  chuỗi ngẫu nhiên ≥ 32 ký tự, GIỐNG HỆT biến cùng tên trong Modal secret `dattts`
 *
 * - Chuyển nguyên method, path, query và body dạng stream (không đọc/biến đổi: chữ ký webhook Polar tính trên
 *   raw body). Mọi header trừ hop-by-hop, `host`, `cf-*` (do Cloudflare đặt) và mọi `x-dattts-*` khách tự gửi;
 *   rồi thêm `X-DatTTS-Proxy-Secret` + `X-DatTTS-Client-IP` (= CF-Connecting-IP).
 * - Trả nguyên status/header/body dạng stream (Range/206, Content-Disposition, Vary, CORS, Cache-Control).
 *   Không tự cache: Cloudflare chỉ tôn trọng Cache-Control của gốc (/api/files/* là immutable).
 * - Không đặt timeout riêng: /api/tts có thể chờ vài chục giây khi container Modal khởi động lạnh.
 * - Modal cắt mọi request web ở 150 giây và trả 303 về CHÍNH URL đó (kèm tham số mã lời gọi) để client hỏi lại
 *   kết quả. Proxy tự theo các 303 đó (GET, tối đa MODAL_MAX_FOLLOW lần) thay vì đưa cho trình duyệt — trình
 *   duyệt theo sang modal.run sẽ lỗi CORS và đi vòng qua proxy. Các 3xx khác vẫn trả nguyên.
 * - Lỗi kết nối tới Modal / thiếu cấu hình → 502 JSON {"error":{"code":"upstream_unavailable",…}} (EN/VI).
 */

// RFC 9110 §7.6.1 + các header proxy cũ; `host` do fetch tự đặt theo MODAL_ORIGIN.
const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-connection",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "host",
]);

const MESSAGES = {
  en: "The DatTTS server is temporarily unavailable. Please try again in a minute.",
  vi: "Máy chủ DatTTS tạm thời không phản hồi. Vui lòng thử lại sau ít phút.",
};

function pickLang(acceptLanguage) {
  // Giống backend (messages.pick_lang): ngôn ngữ ưu tiên đầu tiên là vi* → tiếng Việt, còn lại tiếng Anh.
  const first = String(acceptLanguage || "").split(",")[0].trim().toLowerCase();
  return first.startsWith("vi") ? "vi" : "en";
}

function upstreamUnavailable(request) {
  const lang = pickLang(request.headers.get("accept-language"));
  const body = JSON.stringify({ error: { code: "upstream_unavailable", message: MESSAGES[lang] } });
  return new Response(body, {
    status: 502,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
    },
  });
}

/** Tên header liệt kê trong `Connection: a, b` cũng là hop-by-hop. */
function connectionTokens(headers) {
  return new Set(
    String(headers.get("connection") || "")
      .split(",")
      .map((t) => t.trim().toLowerCase())
      .filter(Boolean),
  );
}

export function buildUpstreamHeaders(request, secret) {
  const listed = connectionTokens(request.headers);
  const out = new Headers();
  for (const [name, value] of request.headers) {
    const k = name.toLowerCase();
    if (HOP_BY_HOP.has(k) || listed.has(k) || k.startsWith("x-dattts-") || k.startsWith("cf-")) continue;
    out.append(name, value);
  }
  out.set("x-dattts-proxy-secret", secret);
  const ip = request.headers.get("cf-connecting-ip");
  if (ip) out.set("x-dattts-client-ip", ip.trim());
  return out;
}

function buildResponseHeaders(upstream) {
  const listed = connectionTokens(upstream.headers);
  const out = new Headers();
  for (const [name, value] of upstream.headers) {
    const k = name.toLowerCase();
    if (HOP_BY_HOP.has(k) || listed.has(k)) continue;
    out.append(name, value);
  }
  // getSetCookie() giữ nhiều Set-Cookie riêng rẽ (Headers iterator gộp chúng bằng dấu phẩy)
  if (typeof upstream.headers.getSetCookie === "function") {
    const cookies = upstream.headers.getSetCookie();
    if (cookies.length > 1) {
      out.delete("set-cookie");
      for (const c of cookies) out.append("set-cookie", c);
    }
  }
  return out;
}

// 8 × 150 s = 20 phút > timeout 10 phút của hàm web trên Modal.
export const MODAL_MAX_FOLLOW = 8;

/** URL tiếp theo nếu `upstream` là 303 "chờ kết quả" của Modal (cùng origin + cùng path với `url`), ngược lại null. */
export function modalContinuation(upstream, url) {
  if (upstream.status !== 303) return null;
  const loc = upstream.headers.get("location");
  if (!loc) return null;
  let next;
  try {
    next = new URL(loc, url);
  } catch {
    return null;
  }
  const cur = new URL(url);
  return next.origin === cur.origin && next.pathname === cur.pathname ? next.href : null;
}

export function targetUrl(requestUrl, origin) {
  const u = new URL(requestUrl);
  const base = new URL(String(origin).trim());
  if (base.protocol !== "https:" && base.protocol !== "http:") throw new Error("MODAL_ORIGIN phải là http(s)");
  return base.origin + u.pathname + u.search;
}

export async function onRequest(context) {
  const { request, env } = context;
  const origin = env && env.MODAL_ORIGIN;
  const secret = env && env.PROXY_SHARED_SECRET;
  if (!origin || !secret) {
    console.error("DatTTS proxy: thiếu biến môi trường MODAL_ORIGIN hoặc PROXY_SHARED_SECRET");
    return upstreamUnavailable(request);
  }

  let url;
  try {
    url = targetUrl(request.url, origin);
  } catch (e) {
    console.error("DatTTS proxy: MODAL_ORIGIN không hợp lệ:", e && e.message);
    return upstreamUnavailable(request);
  }

  const method = request.method.toUpperCase();
  const hasBody = method !== "GET" && method !== "HEAD" && request.body !== null;
  const init = {
    method,
    headers: buildUpstreamHeaders(request, secret),
    redirect: "manual", // trả nguyên 3xx cho trình duyệt
  };
  if (hasBody) {
    init.body = request.body; // stream thô, không đọc/biến đổi
    init.duplex = "half"; // bắt buộc với body dạng stream (Node/undici; Workers bỏ qua)
  }

  let upstream;
  try {
    upstream = await fetch(url, init);
    for (let i = 0; i < MODAL_MAX_FOLLOW; i++) {
      const next = modalContinuation(upstream, url);
      if (!next) break;
      if (upstream.body) await upstream.body.cancel();
      url = next;
      const headers = new Headers(init.headers);
      for (const h of ["content-type", "content-length", "content-encoding"]) headers.delete(h);
      upstream = await fetch(url, { method: method === "HEAD" ? "HEAD" : "GET", headers, redirect: "manual" });
    }
  } catch (e) {
    console.error("DatTTS proxy: không kết nối được Modal:", e && e.message);
    return upstreamUnavailable(request);
  }

  const nullBody = method === "HEAD" || [101, 204, 205, 304].includes(upstream.status);
  return new Response(nullBody ? null : upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: buildResponseHeaders(upstream),
  });
}
