"""Regression test cho các lỗi đã xác nhận ở vòng review (quota, job, bộ nhớ, Modal store, văn bản)."""

import json
import threading
import time
import tracemalloc
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from dattts.api import create_app
from dattts.engine import FakeEngine
from dattts.render import MAX_SENTENCE_CHARS, RenderError, RenderRequest, billable_chars, plan, render
from dattts.settings import Settings
from dattts.store import LocalRenderer, LocalStore, ModalStore, job_key, render_and_store, run_job
from dattts.textproc import apply_builtin_rules, split_sentences
from test_api import make_client  # noqa: F401  (fixture dùng chung)

BOMB = [{"from": "a", "to": "x" * 100}]


# ───────────── #1 từ điển làm văn bản dài ra ─────────────


def test_dictionary_expansion_is_billed_and_resplit(fake_engine):
    req = RenderRequest(text=" ".join(["a"] * 1500), dictionary=BOMB)
    items = plan(req, fake_engine)
    longest = max(len(p) for it in items for p in it["parts"])
    assert longest <= MAX_SENTENCE_CHARS
    spoken = sum(len(it["spoken"]) for it in items)
    assert billable_chars(req, items) >= spoken - len(items) * 2  # tính theo văn bản thực đọc


def test_dictionary_without_expansion_bills_text_only(fake_engine):
    req = RenderRequest(text="Năm nay KPI tăng.", dictionary=[{"from": "KPI", "to": "kê pi ai"}])
    assert billable_chars(req, plan(req, fake_engine)) == len("Năm nay KPI tăng.") + len("kê pi ai") - 3
    req2 = RenderRequest(text="Năm nay KPI tăng.", dictionary=[{"from": "KPI", "to": "chỉ"}])
    assert billable_chars(req2, plan(req2, fake_engine)) == len("Năm nay KPI tăng.")


def test_api_dictionary_bomb_rejected_and_billed(make_client):
    c = make_client(free_daily_chars=100000)
    text = " ".join(["a"] * 45)
    r = c.post("/api/tts", json={"text": text, "dictionary": BOMB})
    assert r.status_code == 413, r.text  # 45×100 > 3000 ký tự đồng bộ
    assert r.json()["error"]["code"] == "too_long"
    assert c.get("/api/quota").json()["used"] == 0
    ok = c.post("/api/tts", json={"text": "a a", "dictionary": BOMB})
    assert ok.status_code == 200
    assert ok.json()["quota"]["used"] == ok.json()["chars"] >= 200
    longest = max(len(t) for t, _ in c.engine.calls)
    assert longest <= MAX_SENTENCE_CHARS


# ───────────── #2 job bị đánh dấu hỏng vẫn chạy, hoàn quota hai lần ─────────────


def _job_rec(store, jid, status="queued", charged=100, **kw):
    rec = {"job_id": jid, "status": status, "progress": 0.0, "done": 0, "total": 0, "chars": charged,
           "created_at": time.time(), "updated_at": time.time(), "quota_key": "q:test", "charged": charged, **kw}
    store.kv_set(job_key(jid), rec)
    store.kv_incr("q:test", charged)
    return rec


def test_run_job_skips_job_already_marked_stale(tmp_path, fake_engine):
    store = LocalStore(str(tmp_path))
    _job_rec(store, "jobstale1", status="error", refunded=True)
    store.kv_incr("q:test", -100)  # API đã hoàn
    called = []
    out = run_job(store, "jobstale1", RenderRequest(text="Xin chào."), lambda r, p: called.append(1))
    assert not called and out["status"] == "error"
    assert store.kv_get("q:test") == 0


def test_run_job_never_double_refunds(tmp_path):
    store = LocalStore(str(tmp_path))
    _job_rec(store, "jobfail01")

    def fail(req, progress):
        # Trong lúc chạy, API đánh dấu quá hạn + hoàn quota
        rec = store.kv_get(job_key("jobfail01"))
        rec.update(status="error", refunded=True)
        store.kv_set(job_key("jobfail01"), rec)
        store.kv_incr("q:test", -100)
        raise RenderError("engine_error", "lỗi", 500)

    out = run_job(store, "jobfail01", RenderRequest(text="Xin chào."), fail)
    assert out["status"] == "error" and out["refunded"]
    assert store.kv_get("q:test") == 0  # không âm


def test_run_job_success_after_stale_refund_recharges(tmp_path, fake_engine):
    store = LocalStore(str(tmp_path))
    _job_rec(store, "jobdone01")

    def ok(req, progress):
        rec = store.kv_get(job_key("jobdone01"))
        rec.update(status="error", refunded=True)
        store.kv_set(job_key("jobdone01"), rec)
        store.kv_incr("q:test", -100)
        return render_and_store(fake_engine, store, req, progress)

    out = run_job(store, "jobdone01", RenderRequest(text="Xin chào."), ok)
    assert out["status"] == "done" and not out.get("refunded") and "error" not in out
    assert store.kv_get("q:test") == 100  # không được miễn phí


def test_queued_job_uses_longer_stale_threshold(make_client):
    c = make_client(job_stale_seconds=1, job_queue_stale_seconds=3600)
    jid = "queuedjob123"
    rec = {"job_id": jid, "status": "queued", "progress": 0.0, "done": 0, "total": 0, "chars": 5,
           "created_at": time.time() - 30, "updated_at": time.time() - 30}
    c.store.kv_set(job_key(jid), rec)
    assert c.get(f"/api/jobs/{jid}").json()["status"] == "queued"
    rec["status"] = "running"
    c.store.kv_set(job_key(jid), rec)
    assert c.get(f"/api/jobs/{jid}").json()["status"] == "error"


# ───────────── #4/#12 bộ nhớ khi dựng và khi phục vụ file ─────────────


def test_render_does_not_hold_whole_waveform(tmp_path):
    eng = FakeEngine()
    text = " ".join(f"Đây là câu thử nghiệm số {i} để kiểm tra bộ nhớ khi dựng bản dài." for i in range(60))
    store = LocalStore(str(tmp_path))
    tracemalloc.start()
    meta = render_and_store(eng, store, RenderRequest(text=text, format="wav"))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    full_f32 = meta["duration"] * eng.sample_rate * 4
    assert full_f32 > 40e6
    assert peak < full_f32 * 0.25, (peak, full_f32)
    with wave.open(str(store.blob_path(f"{meta['id']}.wav"))) as w:
        assert abs(w.getnframes() / w.getframerate() - meta["duration"]) < 0.01


def test_files_are_streamed_with_range(make_client):
    c = make_client()
    r = c.post("/api/tts", json={"text": "Xin chào các bạn.", "format": "wav"}).json()
    full = c.get(r["audio_url"])
    assert full.status_code == 200 and int(full.headers["content-length"]) == len(full.content)
    part = c.get(r["audio_url"], headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == full.content[10:20]
    assert part.headers["content-range"] == f"bytes 10-19/{len(full.content)}"
    assert c.get(r["audio_url"], headers={"Range": "bytes=99999999-"}).status_code == 416
    head = c.head(r["audio_url"])
    assert head.status_code == 200 and head.headers["content-length"] == str(len(full.content))


# ───────────── #5 body quá lớn ─────────────


def test_huge_body_rejected_before_parsing(make_client):
    c = make_client()
    big = "a" * (c.settings.job_max_chars * 6 + 600 * 1024)
    t0 = time.time()
    r = c.post("/api/tts", content=json.dumps({"text": big}), headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_long"
    assert time.time() - t0 < 2.0


def test_long_text_rejected_before_clean_text(make_client, monkeypatch):
    import dattts.render as R

    c = make_client()
    calls = []
    real = R.clean_text
    monkeypatch.setattr(R, "clean_text", lambda t: calls.append(len(t)) or real(t))
    r = c.post("/api/tts", json={"text": "chữ " * 5000})
    assert r.status_code == 413 and not calls


# ───────────── #6 kv_incr nguyên tử giữa các container ─────────────


class _FakeDict:
    """Giả modal.Dict dùng chung giữa nhiều 'container'; get/put chậm để lộ race."""

    def __init__(self):
        self.d, self.mu = {}, threading.Lock()

    def get(self, k, default=None):
        time.sleep(0.0005)
        with self.mu:
            return json.loads(json.dumps(self.d.get(k, default)))

    def put(self, k, v, skip_if_exists=False):
        time.sleep(0.0005)
        with self.mu:
            if skip_if_exists and k in self.d:
                return False
            self.d[k] = v
            return True

    def pop(self, k):
        with self.mu:
            return self.d.pop(k)


class _FakeVolume:
    def __init__(self, mount, staged):
        self.mount, self.staged = mount, staged
        self.reloads = 0

    def reload(self):
        self.reloads += 1
        for name, data in list(self.staged.items()):
            (self.mount / name).write_bytes(data)

    def commit(self):
        pass


def test_modal_kv_incr_atomic_across_containers(tmp_path):
    d = _FakeDict()
    stores = [ModalStore(mount=str(tmp_path), dict_obj=d, volume_obj=_FakeVolume(tmp_path, {})) for _ in range(2)]
    threads = [threading.Thread(target=lambda s=s: [s.kv_incr("quota:x", 1) for _ in range(100)]) for s in stores]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert d.d["quota:x"] == 200
    assert not any(k.startswith("lock:") for k in d.d)


def test_modal_kv_incr_steals_abandoned_lock(tmp_path):
    d = _FakeDict()
    d.d["lock:k"] = {"o": "dead", "t": time.time() - 60}
    s = ModalStore(mount=str(tmp_path), dict_obj=d, volume_obj=_FakeVolume(tmp_path, {}))
    assert s.kv_incr("k", 5) == 5


# ───────────── #11 reload bị bỏ qua → 404 giả ─────────────


def test_modal_blob_miss_after_recent_reload_still_reloads(tmp_path):
    staged = {}
    vol = _FakeVolume(tmp_path, staged)
    s = ModalStore(mount=str(tmp_path), dict_obj=_FakeDict(), volume_obj=vol)
    assert s.blob_get("aaaaaaaaaaaaaaaaaaaa.mp3") is None  # request A trượt → reload
    staged["bbbbbbbbbbbbbbbbbbbb.mp3"] = b"ID3data"  # worker commit file của B
    assert s.blob_get("bbbbbbbbbbbbbbbbbbbb.mp3") == b"ID3data"  # 0 s sau vẫn thấy
    assert vol.reloads == 2


# ───────────── #16 đổi định dạng không tính quota lần nữa ─────────────


def test_alt_format_is_transcoded_free(make_client):
    c = make_client()
    body = {"text": "Xin chào, đây là bản thử định dạng."}
    a = c.post("/api/tts", json=body).json()
    calls = len(c.engine.calls)
    b = c.post("/api/tts", json={**body, "format": "wav"})
    assert b.status_code == 200, b.text
    b = b.json()
    assert b["cached"] and b["format"] == "wav" and b["id"] != a["id"]
    assert b["quota"]["used"] == a["quota"]["used"]
    assert len(c.engine.calls) == calls  # không dựng lại giọng
    assert abs(b["duration"] - a["duration"]) < 0.01 and b["segments"] == a["segments"]
    wav = c.get(b["audio_url"])
    assert wav.status_code == 200 and wav.content[:4] == b"RIFF"
    assert c.get(b["srt_url"]).status_code == 200
    j = c.post("/api/jobs", json={**body, "format": "ogg"}).json()
    assert j["status"] == "done"


# ───────────── #22 ngoặc vuông không phải tên giọng ─────────────


@pytest.mark.parametrize("text", ["Xem chú thích [1] và [2] ở cuối trang.", "Anh ấy [cười] nói vậy.",
                                  "Theo Wikipedia[a][cần dẫn nguồn], đây là sự thật."])
def test_non_voice_brackets_are_dropped(fake_engine, text):
    items = plan(RenderRequest(text=text), fake_engine)
    assert len(items) == 1 and "[" not in items[0]["spoken"]


def test_name_like_unknown_bracket_still_errors(fake_engine):
    with pytest.raises(RenderError) as e:
        plan(RenderRequest(text="[Ai Đó] Chào"), fake_engine)
    assert e.value.code == "unknown_voice"


# ───────────── #23 chỉ có dấu câu ─────────────


def test_punctuation_only_rejected_before_charge(make_client, fake_engine):
    with pytest.raises(RenderError) as e:
        render(RenderRequest(text="... !!! ??? ---", format="wav"), fake_engine)
    assert e.value.code == "empty_audio"
    c = make_client()
    r = c.post("/api/tts", json={"text": "... !!! ??? ---"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "empty_audio"
    assert c.get("/api/quota").json()["used"] == 0


# ───────────── văn bản: #7/#25, #8, #24, #26–#31 ─────────────


@pytest.mark.parametrize("src,expected", [
    ("thắng 1-2 chung cuộc", "thắng 1-2 chung cuộc"),
    ("Đội thắng 3-5 trên sân khách", "Đội thắng 3-5 trên sân khách"),
    ("tỷ số 2-3", "tỷ số 2-3"),
    ("Nghỉ lễ 30-4 và 1-5 nhé", "Nghỉ lễ 30-4 và ngày 1-5 nhé"),
    ("Quốc khánh 2-9 nghỉ", "Quốc khánh ngày 2-9 nghỉ"),
    ("dịp 2-9", "dịp ngày 2-9"),
    ("Mất 5-7 ngày.", "Mất 5 đến 7 ngày."),
    ("Vé 150k-200k", "Vé 150 đến 200 nghìn"),
    ("Văn phòng tại 123 Nguyễn Huệ, P. Bến Nghé, Q.1", "Văn phòng tại 123 Nguyễn Huệ, phường Bến Nghé, Q.1"),
    ("45 Lê Lợi, Q. 3, H. Củ Chi", "45 Lê Lợi, quận 3, huyện Củ Chi"),
    ("Giá 1.5 tấn, 37.5°C, 2.5 lít, 0.5 mm.", "Giá 1,5 tấn, 37,5°C, 2,5 lít, 0,5 mm."),
    ("Truy cập https://dattts.com/pricing để xem.", "Truy cập Đạt tê tê ét chấm com để xem."),
    ("Đọc www.vnexpress.net/the-thao nhé", "Đọc vnexpress.net nhé"),
    ("Xem https://example.com/a/b?x=1.", "Xem example.com."),
    ("Lỗ -2,5 tỷ đồng", "Lỗ âm 2,5 tỷ đồng"),
    ("tăng trưởng -1,2%", "tăng trưởng -1,2%"),
    ("Gọi 028.3822.1234 nhé.", "Gọi 028 3822 1234 nhé."),
    ("1.234.567 đồng", "1.234.567 đồng"),
    ("1. Mở ứng dụng.", "1, Mở ứng dụng."),
    ("II. Nội dung chính.", "2, Nội dung chính."),
    ("Ông ấy sống ở TP.", "Ông ấy sống ở thành phố."),
    ("TP. Huế là cố đô.", "thành phố Huế là cố đô."),
])
def test_review_text_rules(src, expected):
    assert apply_builtin_rules(src) == expected


def _texts(t, **kw):
    return [s for s, _ in split_sentences(t, **kw)]


def test_split_never_cuts_grouped_numbers():
    for chunk in _texts("Dân số khoảng 100 000 000 người " * 9, max_chars=60):
        assert not chunk.split()[0].isdigit() or not chunk.startswith("000")
        assert not chunk.endswith("100 000")


def test_list_markers_stay_with_their_item():
    assert _texts("Các bước thực hiện:\n1. Mở ứng dụng.\n2. Chọn giọng đọc.") == [
        "Các bước thực hiện:", "1. Mở ứng dụng.", "2. Chọn giọng đọc."]
    assert _texts("Có ba lưu ý: 1. Uống đủ nước. 2. Ngủ đủ giấc.") == [
        "Có ba lưu ý:", "1. Uống đủ nước.", "2. Ngủ đủ giấc."]
    assert _texts("A. Mở đầu. B. Nội dung.") == ["A. Mở đầu.", "B. Nội dung."]
    assert _texts("Năm 2020. Tôi đi học.") == ["Năm 2020.", "Tôi đi học."]


def test_tp_at_sentence_end_and_address_h():
    assert _texts("Ông ấy sống ở TP. Ông ấy rất vui.") == ["Ông ấy sống ở TP.", "Ông ấy rất vui."]
    assert _texts("Tôi ở TP. Hồ Chí Minh. Xin chào.") == ["Tôi ở TP. Hồ Chí Minh.", "Xin chào."]
    assert _texts("Phòng 205, Q. Gò Vấp, H. Củ Chi.") == ["Phòng 205, Q. Gò Vấp, H. Củ Chi."]


def test_modal_renderer_routes_jobs_to_job_worker(tmp_path):
    modal_app = pytest.importorskip("modal_app")

    class W:
        def __init__(self):
            self.spawned = []
            self.run_job = self

        def spawn(self, *a):
            self.spawned.append(a)

    sync, jobs = W(), W()
    r = modal_app.ModalRenderer(LocalStore(str(tmp_path)), worker=sync, job_worker=jobs)
    r.submit("job12345", RenderRequest(text="Xin chào."))
    assert jobs.spawned and not sync.spawned
    assert hasattr(modal_app, "TTSJobWorker")
