import io
import re
import wave

import numpy as np
import pytest

from dattts import audio
from dattts.engine import DEFAULT_VOICE_ID, FakeEngine, VieNeuEngine, voice_id_from_name
from dattts.render import GAPS, RenderError, RenderRequest, plan, render, request_key

SRT_BLOCK = re.compile(
    r"(\d+)\n(\d\d):(\d\d):(\d\d),(\d{3}) --> (\d\d):(\d\d):(\d\d),(\d{3})\n(.+)\n?$"
)


def parse_srt(srt: str) -> list[dict]:
    out = []
    for block in srt.strip().split("\n\n"):
        m = SRT_BLOCK.match(block)
        assert m, f"khối SRT hỏng: {block!r}"
        g = m.groups()
        start = int(g[1]) * 3600 + int(g[2]) * 60 + int(g[3]) + int(g[4]) / 1000
        end = int(g[5]) * 3600 + int(g[6]) * 60 + int(g[7]) + int(g[8]) / 1000
        out.append({"n": int(g[0]), "start": start, "end": end, "text": g[9]})
    return out


def wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / w.getframerate()


# ───────────── engine helpers ─────────────

@pytest.mark.parametrize("name, vid", [
    ("Trúc Ly", "truc-ly"), ("Adam bựa", "adam-bua"), ("Thiền Tâm Đức", "thien-tam-duc"),
    ("Đoan Trang", "doan-trang"), ("  Mỹ  Duyên ", "my-duyen"), ("truc-ly", "truc-ly"),
])
def test_voice_id_from_name(name, vid):
    assert voice_id_from_name(name) == vid


def test_fake_engine_shape_and_determinism(fake_engine):
    vs = fake_engine.voices()
    assert len(vs) == 4 and DEFAULT_VOICE_ID in {v["id"] for v in vs}
    for v in vs:
        assert set(v) == {"id", "name", "gender", "region", "style", "style_label", "featured", "description"}
    a = fake_engine.synth("Xin chào", "truc-ly")
    b = FakeEngine().synth("Xin chào", "truc-ly")
    assert a.dtype == np.float32 and np.array_equal(a, b)
    assert abs(len(a) / 48000 - 0.06 * 8) < 0.01
    assert np.abs(a).max() <= 1.0


def test_vieneu_voices_metadata_without_loading_model():
    e = VieNeuEngine("/nonexistent-model-dir", lazy=True)
    vs = e.voices()
    assert len(vs) == 25 and not e.loaded
    ids = [v["id"] for v in vs]
    assert len(set(ids)) == 25 and "truc-ly" in ids and "adam-bua" in ids and "thien-tam-duc" in ids
    feat = [v["featured"] for v in vs if v["featured"] is not None]
    assert feat == sorted(feat) and len(feat) == 10
    assert all(v["featured"] is None for v in vs[len(feat):])
    assert {v["region"] for v in vs} == {"Bắc", "Trung", "Nam"}
    assert {v["style_label"] for v in vs} == {"Tự nhiên", "Tin tức", "Kể chuyện"}
    assert e.voice_name("truc-ly") == "Trúc Ly"
    assert e.voice_name("minh-quan") == "Hải Đăng"  # tên cũ vẫn dùng được


# ───────────── request_key ─────────────

def test_request_key_stable_and_sensitive():
    base = RenderRequest(text="Xin chào")
    k = request_key(base, "fake")
    assert re.fullmatch(r"[0-9a-f]{20}", k)
    assert k == request_key(RenderRequest(text="  Xin   chào "), "fake")
    assert k == request_key(RenderRequest(text="Xin chào", voice="Trúc Ly"), "fake")
    others = [
        RenderRequest(text="Xin chào!"),
        RenderRequest(text="Xin chào", voice="thien-minh"),
        RenderRequest(text="Xin chào", speed=1.2),
        RenderRequest(text="Xin chào", format="wav"),
        RenderRequest(text="Xin chào", dictionary=[{"from": "chào", "to": "chao"}]),
    ]
    keys = {request_key(r, "fake") for r in others} | {request_key(base, "vieneu-v3-turbo")}
    assert k not in keys and len(keys) == 6


# ───────────── plan ─────────────

def test_plan_multi_voice_and_gaps(fake_engine):
    req = RenderRequest(
        text="Mở đầu chương trình. Chúc vui.\n[Thiện Minh] Chào anh, TP.HCM hôm nay đẹp. [thuy-dung] Q3/2026 nhé!",
        dictionary=[{"from": "chúc", "to": "chúc các bạn"}],
    )
    items = plan(req, fake_engine)
    assert [it["voice"] for it in items] == ["truc-ly", "truc-ly", "thien-minh", "thuy-dung"]
    assert [it["text"] for it in items] == [
        "Mở đầu chương trình.", "Chúc vui.", "Chào anh, TP.HCM hôm nay đẹp.", "Q3/2026 nhé!"]
    assert items[1]["spoken"] == "chúc các bạn vui."
    assert items[3]["spoken"] == "quý 3 năm 2026 nhé!"
    assert [it["gap_after"] for it in items] == [GAPS["sentence"], GAPS["paragraph"], GAPS["voice_change"], 0.0]
    assert [it["index"] for it in items] == [0, 1, 2, 3]


def test_plan_default_voice_from_request(fake_engine):
    items = plan(RenderRequest(text="Một. Hai.", voice="Quang Sơn"), fake_engine)
    assert {it["voice"] for it in items} == {"quang-son"}


@pytest.mark.parametrize("req, code", [
    (RenderRequest(text="   "), "empty_text"),
    (RenderRequest(text="[Trúc Ly]"), "empty_text"),
    (RenderRequest(text="Chào", speed=2.0), "bad_speed"),
    (RenderRequest(text="Chào", speed=0.5), "bad_speed"),
    (RenderRequest(text="Chào", format="flac"), "bad_format"),
    (RenderRequest(text="Chào", dictionary=[{"from": "a", "to": "b"}] * 201), "bad_dictionary"),
    (RenderRequest(text="Chào", dictionary=[{"from": "a" * 101, "to": "b"}]), "bad_dictionary"),
    (RenderRequest(text="Chào", voice="Không Có"), "unknown_voice"),
    (RenderRequest(text="[Ai Đó] Chào"), "unknown_voice"),
])
def test_plan_errors(fake_engine, req, code):
    with pytest.raises(RenderError) as ei:
        plan(req, fake_engine)
    assert ei.value.code == code and ei.value.status == 400
    assert ei.value.message  # thông báo tiếng Việt cho người dùng


def test_unknown_voice_message_lists_names(fake_engine):
    with pytest.raises(RenderError) as ei:
        plan(RenderRequest(text="[Ai Đó] Chào"), fake_engine)
    assert "Ai Đó" in ei.value.message and "Trúc Ly" in ei.value.message


# ───────────── render (FakeEngine) ─────────────

def test_render_wav_timestamps_exact(fake_engine):
    req = RenderRequest(text="Xin chào các bạn. Hôm nay trời đẹp.\nĐoạn hai.", format="wav")
    seen = []
    res = render(req, fake_engine, progress=lambda d, t: seen.append((d, t)))
    assert seen == [(1, 3), (2, 3), (3, 3)]
    segs = res["segments"]
    assert [s["text"] for s in segs] == ["Xin chào các bạn.", "Hôm nay trời đẹp.", "Đoạn hai."]
    lens = [round(0.06 * len(s["text"]), 3) for s in segs]
    assert segs[0]["start"] == 0.0
    assert segs[0]["end"] == pytest.approx(lens[0], abs=0.002)
    assert segs[1]["start"] == pytest.approx(lens[0] + 0.3, abs=0.002)
    assert segs[2]["start"] == pytest.approx(segs[1]["end"] + 0.6, abs=0.002)
    assert res["duration"] == pytest.approx(segs[-1]["end"], abs=0.002)
    assert wav_seconds(res["audio"]) == pytest.approx(res["duration"], abs=0.002)
    assert res["chars"] == len("Xin chào các bạn. Hôm nay trời đẹp.\nĐoạn hai.")
    assert res["format"] == "wav" and re.fullmatch(r"[0-9a-f]{20}", res["id"])
    assert res["id"] == request_key(req, "fake")


def test_render_subtitles_use_original_text(fake_engine):
    res = render(RenderRequest(text="Doanh thu Q3/2026 đạt 100 USD. Xem dattts.com nhé."), fake_engine)
    cues = parse_srt(res["srt"])
    assert [c["text"] for c in cues] == ["Doanh thu Q3/2026 đạt 100 USD.", "Xem dattts.com nhé."]
    assert res["vtt"].startswith("WEBVTT")
    # engine nhận văn bản đã chuẩn hoá
    assert fake_engine.calls[0][0] == "Doanh thu quý 3 năm 2026 đạt 100 đô la Mỹ."
    assert res["audio"][:3] == b"ID3" or res["audio"][0] == 0xFF


def test_render_speed_applied_per_sentence(fake_engine):
    text = "Câu thứ nhất khá dài một chút. Câu thứ hai."
    normal = render(RenderRequest(text=text, format="wav"), fake_engine)
    fast = render(RenderRequest(text=text, format="wav", speed=1.5), fake_engine)
    assert fast["duration"] == pytest.approx(normal["duration"] / 1.5, rel=0.03)
    for a, b in zip(normal["segments"], fast["segments"]):
        assert b["end"] - b["start"] == pytest.approx((a["end"] - a["start"]) / 1.5, rel=0.03)


class EmptyOnceEngine(FakeEngine):
    def synth(self, text, voice_id):
        if "trống" in text:
            return np.zeros(0, dtype=np.float32)
        return super().synth(text, voice_id)


def test_render_skips_empty_sentence():
    res = render(RenderRequest(text="Câu một. Câu trống. Câu ba.", format="wav"), EmptyOnceEngine())
    segs = res["segments"]
    assert [s["text"] for s in segs] == ["Câu một.", "Câu ba."]
    assert [s["index"] for s in segs] == [0, 1]
    gap = segs[1]["start"] - segs[0]["end"]
    assert gap == pytest.approx(0.3 + 0.05, abs=0.002)


def test_render_engine_failure_is_render_error():
    class Boom(FakeEngine):
        def synth(self, text, voice_id):
            raise RuntimeError("onnx nổ")

    with pytest.raises(RenderError) as ei:
        render(RenderRequest(text="Chào."), Boom())
    assert ei.value.code == "engine_error" and ei.value.status == 500


def test_render_segments_monotonic_multi_voice(fake_engine):
    text = ("[Trúc Ly] Chào mừng các bạn đến với podcast DatTTS. Hôm nay chúng ta nói về giọng đọc AI.\n"
            "[Thiện Minh] Cảm ơn Ly. Công nghệ này đã tiến bộ rất nhiều trong năm 2026!\n"
            "[Trúc Ly] Đúng vậy, hẹn gặp lại các bạn.")
    res = render(RenderRequest(text=text, format="ogg"), fake_engine)
    segs = res["segments"]
    assert len(segs) == 5
    prev = 0.0
    for s in segs:
        assert s["start"] >= prev and s["end"] > s["start"]
        prev = s["end"]
    assert [s["voice"] for s in segs] == ["truc-ly", "truc-ly", "thien-minh", "thien-minh", "truc-ly"]
    assert res["audio"][:4] == b"OggS"


# ───────────── render (VieNeu thật) ─────────────

@pytest.mark.real
def test_real_engine_multi_voice_render(real_engine):
    text = ("[Trúc Ly] Xin chào, mình là DatTTS. Doanh thu Q3/2026 tăng 15%.\n"
            "[Thiện Minh] Cảm ơn bạn. Hẹn gặp lại!")
    progress = []
    res = render(RenderRequest(text=text, format="mp3", speed=1.1), real_engine,
                 progress=lambda d, t: progress.append((d, t)))
    assert res["duration"] > 1.0
    assert progress[-1] == (4, 4)
    segs = res["segments"]
    assert [s["voice"] for s in segs] == ["truc-ly", "truc-ly", "thien-minh", "thien-minh"]
    prev = 0.0
    for s in segs:
        assert s["start"] >= prev and s["end"] > s["start"]
        prev = s["end"]
    assert segs[-1]["end"] <= res["duration"] + 1e-3
    cues = parse_srt(res["srt"])
    assert [c["text"] for c in cues] == [s["text"] for s in segs]
    assert cues[0]["text"] == "Xin chào, mình là DatTTS."
    assert len(res["audio"]) > 5000 and res["id"] == request_key(
        RenderRequest(text=text, format="mp3", speed=1.1), "vieneu-v3-turbo")


@pytest.mark.real
def test_real_engine_synth_basic(real_engine):
    wav = real_engine.synth("Chào buổi sáng.", "mai-anh")
    assert wav.dtype == np.float32 and len(wav) > 0.3 * real_engine.sample_rate
    assert np.abs(wav).max() <= 1.0 + 1e-3
    assert real_engine.sample_rate == 48000
    assert len(real_engine.synth("   ", "mai-anh")) == 0


def test_render_punctuation_only_sentence_not_synthesized(fake_engine):
    res = render(RenderRequest(text="Mở đầu.\n...\nKết thúc.", format="wav"), fake_engine)
    assert [s["text"] for s in res["segments"]] == ["Mở đầu.", "Kết thúc."]
    assert [c[0] for c in fake_engine.calls] == ["Mở đầu.", "Kết thúc."]


@pytest.mark.real
def test_real_engine_gaps_follow_spec(real_engine):
    res = render(RenderRequest(text="Một hai ba. Bốn năm sáu.\nBảy tám chín.", format="wav"), real_engine)
    s = res["segments"]
    assert len(s) == 3
    assert s[1]["start"] - s[0]["end"] == pytest.approx(0.3, abs=0.01)
    assert s[2]["start"] - s[1]["end"] == pytest.approx(0.6, abs=0.01)
    assert wav_seconds(res["audio"]) == pytest.approx(res["duration"], abs=0.01)
