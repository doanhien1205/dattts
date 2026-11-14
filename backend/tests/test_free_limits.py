"""Trần chi phí gói miễn phí (trần chung/ngày, giới hạn lượt/phút) và tự xoá file cũ."""
import os
import time

from dattts.store import purge_old_files
from test_api import make_client  # noqa: F401  (fixture)


def err(resp):
    return resp.json()["error"]


def total(client):
    return sum(v for k, v in client.store._kv.items() if k.endswith(":free_total"))


def test_global_free_cap_blocks_and_rolls_back(make_client):
    c = make_client(free_global_daily_chars=30, free_requests_per_min=0)
    assert c.post("/api/tts", json={"text": "Xin chào các bạn nhé."}).status_code == 200   # 21 ký tự
    assert total(c) == 21
    r = c.post("/api/tts", json={"text": "Một câu khác hẳn câu trước."})                 # vượt trần 30
    assert r.status_code == 429 and err(r)["code"] == "quota_exceeded"
    assert "free tier is at capacity" in err(r)["message"]
    assert total(c) == 21                       # đã hoàn bộ đếm chung
    assert c.get("/api/quota").json()["used"] == 21   # và quota IP


def test_global_cap_message_vietnamese(make_client):
    c = make_client(free_global_daily_chars=5, free_requests_per_min=0)
    r = c.post("/api/tts", json={"text": "Xin chào các bạn."}, headers={"Accept-Language": "vi"})
    assert r.status_code == 429 and "hết lượt chung" in err(r)["message"]


def test_global_cap_zero_means_unlimited(make_client):
    c = make_client(free_global_daily_chars=0, free_requests_per_min=0)
    assert c.post("/api/tts", json={"text": "Xin chào."}).status_code == 200
    assert total(c) == 0


def test_cache_hit_does_not_count_against_global_cap(make_client):
    c = make_client(free_global_daily_chars=1000, free_requests_per_min=0)
    for _ in range(3):
        assert c.post("/api/tts", json={"text": "Lặp lại."}).status_code == 200
    assert total(c) == len("Lặp lại.")


def test_internal_key_ignores_global_cap_and_rate(make_client):
    c = make_client(free_global_daily_chars=1, free_requests_per_min=1, api_keys=frozenset({"k1"}))
    for i in range(3):
        r = c.post("/api/tts", json={"text": f"Câu số {i}."}, headers={"X-API-Key": "k1"})
        assert r.status_code == 200


def test_free_requests_per_minute(make_client):
    c = make_client(free_requests_per_min=2)
    assert c.post("/api/tts", json={"text": "Một."}).status_code == 200
    assert c.post("/api/tts", json={"text": "Hai."}).status_code == 200
    r = c.post("/api/tts", json={"text": "Ba."})
    assert r.status_code == 429 and err(r)["code"] == "rate_limited"
    assert r.headers["retry-after"] == "60"
    assert c.get("/api/quota").json()["used"] == len("Một.") + len("Hai.")


def test_failed_render_refunds_global_counter(make_client):
    from dattts.engine import FakeEngine

    class Boom(FakeEngine):
        def synth(self, text, voice_id):
            raise RuntimeError("boom")

    c = make_client(engine=Boom(), free_requests_per_min=0)
    assert c.post("/api/tts", json={"text": "Xin chào."}).status_code >= 500
    assert total(c) == 0


def _touch(path, age_days, now):
    path.write_bytes(b"x")
    t = now - age_days * 86400
    os.utime(path, (t, t))


def test_purge_old_files_only_removes_old_render_files(tmp_path):
    now = time.time()
    old = "a" * 20
    new = "b" * 20
    for ext in ("mp3", "wav", "ogg", "srt", "vtt", "json"):
        _touch(tmp_path / f"{old}.{ext}", 31, now)
    _touch(tmp_path / f"{new}.mp3", 29, now)
    _touch(tmp_path / "sample-truc-ly.mp3", 400, now)
    _touch(tmp_path / "ledger-abc.json", 400, now)
    _touch(tmp_path / "waitlist.json", 400, now)
    assert purge_old_files(tmp_path, 30, now) == 6
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{new}.mp3", "ledger-abc.json", "sample-truc-ly.mp3",
                                                          "waitlist.json"]


def test_purge_disabled_and_missing_dir(tmp_path):
    now = time.time()
    _touch(tmp_path / ("c" * 20 + ".mp3"), 999, now)
    assert purge_old_files(tmp_path, 0, now) == 0
    assert purge_old_files(tmp_path / "nope", 30, now) == 0


def test_purged_render_is_rebuilt_on_next_request(make_client):
    c = make_client(free_requests_per_min=0)
    r1 = c.post("/api/tts", json={"text": "Dựng lại nhé."}).json()
    assert c.store.blob_purge(30, now=time.time() + 31 * 86400) == 4      # mp3 + srt + vtt + json
    assert c.get(r1["audio_url"]).status_code == 404
    r2 = c.post("/api/tts", json={"text": "Dựng lại nhé."}).json()
    assert r2["cached"] is False and c.get(r2["audio_url"]).status_code == 200
