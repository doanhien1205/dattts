import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from dattts.api import create_app
from dattts.engine import FakeEngine
from dattts.settings import Settings
from dattts.store import LocalRenderer, LocalStore, job_key

LONG_TEXT = " ".join(f"Đây là câu thứ {i} trong một văn bản khá dài." for i in range(80))


@pytest.fixture
def make_client(tmp_path):
    made = []

    def _make(engine=None, **overrides):
        overrides.setdefault("web_dir", None)
        store = LocalStore(str(tmp_path / f"data{len(made)}"))
        engine = engine or FakeEngine()
        renderer = LocalRenderer(engine, store)
        settings = Settings.from_env({}, **overrides)
        c = TestClient(create_app(renderer, store, settings))
        c.engine, c.store, c.renderer, c.settings = engine, store, renderer, settings
        made.append(c)
        return c

    yield _make
    for c in made:
        c.renderer.close()


@pytest.fixture
def client(make_client):
    return make_client()


def err(resp):
    body = resp.json()
    assert set(body) == {"error"}, body
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["message"]
    return body["error"]["code"]


def wait_job(client, job_id, timeout=20.0):
    t0 = time.time()
    seen = []
    while time.time() - t0 < timeout:
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200
        s = r.json()
        seen.append(s["status"])
        if s["status"] in ("done", "error"):
            return s, seen
        time.sleep(0.05)
    raise AssertionError(f"job chưa xong: {seen[-5:]}")


# ---------- health / voices ----------


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True and j["engine"] == "fake" and j["voices"] == 4


def test_voices_sorted_with_sample_url(client):
    j = client.get("/api/voices").json()
    assert j["default"] == "truc-ly"
    ids = [v["id"] for v in j["voices"]]
    assert ids == ["truc-ly", "thien-minh", "thuy-dung", "quang-son"]  # featured trước
    v = j["voices"][1]
    assert set(v) >= {"id", "name", "gender", "region", "style", "style_label", "featured", "description",
                      "sample_url"}
    assert v["style_label"] == "Kể chuyện" and v["sample_url"] == "/api/voices/thien-minh/sample.mp3"


def test_voice_sample_cached_and_free(client):
    r = client.get("/api/voices/thien-minh/sample.mp3")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    assert client.store.blob_exists("sample-thien-minh.mp3")
    n = len(client.engine.calls)
    r2 = client.get("/api/voices/thien-minh/sample.mp3")
    assert r2.content == r.content and len(client.engine.calls) == n
    assert client.get("/api/quota").json()["used"] == 0
    # tên có dấu cũng nhận
    assert client.get("/api/voices/Thiện Minh/sample.mp3").status_code == 200


def test_voice_sample_unknown(client):
    r = client.get("/api/voices/khong-co/sample.mp3")
    assert r.status_code == 404 and err(r) == "unknown_voice"


# ---------- /api/tts ----------


def test_tts_success_and_files(client):
    r = client.post("/api/tts", json={"text": "Xin chào, mình là DatTTS. Chúc bạn một ngày vui!",
                                      "voice": "Thiện Minh", "speed": 1.2, "format": "mp3"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["id"]) == 20 and j["cached"] is False and j["format"] == "mp3"
    assert j["audio_url"] == f"/api/files/{j['id']}.mp3"
    assert j["srt_url"].endswith(".srt") and j["vtt_url"].endswith(".vtt")
    assert [s["voice"] for s in j["segments"]] == ["thien-minh", "thien-minh"]
    assert j["segments"][0]["text"] == "Xin chào, mình là DatTTS."
    assert j["duration"] > 0 and j["chars"] == 48
    q = j["quota"]
    assert q["used"] == 48 and q["limit"] == 20000 and q["remaining"] == 20000 - 48
    a = client.get(j["audio_url"])
    assert a.status_code == 200 and a.headers["content-type"] == "audio/mpeg"
    assert a.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert a.headers["accept-ranges"] == "bytes"
    assert "content-disposition" not in a.headers
    s = client.get(j["srt_url"])
    assert s.headers["content-type"].startswith("application/x-subrip")
    assert "Xin chào, mình là DatTTS." in s.text
    v = client.get(j["vtt_url"])
    assert v.headers["content-type"].startswith("text/vtt") and v.text.startswith("WEBVTT")


@pytest.mark.parametrize("fmt,ctype,magic", [("wav", "audio/wav", b"RIFF"), ("ogg", "audio/ogg", b"OggS")])
def test_tts_other_formats(client, fmt, ctype, magic):
    j = client.post("/api/tts", json={"text": "Định dạng khác.", "format": fmt.upper()}).json()
    assert j["format"] == fmt
    a = client.get(j["audio_url"])
    assert a.headers["content-type"] == ctype and a.content[:4] == magic


def test_tts_multi_voice_script(client):
    j = client.post("/api/tts", json={"text": "[Trúc Ly] Chào anh. [Thiện Minh] Chào em."}).json()
    assert [s["voice"] for s in j["segments"]] == ["truc-ly", "thien-minh"]


@pytest.mark.parametrize("body,code", [
    ({"text": ""}, "empty_text"),
    ({"text": "   \n\t "}, "empty_text"),
    ({}, "empty_text"),
    ({"text": 123}, "bad_text"),
    ({"text": "Chào", "speed": 2.0}, "bad_speed"),
    ({"text": "Chào", "speed": 0.5}, "bad_speed"),
    ({"text": "Chào", "speed": "nhanh"}, "bad_speed"),
    ({"text": "Chào", "format": "flac"}, "bad_format"),
    ({"text": "Chào", "format": 5}, "bad_format"),
    ({"text": "Chào", "dictionary": [{"from": "a", "to": "b"}] * 201}, "bad_dictionary"),
    ({"text": "Chào", "dictionary": [{"from": "a" * 101, "to": "b"}]}, "bad_dictionary"),
    ({"text": "Chào", "dictionary": "abc"}, "bad_dictionary"),
    ({"text": "Chào", "dictionary": [{"from": 1, "to": []}]}, "bad_dictionary"),
])
def test_tts_validation_errors(client, body, code):
    r = client.post("/api/tts", json=body)
    assert r.status_code == 400, r.text
    assert err(r) == code
    assert client.engine.calls == []
    assert client.get("/api/quota").json()["used"] == 0


def test_tts_unknown_voice_lists_valid_names(client):
    for body in ({"text": "Chào", "voice": "Không Có"}, {"text": "[Ai Đó] Chào."}):
        r = client.post("/api/tts", json=body)
        assert r.status_code == 400 and err(r) == "unknown_voice"
        assert "Trúc Ly" in r.json()["error"]["message"]
    assert client.get("/api/quota").json()["used"] == 0


def test_invalid_json_envelope(client):
    r = client.post("/api/tts", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and err(r) == "bad_json"


def test_tts_too_long(client):
    r = client.post("/api/tts", json={"text": "a" * 3001})
    assert r.status_code == 413 and err(r) == "too_long"
    assert "/api/jobs" in r.json()["error"]["message"]
    assert client.post("/api/jobs", json={"text": "a " * 50001}).status_code == 413


def test_dictionary_applies_and_changes_cache_key(client):
    a = client.post("/api/tts", json={"text": "Gặp AI nhé."}).json()
    b = client.post("/api/tts", json={"text": "Gặp AI nhé.", "dictionary": [{"from": "AI", "to": "ây ai"}]}).json()
    assert a["id"] != b["id"]
    assert ("Gặp ây ai nhé.", "truc-ly") in client.engine.calls


# ---------- quota ----------


def test_quota_exceeded_then_api_key_bypass(make_client):
    c = make_client(free_daily_chars=40, api_keys=frozenset({"key-123"}))
    assert c.post("/api/tts", json={"text": "Một hai ba bốn năm sáu bảy."}).status_code == 200  # 27 ký tự
    r = c.post("/api/tts", json={"text": "Tám chín mười mười một mười hai."})
    assert r.status_code == 429 and err(r) == "quota_exceeded"
    assert "13" in r.json()["error"]["message"]  # còn 13 ký tự
    q = c.get("/api/quota").json()
    assert q["used"] == 27 and q["remaining"] == 13  # lần bị từ chối không bị trừ
    r = c.post("/api/tts", json={"text": "Tám chín mười mười một mười hai."}, headers={"X-API-Key": "key-123"})
    assert r.status_code == 200
    assert r.json()["quota"]["limit"] is None
    assert c.get("/api/quota", headers={"X-API-Key": "key-123"}).json()["remaining"] is None
    r = c.post("/api/tts", json={"text": "Khác."}, headers={"X-API-Key": "sai"})
    assert r.status_code == 401 and err(r) == "invalid_api_key"


def test_cache_hit_not_counted(client):
    body = {"text": "Câu này sẽ được lưu đệm.", "voice": "truc-ly"}
    a = client.post("/api/tts", json=body).json()
    calls = len(client.engine.calls)
    b = client.post("/api/tts", json={**body, "voice": "Trúc Ly"}).json()
    assert b["cached"] is True and b["id"] == a["id"]
    assert len(client.engine.calls) == calls
    assert b["quota"]["used"] == a["quota"]["used"] == a["chars"]


def test_quota_endpoint_reset_at_vn_midnight(client):
    q = client.get("/api/quota").json()
    assert q["used"] == 0 and q["limit"] == 20000 and q["remaining"] == 20000
    t = datetime.fromisoformat(q["reset_at"])
    assert t.utcoffset().total_seconds() == 7 * 3600
    assert (t.hour, t.minute, t.second) == (0, 0, 0)
    assert 0 < (t - datetime.now(t.tzinfo)).total_seconds() <= 86400


def test_quota_key_uses_vn_day(client):
    client.post("/api/tts", json={"text": "Chào."})
    keys = [k for k in client.store._kv if k.startswith("quota:")]
    day = datetime.now(datetime.fromisoformat(client.get("/api/quota").json()["reset_at"]).tzinfo)
    assert keys == [f"quota:{day:%Y-%m-%d}:ip:testclient"]


def test_forwarded_for_only_when_trusted(make_client):
    c = make_client(free_daily_chars=10)
    c.post("/api/tts", json={"text": "Mười ký tự"}, headers={"X-Forwarded-For": "1.1.1.1"})
    # không tin proxy → vẫn tính theo IP thật (testclient)
    assert c.get("/api/quota", headers={"X-Forwarded-For": "2.2.2.2"}).json()["used"] == 10
    t = make_client(free_daily_chars=10, trust_proxy=True)
    # Proxy thêm IP khách thật vào CUỐI; phần đầu do khách tự gửi
    t.post("/api/tts", json={"text": "Mười ký tự"}, headers={"X-Forwarded-For": "1.1.1.1, 10.0.0.1"})
    assert t.get("/api/quota", headers={"X-Forwarded-For": "10.0.0.1"}).json()["used"] == 10
    assert t.get("/api/quota", headers={"X-Forwarded-For": "2.2.2.2"}).json()["used"] == 0


def test_spoofed_forwarded_for_cannot_reset_quota(make_client):
    """Regression: khách đổi phần tử ĐẦU của X-Forwarded-For mỗi lần → không được thêm quota."""
    t = make_client(free_daily_chars=10, trust_proxy=True)
    codes = [t.post("/api/tts", json={"text": f"Câu số {i} ạ"},
                    headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.7"}).status_code for i in range(5)]
    assert codes[0] == 200 and codes[1:] == [429] * 4
    # Hai proxy tin cậy → lấy phần tử thứ 2 từ phải
    t2 = make_client(free_daily_chars=10, trust_proxy=True, trust_proxy_hops=2)
    t2.post("/api/tts", json={"text": "Mười ký tự"}, headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.9, 10.1.1.1"})
    assert t2.get("/api/quota", headers={"X-Forwarded-For": "203.0.113.9, 10.9.9.9"}).json()["used"] == 10


# ---------- jobs ----------


def test_job_lifecycle(client):
    r = client.post("/api/jobs", json={"text": LONG_TEXT, "voice": "Thùy Dung", "format": "ogg"})
    assert r.status_code == 202
    j = r.json()
    assert j["status"] == "queued" and len(j["job_id"]) >= 16
    assert client.get("/api/quota").json()["used"] == len(LONG_TEXT)  # tính quota khi nhận job
    s, seen = wait_job(client, j["job_id"])
    assert s["status"] == "done", s
    assert s["progress"] == 1.0 and s["done"] == s["total"] == 80
    res = s["result"]
    assert res["format"] == "ogg" and res["cached"] is False and len(res["segments"]) == 80
    assert client.get(res["audio_url"]).status_code == 200
    assert client.store.kv_get(job_key(j["job_id"]))["status"] == "done"
    # cùng nội dung → job xong ngay từ cache, không tính quota
    r2 = client.post("/api/jobs", json={"text": LONG_TEXT, "voice": "thuy-dung", "format": "ogg"})
    s2, _ = wait_job(client, r2.json()["job_id"])
    assert s2["result"]["cached"] is True and s2["result"]["id"] == res["id"]
    assert client.get("/api/quota").json()["used"] == len(LONG_TEXT)


def test_job_ids_unique_and_url_safe(client):
    ids = {client.post("/api/jobs", json={"text": f"Câu {i}."}).json()["job_id"] for i in range(5)}
    assert len(ids) == 5
    assert all(all(ch.isalnum() or ch in "-_" for ch in i) for i in ids)


def test_job_validation_and_not_found(client):
    r = client.post("/api/jobs", json={"text": "Chào", "voice": "Không Có"})
    assert r.status_code == 400 and err(r) == "unknown_voice"
    for jid in ("khongtontai123", "../../etc", "a"):
        r = client.get(f"/api/jobs/{jid}")
        assert r.status_code == 404 and err(r) in ("job_not_found", "not_found")


def test_job_engine_error_refunds(make_client):
    class Broken(FakeEngine):
        def synth(self, text, voice_id):
            raise RuntimeError("hỏng")

    c = make_client(engine=Broken())
    j = c.post("/api/jobs", json={"text": "Một câu. Hai câu."}).json()
    s, _ = wait_job(c, j["job_id"])
    assert s["status"] == "error" and s["error"]["code"] == "engine_error"
    assert c.get("/api/quota").json()["used"] == 0
    r = c.post("/api/tts", json={"text": "Một câu."})
    assert r.status_code == 500 and err(r) == "engine_error"
    assert c.get("/api/quota").json()["used"] == 0


def test_job_stale_marked_error(client):
    client.store.kv_set(job_key("staleJob123"), {"job_id": "staleJob123", "status": "running",
                                                 "updated_at": time.time() - 10_000, "quota_key": "q:x",
                                                 "charged": 5})
    client.store.kv_incr("q:x", 5)
    s = client.get("/api/jobs/staleJob123").json()
    assert s["status"] == "error" and s["error"]["code"] == "job_stale"
    assert client.store.kv_get("q:x") == 0
    client.get("/api/jobs/staleJob123")
    assert client.store.kv_get("q:x") == 0  # không hoàn hai lần


# ---------- files ----------


def test_file_download_headers_and_range(client):
    j = client.post("/api/tts", json={"text": "Tải xuống thử.", "format": "wav"}).json()
    r = client.get(j["audio_url"] + "?download=1")
    assert r.headers["content-disposition"] == f'attachment; filename="dattts-{j["id"]}.wav"'
    assert r.headers["content-type"] == "audio/wav"
    full = r.content
    p = client.get(j["audio_url"], headers={"Range": "bytes=10-19"})
    assert p.status_code == 206 and p.content == full[10:20]
    assert p.headers["content-range"] == f"bytes 10-19/{len(full)}"
    tail = client.get(j["audio_url"], headers={"Range": "bytes=-5"})
    assert tail.status_code == 206 and tail.content == full[-5:]
    assert client.get(j["audio_url"], headers={"Range": f"bytes={len(full) + 5}-"}).status_code == 416
    etag = r.headers["etag"]
    assert client.get(j["audio_url"], headers={"If-None-Match": etag}).status_code == 304
    srt = client.get(j["srt_url"] + "?download=1")
    assert srt.headers["content-disposition"].endswith(f'dattts-{j["id"]}.srt"')


def test_files_reject_traversal_and_unknown(client):
    j = client.post("/api/tts", json={"text": "Bí mật."}).json()
    rid = j["id"]
    bad = [
        f"/api/files/{rid}.json",  # metadata nội bộ
        f"/api/files/{rid}.exe",
        "/api/files/kv.json",
        "/api/files/..%2Fkv.json",
        "/api/files/%2e%2e%2fkv.json",
        "/api/files/../kv.json",
        "/api/files/..%2F..%2Fetc%2Fpasswd",
        "/api/files/sample-truc-ly.mp3",
        "/api/files/waitlist.json",
        f"/api/files/{rid.upper()}.mp3",
        "/api/files/0123456789abcdef0123.mp3",  # đúng dạng nhưng không có
    ]
    for url in bad:
        r = client.get(url)
        assert r.status_code == 404, url
        assert r.headers["content-type"].startswith("application/json"), url
        assert b"quota:" not in r.content


# ---------- waitlist ----------


def test_waitlist_dedupe(client):
    r = client.post("/api/waitlist", json={"email": "An@Example.com", "plan": "pro"})
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["position"] == 1
    assert client.post("/api/waitlist", json={"email": "binh@example.vn"}).json()["position"] == 2
    again = client.post("/api/waitlist", json={"email": "  an@example.COM "}).json()
    assert again["position"] == 1 and again["already"] is True
    assert len(client.store.kv_get("waitlist")) == 2
    assert client.store.blob_exists("waitlist.json")


@pytest.mark.parametrize("email", ["", "khong-phai-email", "a@b", "a b@c.com", "@x.com", "a" * 300 + "@x.com"])
def test_waitlist_bad_email(client, email):
    r = client.post("/api/waitlist", json={"email": email})
    assert r.status_code == 400 and err(r) == "bad_email"


def test_waitlist_restored_from_blob_when_kv_lost(client):
    client.post("/api/waitlist", json={"email": "a@x.com"})
    client.store._kv.pop("waitlist")
    assert client.post("/api/waitlist", json={"email": "b@x.com"}).json()["position"] == 2


def test_admin_waitlist_404_when_token_unset(client):
    r = client.get("/api/admin/waitlist", headers={"Authorization": "Bearer x"})
    assert r.status_code == 404 and err(r) == "not_found"


def test_admin_waitlist_auth(make_client):
    c = make_client(admin_token="s3cret-token")
    c.post("/api/waitlist", json={"email": "an@example.com", "plan": "pro", "note": "Cần giọng Nam Bộ"})
    r = c.get("/api/admin/waitlist")
    assert r.status_code == 401 and err(r) == "unauthorized"
    assert c.get("/api/admin/waitlist", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/api/admin/waitlist", headers={"Authorization": "s3cret-token"}).status_code == 401
    ok = c.get("/api/admin/waitlist", headers={"Authorization": "Bearer s3cret-token"})
    assert ok.status_code == 200
    j = ok.json()
    assert j["count"] == 1 and j["items"][0]["email"] == "an@example.com"
    assert j["items"][0]["note"] == "Cần giọng Nam Bộ"
    csv_ = c.get("/api/admin/waitlist?format=csv", headers={"Authorization": "Bearer s3cret-token"})
    assert csv_.headers["content-type"].startswith("text/csv") and "an@example.com" in csv_.text


# ---------- CORS, 404, static ----------


def test_cors_allowed_origin(client):
    r = client.get("/api/health", headers={"Origin": "https://dattts.com"})
    assert r.headers["access-control-allow-origin"] == "https://dattts.com"
    r = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    pre = client.options("/api/tts", headers={"Origin": "https://www.dattts.com",
                                              "Access-Control-Request-Method": "POST",
                                              "Access-Control-Request-Headers": "content-type,x-api-key"})
    assert pre.status_code == 200
    assert pre.headers["access-control-allow-origin"] == "https://www.dattts.com"


def test_unknown_api_route_and_method(client):
    r = client.get("/api/khong-co")
    assert r.status_code == 404 and err(r) == "not_found"
    r = client.get("/api/tts")
    assert r.status_code in (404, 405)
    err(r)


def test_static_web_served(make_client, tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>DatTTS</title>Đạt", encoding="utf-8")
    c = make_client(web_dir=str(web))
    r = c.get("/")
    assert r.status_code == 200 and "DatTTS" in r.text
    assert c.get("/api/health").json()["ok"] is True
    assert err(c.get("/api/nope")) == "not_found"
