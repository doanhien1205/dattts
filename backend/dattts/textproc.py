"""Xử lý văn bản thuần Python (không phụ thuộc engine).

- clean_text / count_chars: chuẩn hoá và đếm ký tự tính phí.
- parse_script: cú pháp nhiều giọng "[Tên giọng] ...".
- apply_dictionary: từ điển phát âm của người dùng.
- apply_builtin_rules: vá những chỗ bộ chuẩn hoá của VieNeu đọc kém (đã dò bằng
  ``vieneu_utils.phonemize_text.normalize_to_chunks_v3_with_gaps``). Chỉ thêm luật
  cho lỗi thật; số, ngày, tiền, đơn vị engine đã đọc tốt nên KHÔNG đụng vào.
- split_sentences: tách câu tiếng Việt, giữ nguyên số "1.000", "3,5", "TP.HCM", "v.v."
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

__all__ = [
    "Segment",
    "clean_text",
    "count_chars",
    "parse_script",
    "apply_dictionary",
    "apply_builtin_rules",
    "split_sentences",
]


@dataclass
class Segment:
    voice: str | None
    text: str


# ───────────────────────────── clean_text ─────────────────────────────

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
_HSPACE = re.compile(r"[ \t  -   　]+")


def clean_text(text: str) -> str:
    """NFC, bỏ ký tự điều khiển/zero-width, gộp khoảng trắng; giữ xuống dòng (tối đa 1 dòng trống)."""
    if not text:
        return ""
    t = unicodedata.normalize("NFC", str(text))
    t = t.replace("\r\n", "\n").replace("\r", "\n").replace(" ", "\n").replace(" ", "\n")
    t = t.translate(_ZERO_WIDTH)
    t = "".join(
        ch if ch in "\n\t" or unicodedata.category(ch)[0] != "C" else " " for ch in t
    )
    t = _HSPACE.sub(" ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def count_chars(text: str) -> int:
    return len(clean_text(text))


# ───────────────────────────── parse_script ─────────────────────────────

_TAG = re.compile(r"\[([^\[\]\n]{1,48})\]")


def parse_script(text: str) -> list[Segment]:
    """"[Trúc Ly] Chào anh. [Thiện Minh] Chào em." → các Segment.

    Văn bản trước thẻ đầu tiên dùng giọng mặc định (voice=None). Segment rỗng bị bỏ.
    Tên trong ngoặc được giữ nguyên (đã strip) — render.py chịu trách nhiệm resolve.
    """
    text = clean_text(text)
    out: list[Segment] = []
    voice: str | None = None
    pos = 0
    for m in _TAG.finditer(text):
        chunk = text[pos:m.start()].strip()
        if chunk:
            out.append(Segment(voice, chunk))
        name = m.group(1).strip()
        voice = name or None
        pos = m.end()
    chunk = text[pos:].strip()
    if chunk:
        out.append(Segment(voice, chunk))
    return out


# ───────────────────────────── apply_dictionary ─────────────────────────────

def _word_pattern(word: str) -> str:
    esc = re.escape(word)
    # Chỉ đặt ranh giới "nguyên từ" ở phía là chữ/số; mục bắt đầu/kết thúc bằng dấu câu vẫn khớp.
    left = r"(?<!\w)" if word[:1].isalnum() or word[:1] == "_" else ""
    right = r"(?!\w)" if word[-1:].isalnum() or word[-1:] == "_" else ""
    return left + esc + right


def apply_dictionary(text: str, entries: list[dict]) -> str:
    """Thay nguyên từ, không phân biệt hoa thường, cụm dài được ưu tiên; thay một lượt (không đệ quy)."""
    if not text or not entries:
        return text
    mapping: dict[str, str] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        src = unicodedata.normalize("NFC", str(e.get("from", "") or "")).strip()
        dst = unicodedata.normalize("NFC", str(e.get("to", "") or "")).strip()
        if src:
            mapping.setdefault(src.casefold(), dst)
    if not mapping:
        return text
    keys = sorted(mapping, key=len, reverse=True)
    pat = re.compile("|".join(_word_pattern(k) for k in keys), re.IGNORECASE)

    def rep(m: re.Match) -> str:
        return mapping.get(m.group(0).casefold(), m.group(0))

    return pat.sub(rep, text)


# ───────────────────────────── apply_builtin_rules ─────────────────────────────
# Mỗi luật ứng với một lỗi đọc đã quan sát được ở bộ chuẩn hoá VieNeu 3.8.3
# (ghi ở chú thích "engine:"). Có test tương ứng trong tests/test_textproc.py.

_L = r"A-Za-zÀ-ỹĐđ"  # chữ cái Latin + tiếng Việt

_LEGAL_CODES = {
    # engine: "NĐ-CP" → "nờ đê xê phê"
    "NĐ-CP": "nghị định Chính phủ",
    "NQ-CP": "nghị quyết Chính phủ",
    # engine: "QĐ-TTg" → "qui đê tê tg"
    "QĐ-TTg": "quyết định Thủ tướng",
    "CT-TTg": "chỉ thị Thủ tướng",
    "QĐ-UBND": "quyết định Ủy ban nhân dân",
    "QĐ-BYT": "quyết định Bộ Y tế",
    # engine: "TT-BTC" → "tê tê bê tê xê"
    "TT-BTC": "thông tư Bộ Tài chính",
    "TT-BGDĐT": "thông tư Bộ Giáo dục và Đào tạo",
    "TT-BYT": "thông tư Bộ Y tế",
    "TT-NHNN": "thông tư Ngân hàng Nhà nước",
    "NQ/TW": "nghị quyết Trung ương",
    "NQ-TW": "nghị quyết Trung ương",
}
_LEGAL_ALT = "|".join(re.escape(k) for k in sorted(_LEGAL_CODES, key=len, reverse=True))

_TITLES = [
    # engine: "PGS.TS. Lê" → "phó giáo sư tiến sĩ. lê" (dấu chấm gây ngắt câu giữa chừng)
    ("PGS.TS.", "phó giáo sư tiến sĩ"),
    ("GS.TS.", "giáo sư tiến sĩ"),
    ("TS.BS.", "tiến sĩ bác sĩ"),
    ("ThS.BS.", "thạc sĩ bác sĩ"),
    ("BSCKII.", "bác sĩ chuyên khoa hai"),
    ("BSCKI.", "bác sĩ chuyên khoa một"),
    ("PGS.", "phó giáo sư"),
    ("GS.", "giáo sư"),
    ("TS.", "tiến sĩ"),
    ("ThS.", "thạc sĩ"),
    ("BS.", "bác sĩ"),
    ("KS.", "kỹ sư"),
    ("NSƯT.", "nghệ sĩ ưu tú"),
    ("NSND.", "nghệ sĩ nhân dân"),
    # engine: "Mr. Bean" → "mr, bean"
    ("Mrs.", "mi-sít"),
    ("Mr.", "mít-tơ"),
    ("Ms.", "mít"),
    ("Dr.", "tiến sĩ"),
]

# Thành phố một từ (sau "TP." viết hoa rồi tới chữ thường vẫn là tên thành phố: "TP. Huế là…")
_ONE_WORD_CITIES = {"huế", "vinh", "pleiku", "kon tum", "sa đéc", "hcm", "hn"}
_CAP_WORD = re.compile(r"[A-ZÀ-ỸĐ][\wÀ-ỹđĐ]*")


def _tp_continues(after: str) -> bool:
    """"TP." + phần sau: True nếu phần sau là tên thành phố ("TP. Hồ Chí Minh", "TP. Huế"), False nếu
    "TP." kết thúc câu ("sống ở TP. Ông ấy rất vui")."""
    words = after.strip().split()
    if not words:
        return False
    w0 = words[0].strip(".,;:!?…\"'”)")
    if not w0 or not (w0[0].isupper() or w0[0].isdigit()):
        return False
    if w0.isupper() and len(w0) >= 2 or w0.lower() in _ONE_WORD_CITIES or w0[0].isdigit():
        return True
    if w0 != words[0]:  # "TP. Vinh." / "TP. Vinh, …" — tên một từ rồi dấu câu
        return True
    if len(words) == 1:
        return True
    w1 = words[1]
    return bool(w1) and w1[0].isupper()


_RULES: list[tuple[re.Pattern, object]] = []


def _rule(pattern: str, repl, flags: int = 0) -> None:
    _RULES.append((re.compile(pattern, flags), repl))


# 1) Thương hiệu. engine: "dattts.com" → "dat tê tê ét chấm com"; "DatTTS" → "dat t t s" (tiếng Anh)
_URL_PATH = r"(?:/[^\s]*?)?(?=[.,;:!?)\]”\"']*(?:\s|$))"
_rule(r"(?<![\w.])(?:https?://)?(?:www\.)?dattts\.com" + _URL_PATH, "Đạt tê tê ét chấm com", re.I)
_rule(r"(?<![\w@])dattts(?!\w)", "Đạt tê tê ét", re.I)

# 2) URL: engine đọc "https://" thành "hát tê tê phê ét hai chấm gạch chéo gạch chéo" → bỏ tiền tố.
_rule(r"\bhttps?://(?:www\.)?", "", re.I)
_rule(r"(?<![\w.@])www\.(?=[\w-]+\.)", "", re.I)
#    Đường dẫn sau tên miền: engine đọc "/pricing" thành "trên pricing", "/a-b" thành "gạch chéo a gạch nối b"
#    → chỉ đọc tên miền.
_rule(r"(?<![\w.@/])([\w-]+(?:\.[\w-]+)*\.(?:com|vn|net|org|io|edu|gov|info|biz|me|app|dev|ai|co|tv)"
      r"(?:\.vn)?)/[^\s]*?(?=[.,;:!?)\]”\"']*(?:\s|$))", r"\1", re.I)

# 2b) Số điện thoại viết dấu chấm. engine: "028.3822.1234" → "không hai tám chấm ba tám…" → dùng khoảng trắng
_rule(r"(?<![\w.,])(0\d{2,3})\.(\d{3,4})\.(\d{3,4})(?![\w]|[.,]\d)", r"\1 \2 \3")

# 3) Quý. engine: "Q3/2026" → "quý ba hai không hai sáu"; "Q1" → "qui một"
_rule(r"\bQ([1-4])\s*[/\-]\s*((?:19|20)\d{2})\b", r"quý \1 năm \2")
_rule(r"\bQ([1-4])\s+((?:19|20)\d{2})\b", r"quý \1 năm \2")

# 4) Số hiệu văn bản pháp luật. engine: "15/2020/NĐ-CP" → "mười lăm trên hai nghìn ... trên nờ đê xê phê"
_rule(
    r"\b(\d{1,4})/((?:19|20)\d{2})/(" + _LEGAL_ALT + r")(?![\w-])",
    lambda m: f"{m.group(1)} năm {m.group(2)} {_LEGAL_CODES[m.group(3)]}",
)
_rule(r"(?<![\w-])(" + _LEGAL_ALT + r")(?![\w-])", lambda m: _LEGAL_CODES[m.group(1)])

# 5) Tiền tệ. engine: "USD" → "<en>u s d</en>", "$100" → "một trăm u s d"
_rule(r"(?<![\w$])(?:US)?\$\s?(\d[\d.,]*)(?:\s*(triệu|tỷ|tỉ|nghìn|ngàn))?",
      lambda m: f"{m.group(1)}{' ' + m.group(2) if m.group(2) else ''} đô la Mỹ")
_rule(r"(\d)\s?\$(?!\w)", r"\1 đô la Mỹ")
_rule(r"(?<!\w)USD(?!\w)", "đô la Mỹ")
#    engine: "VND"/"VNĐ" → "việt nam đồng" (dài, không tự nhiên) → "đồng"
_rule(r"(?<!\w)VN[DĐ](?!\w)", "đồng", re.I)
#    "150k-200k" → "150 đến 200 nghìn" (trước luật "k" để khoảng được nối đúng)
_rule(r"(?<![\w.,])(\d{1,3})\s?[kK]\s*[-–]\s*(\d{1,3})\s?[kK](?![\w])", r"\1 đến \2 nghìn")
#    engine: "150k" → "một trăm năm mươi ca". Viết hoa "4K" (độ phân giải) giữ nguyên khi < 10.
_rule(r"(?<![\w.,])(\d{1,3}(?:[.,]\d+)?)\s?k(?![\w])", r"\1 nghìn")
_rule(r"(?<![\w.,])(\d{2,3}(?:[.,]\d+)?)\s?K(?![\w])", r"\1 nghìn")

# 6) Khoảng số. engine: "Mất 5-7 ngày" → "mất ngày năm tháng bảy ngày"; "3-4h" → "ba đến bốnh"
_rule(r"(?<![\w/.,\-–])(\d{1,2})\s*[-–]\s*(\d{1,2})h(?![\w])", r"\1 đến \2 giờ")
_rule(r"(\d)(đ|đồng)\s*[-–]\s*(\d)", r"\1\2 đến \3")


# Từ đứng trước cho biết "a-b" là ngày-tháng (engine tự đọc "ngày a tháng b" khi có "ngày"/"mùng"/"lễ")
_DATE_CTX = re.compile(r"(?:ngày|mùng|mồng|lễ|dịp|tết|khánh|niệm|hôm|sáng|chiều|tối|trưa|đêm)\s*$", re.I)
_DATE_CTX_SELF = re.compile(r"(?:ngày|mùng|mồng|lễ)\s*$", re.I)  # engine đã đọc đúng, giữ nguyên
# Tỷ số trận đấu: engine đọc "thắng 1-2" là "một hai" → giữ nguyên
_SCORE_CTX = re.compile(r"(?:thắng|thua|hòa|hoà|số|cuộc|tỉ|tỷ)\s*$", re.I)
# "lễ 30-4 và 1-5": ngày-tháng nối tiếp ngày-tháng
_DATE_CHAIN = re.compile(
    r"(?:ngày|mùng|mồng|lễ|dịp|tết|khánh|niệm)\s+\d{1,2}\s*[-–/]\s*\d{1,2}\s*(?:,|và|hoặc|hay|hoặc là)\s*$",
    re.I)


def _range(m: re.Match) -> str:
    a, b = m.group(2), m.group(3)
    prefix = m.group(1) or ""
    before = m.string[max(0, m.start() - 48):m.start()] + prefix
    is_date_shape = 1 <= int(a) <= 31 and 1 <= int(b) <= 12
    if is_date_shape and (_DATE_CTX.search(prefix) or _DATE_CHAIN.search(before)):
        if _DATE_CTX_SELF.search(prefix):
            return m.group(0)
        # "dịp 2-9", "và 1-5" → "dịp ngày 2-9": engine đọc "ngày hai tháng chín"
        return f"{prefix}ngày {a}-{b}"
    if _SCORE_CTX.search(prefix):
        return m.group(0)
    if int(a) >= int(b):
        return m.group(0)
    return f"{prefix}{a} đến {b}"


_rule(r"((?:\S+\s+)?)(?<![\w/.,\-–])(\d{1,3})\s*[-–]\s*(\d{1,3})(?![\w/.,\-–]\d|\w|/|-)", _range)

# 6b) Số âm thập phân. engine: "-2,5 tỷ" → "hai phẩy năm tỷ" (mất dấu âm; số nguyên và % thì đọc đúng)
_rule(r"(?:(?<=[\s(])(?<!\d\s)|^)[-−](\d+,\d+)(?!\d|\s*%)", r"âm \1")

# 7) Hệ số nhân. engine: "x2" → "ích hai"; "2x" → "hai ích"; kích thước "3x4" giữ "nhân"
_rule(r"(?<![\w.,])(\d+)\s?[x×]\s?(\d+)(?![\d.,]\d)", r"\1 nhân \2")
_rule(r"(?<![\w.,])[x×](\d+)(?![\w.,])", r"gấp \1")
_rule(r"(?<![\w.,])(\d+)[x×](?![\w])", r"gấp \1")

# 8) Học hàm, danh xưng có dấu chấm (gây ngắt câu giữa tên). Áp dụng ở apply_builtin_rules.
# 9) "v.v." → engine đọc "vân vân." kèm ngắt câu → "vân vân"
_rule(r"(?<!\w)v\.v\.(?:\.\.)?", "vân vân", re.I)

# 10) "TP.HN", "TP. Hồ Chí Minh", "Tp.HCM" → engine chèn ngắt câu hoặc đọc "tp" → "thành phố ".
#     "TP." đứng cuối câu ("sống ở TP. Ông ấy…") là hết câu, không nối sang câu sau.
_rule(r"(?<!\w)TP\.(?!HCM\b)(?=[A-ZÀ-ỸĐ])", "thành phố ")
_rule(r"(?<!\w)Tp\.(?=[A-ZÀ-ỸĐ])", "thành phố ")
_rule(r"(?<!\w)(?:TP\.(?!\s+HCM\b)|Tp\.)(?=\s+[A-ZÀ-ỸĐ])",
      lambda m: "thành phố" if _tp_continues(m.string[m.end():]) else "thành phố.")
_rule(r"(?<!\w)T[Pp]\.(?=\s*$)", "thành phố.")
_rule(r"(?<!\w)Tp(?=\s+[A-ZÀ-ỸĐ])", "thành phố")
_rule(r"(?<!\w)thành phố HN(?!\w)", "thành phố Hà Nội")

# 10b) Địa chỉ viết tắt có khoảng trắng. engine: "P. Bến Nghé" → "phê. bến nghé", "Q. 3" → "qui. ba",
#      "H. Củ Chi" → "hát." (+ ngắt câu). Chỉ áp dụng sau dấu phẩy / đầu câu / "tại", "ở".
_ADDR = {"P": "phường", "Q": "quận", "H": "huyện", "TX": "thị xã", "TT": "thị trấn", "X": "xã"}
_rule(r"(^|,\s*|\b(?:tại|ở|thuộc)\s+)(TX|TT|P|Q|H|X)\.\s+(?=[\dA-ZÀ-ỸĐ])",
      lambda m: f"{m.group(1)}{_ADDR[m.group(2)]} ")

# 11) Số thập phân viết dấu chấm trước đơn vị: "1.5 triệu" → engine đọc "một chấm năm triệu"
_rule(r"(?<![\w.,])(\d+)\.(\d{1,2})(?=\s*(?:triệu|tỷ|tỉ|nghìn|ngàn|lần|điểm|%|/\d|°|độ|giờ|phút|giây|tuần|tháng|năm|"
      r"tuổi|ngày|km|kg|cm|mm|ml|mg|m[²³23]?\b|g\b|l\b|lít|tấn|tạ|yến|ha\b|inch|[GMKT]B\b|USD|đô|đồng|người|sao))", r"\1,\2")

# 11b) Đề mục danh sách "1. …", "A. …", "II. …" ở đầu câu → "1, …" (engine đọc "một, …" thay vì "một." rồi ngắt)
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "XI": 11, "XII": 12}
_rule(r"^((?:\d{1,2}|[A-ZĐ]|[IVX]{2,4}))\.\s+(?=\S)", lambda m: f"{_ROMAN.get(m.group(1), m.group(1))}, ")

# 12) "No.1" → engine "no, một" → "số 1"
_rule(r"(?<!\w)No\.\s?(\d+)", r"số \1")


def apply_builtin_rules(text: str) -> str:
    """Vá những chỗ engine đọc kém. Không chạm vào số/ngày/tiền mà engine đã đọc tốt."""
    if not text:
        return text
    t = text
    for abbr, full in _TITLES:
        t = re.sub(r"(?<![\w.])" + re.escape(abbr) + r"\s*", full + " ", t)
    for pat, repl in _RULES:
        t = pat.sub(repl, t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


# ───────────────────────────── split_sentences ─────────────────────────────

# Từ viết tắt có dấu chấm KHÔNG kết thúc câu (so sánh không phân biệt hoa thường).
_ABBREV = {
    "tp", "tt", "ts", "pgs", "gs", "ths", "bs", "ks", "cn", "ncs", "nsưt", "nsnd",
    "mr", "mrs", "ms", "dr", "prof", "st", "jr", "sr", "vs", "etc", "no", "tel", "đt",
    "q", "p", "tx", "tr", "ph", "kh", "bsckii", "bscki",
    "pgs.ts", "gs.ts", "ts.bs", "ths.bs", "e.g", "i.e", "a.m", "p.m",
}
_CLOSERS = "\"'”’»)]}"
_OPENERS = "\"'“‘«([{"
_END_PUNCT = ".!?…"


def _prev_token(s: str, end: int) -> str:
    """Token (không gồm khoảng trắng) kết thúc ngay trước vị trí ``end`` (là vị trí dấu chấm)."""
    i = end
    while i > 0 and not s[i - 1].isspace() and s[i - 1] not in _OPENERS:
        i -= 1
    return s[i:end]


_LIST_MARKER = re.compile(r"(?:\d{1,2}|[A-Za-zĐđ]|[IVX]{2,4})")
# ": 1. Uống đủ nước" → ngắt sau dấu hai chấm để đề mục thuộc về câu sau
_COLON_BEFORE_MARKER = re.compile(r":\s+(?=(?:\d{1,2}|[A-ZĐ]|[IVX]{2,4})\.\s+\S)")
# Viết tắt địa chỉ chỉ "không hết câu" khi đứng sau dấu phẩy: "…, H. Củ Chi"
_ADDR_ABBREV = {"h", "x"}


def _is_boundary(p: str, start: int, stop: int, sent_start: int = 0) -> bool:
    """Chuỗi dấu kết câu p[start:stop] (đã gồm dấu đóng ngoặc) có phải ranh giới câu không."""
    punct = p[start:stop].rstrip(_CLOSERS)
    nxt_i = stop
    while nxt_i < len(p) and p[nxt_i] == " ":
        nxt_i += 1
    if nxt_i >= len(p):
        return True
    if nxt_i == stop:  # không có khoảng trắng sau dấu: "1.000", "TP.HCM", "a.b"
        return False
    nxt = p[nxt_i]
    if "?" in punct or "!" in punct:
        return True
    starts_upper = nxt.isupper() or nxt.isdigit() or nxt in _OPENERS or nxt in "-–—•*"
    if "…" in punct or ".." in punct:
        return starts_upper
    # Một dấu chấm đơn
    tok = _prev_token(p, start)
    low = tok.lower()
    # Đề mục danh sách "1. …", "A. …", "II. …" ở đầu câu/đoạn: không tách thành câu riêng
    if punct == "." and _LIST_MARKER.fullmatch(tok) and not p[sent_start:start - len(tok)].strip():
        return False
    if low == "v.v" or low.endswith(".v.v"):
        return starts_upper
    if low == "tp":  # "TP. Hồ Chí Minh" nối tiếp; "sống ở TP. Ông ấy…" là hết câu
        return not _tp_continues(p[nxt_i:])
    if low in _ADDR_ABBREV and re.search(r",\s*$", p[:start - len(tok)]):
        return False
    if low in _ABBREV or low.rstrip(".") in _ABBREV:
        return False
    return True


def _sentences_in_paragraph(p: str) -> list[str]:
    out: list[str] = []
    i, n, last = 0, len(p), 0
    while i < n:
        ch = p[i]
        if ch == ":" and _COLON_BEFORE_MARKER.match(p, i):
            s = p[last:i + 1].strip()
            if s:
                out.append(s)
            last = i + 1
            i += 1
            continue
        if ch in _END_PUNCT:
            j = i
            while j < n and (p[j] in _END_PUNCT):
                j += 1
            while j < n and p[j] in _CLOSERS:
                j += 1
            sent_start = last
            while sent_start < n and p[sent_start].isspace():
                sent_start += 1
            if _is_boundary(p, i, j, sent_start):
                s = p[last:j].strip()
                if s:
                    out.append(s)
                last = j
            i = j
            continue
        i += 1
    tail = p[last:].strip()
    if tail:
        out.append(tail)
    return out


_CLAUSE_BREAK = re.compile(r"[,;:—–](?=\s)|\s[-–—]\s")


def _split_long(s: str, max_chars: int) -> list[str]:
    """Cắt câu dài thành các vế ≤ max_chars, ưu tiên sau dấu ; , : gần max_chars nhất; không cắt giữa từ."""
    parts: list[str] = []
    rest = s
    while len(rest) > max_chars:
        window = rest[: max_chars + 1]
        cut = -1
        min_len = max(12, max_chars // 4)
        # 1) dấu chấm phẩy / hai chấm, 2) dấu phẩy / gạch, 3) khoảng trắng
        for pat in (r"[;:](?=\s)", r"[,—–](?=\s)|\s[-–—](?=\s)"):
            cands = [m.end() for m in re.finditer(pat, window) if m.end() >= min_len]
            if cands:
                cut = cands[-1]
                break
        if cut < 0:
            # Khoảng trắng gần giới hạn nhất, nhưng không nằm giữa hai chữ số ("100 000 000", "0912 345 678")
            w = window.rstrip()
            spaces = [m.start() for m in re.finditer(r" ", w)
                      if m.start() >= min_len and not (w[m.start() - 1].isdigit() and w[m.start() + 1:m.start() + 2].isdigit())]
            if spaces:
                cut = spaces[-1]
        if cut < 0:  # một "từ" siêu dài (URL…) — cắt ở khoảng trắng đầu tiên sau đó
            sp = rest.find(" ", max_chars)
            cut = sp if sp > 0 else len(rest)
        head, rest = rest[:cut].strip(), rest[cut:].strip()
        if head:
            parts.append(head)
    if rest:
        parts.append(rest)
    return parts


def split_sentences(text: str, max_chars: int = 220) -> list[tuple[str, str]]:
    """Tách văn bản thành [(câu, kiểu ngắt sau câu)] với kiểu ∈ {"sentence","paragraph","clause"}.

    Mỗi xuống dòng là một ngắt đoạn. Câu dài hơn ``max_chars`` được cắt thành các vế
    ("clause") tại dấu câu gần giới hạn nhất, không bao giờ cắt giữa từ.
    """
    text = clean_text(text)
    if not text:
        return []
    max_chars = max(20, int(max_chars))
    out: list[tuple[str, str]] = []
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    for p in paragraphs:
        sents = _sentences_in_paragraph(p)
        for si, s in enumerate(sents):
            last_kind = "paragraph" if si == len(sents) - 1 else "sentence"
            pieces = _split_long(s, max_chars) if len(s) > max_chars else [s]
            for pi, piece in enumerate(pieces):
                out.append((piece, last_kind if pi == len(pieces) - 1 else "clause"))
    return out
