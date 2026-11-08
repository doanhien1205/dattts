import pytest

from dattts.textproc import (
    Segment,
    apply_builtin_rules,
    apply_dictionary,
    clean_text,
    count_chars,
    parse_script,
    split_sentences,
)


def texts(text, **kw):
    return [s for s, _ in split_sentences(text, **kw)]


# ───────────── clean_text / count_chars ─────────────

def test_clean_text_nfc_and_spaces():
    decomposed = "Tiếng Việt"  # dấu tổ hợp
    assert clean_text(decomposed) == "Tiếng Việt"
    assert clean_text("  Xin   chào\t\tbạn  nhé  ") == "Xin chào bạn nhé"


def test_clean_text_keeps_paragraphs_and_strips_controls():
    t = "Đoạn một.\r\n\r\n\r\n\r\nĐoạn hai.\x00\x07 Hết​."
    assert clean_text(t) == "Đoạn một.\n\nĐoạn hai. Hết."
    assert clean_text("a  \n  b") == "a\nb"
    assert clean_text("") == ""


def test_count_chars():
    assert count_chars("  Xin   chào ") == len("Xin chào")
    assert count_chars("Tiếng") == 5


# ───────────── parse_script ─────────────

def test_parse_script_multi_voice():
    segs = parse_script("Mở đầu. [Trúc Ly] Chào anh. [Thiện Minh] Chào em.\n[truc-ly]  Hẹn gặp lại.")
    assert segs == [
        Segment(None, "Mở đầu."),
        Segment("Trúc Ly", "Chào anh."),
        Segment("Thiện Minh", "Chào em."),
        Segment("truc-ly", "Hẹn gặp lại."),
    ]


def test_parse_script_no_tags_and_empty_segments():
    assert parse_script("Chỉ một giọng thôi.") == [Segment(None, "Chỉ một giọng thôi.")]
    assert parse_script("[Trúc Ly][Thiện Minh] Xin chào") == [Segment("Thiện Minh", "Xin chào")]
    assert parse_script("[Trúc Ly]   ") == []
    assert parse_script("") == []


def test_parse_script_keeps_paragraphs_inside_segment():
    segs = parse_script("[Mai Anh] Dòng một.\nDòng hai.")
    assert segs == [Segment("Mai Anh", "Dòng một.\nDòng hai.")]


# ───────────── apply_dictionary ─────────────

def test_dictionary_whole_word_case_insensitive():
    entries = [{"from": "AI", "to": "ây ai"}]
    assert apply_dictionary("Ai nói về AI và ai đó?", entries) == "ây ai nói về ây ai và ây ai đó?"
    # không khớp giữa từ
    assert apply_dictionary("TRAIN và chain", entries) == "TRAIN và chain"


def test_dictionary_longest_first_and_single_pass():
    entries = [
        {"from": "Hà Nội", "to": "thủ đô"},
        {"from": "Hà Nội mới", "to": "Hà Nội tươi mới"},
        {"from": "thủ đô", "to": "SAI"},
    ]
    assert apply_dictionary("Hà Nội mới và Hà Nội.", entries) == "Hà Nội tươi mới và thủ đô."


def test_dictionary_vietnamese_word_boundaries_and_punct():
    entries = [{"from": "ma", "to": "MA"}, {"from": "TP.HCM", "to": "Sài Gòn"}]
    assert apply_dictionary("ma mà má mã ma.", entries) == "MA mà má mã MA."
    assert apply_dictionary("Về TP.HCM nhé", entries) == "Về Sài Gòn nhé"


def test_dictionary_ignores_bad_entries():
    assert apply_dictionary("abc", [{"from": "", "to": "x"}, "rác", {"to": "y"}]) == "abc"
    assert apply_dictionary("abc", []) == "abc"


# ───────────── apply_builtin_rules ─────────────
# Mỗi trường hợp ứng với một lỗi thật của bộ chuẩn hoá VieNeu 3.8.3 (đầu ra engine ghi trong chú thích).

@pytest.mark.parametrize("src, expected", [
    # "quý ba hai không hai sáu"
    ("Doanh thu Q3/2026 tăng.", "Doanh thu quý 3 năm 2026 tăng."),
    ("Q1-2025 và Q4 2024", "quý 1 năm 2025 và quý 4 năm 2024"),
    # "dat tê tê ét chấm com"
    ("Truy cập dattts.com ngay.", "Truy cập Đạt tê tê ét chấm com ngay."),
    ("Xem https://www.dattts.com/ nhé", "Xem Đạt tê tê ét chấm com nhé"),
    # "dat <en>t t s</en>"
    ("DatTTS là gì?", "Đạt tê tê ét là gì?"),
    # "nờ đê xê phê"
    ("Theo NĐ-CP số 15.", "Theo nghị định Chính phủ số 15."),
    # "mười lăm trên hai nghìn ... trên nờ đê xê phê"
    ("Theo 15/2020/NĐ-CP.", "Theo 15 năm 2020 nghị định Chính phủ."),
    # "qui đê tê tg", "tê tê bê tê xê"
    ("QĐ-TTg và TT-BTC", "quyết định Thủ tướng và thông tư Bộ Tài chính"),
    # "<en>u s d</en>"
    ("Giá 100 USD.", "Giá 100 đô la Mỹ."),
    ("Giá $100 và 5$.", "Giá 100 đô la Mỹ và 5 đô la Mỹ."),
    ("US$2 triệu", "2 triệu đô la Mỹ"),
    # "việt nam đồng"
    ("Giá 100 VND và 200 VNĐ.", "Giá 100 đồng và 200 đồng."),
    # "hát tê tê phê ét hai chấm gạch chéo gạch chéo ..."
    ("Xem https://example.vn/docs nhé", "Xem example.vn nhé"),
    # "mất ngày năm tháng bảy ngày"
    ("Mất 5-7 ngày.", "Mất 5 đến 7 ngày."),
    ("Thứ 2–6 làm việc", "Thứ 2 đến 6 làm việc"),
    # "ba đến bốnh"
    ("Khoảng 3-4h.", "Khoảng 3 đến 4 giờ."),
    # "... nghìn đồng, một trăm ..." (mất chữ "đến")
    ("Từ 99.000đ - 199.000đ.", "Từ 99.000đ đến 199.000đ."),
    # "ích hai", "hai ích"
    ("Nhanh x2 lần, 2x", "Nhanh gấp 2 lần, gấp 2"),
    ("Phòng 3x4m", "Phòng 3 nhân 4m"),
    # "một trăm năm mươi ca"
    ("Vé 150k và 20K", "Vé 150 nghìn và 20 nghìn"),
    # "phó giáo sư tiến sĩ. lê" (ngắt câu giữa tên)
    ("PGS.TS. Lê Văn B.", "phó giáo sư tiến sĩ Lê Văn B."),
    ("TS. Lê nói", "tiến sĩ Lê nói"),
    # "mr, bean"
    ("Mr. Bean và Ms. Lan", "mít-tơ Bean và mít Lan"),
    # "vân vân. nhé"
    ("Cam, quýt v.v. nhé", "Cam, quýt vân vân nhé"),
    # "thành phố. hà nội", "tp, hồ chí minh"
    ("TP.HN và Tp.HCM", "thành phố Hà Nội và thành phố HCM"),
    ("TP. Hồ Chí Minh", "thành phố Hồ Chí Minh"),
    # "một chấm năm triệu"
    ("Giá 1.5 triệu, điểm 8.5/10", "Giá 1,5 triệu, điểm 8,5/10"),
    # "no, một"
    ("No.1 Việt Nam", "số 1 Việt Nam"),
])
def test_builtin_rules_fix_engine_failures(src, expected):
    assert apply_builtin_rules(src) == expected


@pytest.mark.parametrize("src", [
    # Engine đã đọc tốt → luật KHÔNG được đụng vào
    "Ngày 2/9/1945 và ngày 2-9 là lễ.",
    "Giá 1.000.000 đồng, tăng 15%, còn 3,5%.",
    "Giai đoạn 2020-2025.",
    "Gọi 0912-345-678 hoặc +84 912 345 678.",
    "Trang 1.000-2.000.",
    "UBND, THPT, CLB, ĐH, HĐND, BHXH.",
    "Nhà ở Q.1, P.5, TP.HCM.",
    "Lúc 7h30 và 7:30.",
    "Covid-19, mã ABC-123, iPhone 15.",
    "Ảnh 4K, mạng 5G.",
    "Mục 1.2 của bản v1.2.3.",
    "Tỷ số 3-1.",
    "Tôi & bạn.",
])
def test_builtin_rules_leave_good_text_alone(src):
    assert apply_builtin_rules(src) == src


def test_builtin_rules_email_keeps_local_part():
    assert apply_builtin_rules("lienhe@dattts.com") == "lienhe@Đạt tê tê ét chấm com"


# ───────────── split_sentences ─────────────

def test_split_basic_and_kinds():
    out = split_sentences("Xin chào các bạn. Hôm nay trời đẹp quá! Bạn có khỏe không?\nĐoạn mới bắt đầu.")
    assert out == [
        ("Xin chào các bạn.", "sentence"),
        ("Hôm nay trời đẹp quá!", "sentence"),
        ("Bạn có khỏe không?", "paragraph"),
        ("Đoạn mới bắt đầu.", "paragraph"),
    ]


def test_split_numbers_not_broken():
    t = "Giá là 1.000.000 đồng, lãi 3,5% mỗi năm. Dân số khoảng 100.000 người."
    assert texts(t) == ["Giá là 1.000.000 đồng, lãi 3,5% mỗi năm.", "Dân số khoảng 100.000 người."]


def test_split_abbreviations():
    t = ("PGS.TS. Nguyễn Văn An làm việc tại TP.HCM. Ông có nhiều đề tài, sách, báo v.v. rất hay. "
         "TS. Lê Thị Bình cũng vậy. Mr. Smith đến từ Mỹ.")
    assert texts(t) == [
        "PGS.TS. Nguyễn Văn An làm việc tại TP.HCM.",
        "Ông có nhiều đề tài, sách, báo v.v. rất hay.",
        "TS. Lê Thị Bình cũng vậy.",
        "Mr. Smith đến từ Mỹ.",
    ]


def test_split_vv_at_sentence_end():
    t = "Chợ bán cam, quýt, bưởi v.v. Hôm sau lại đông."
    assert texts(t) == ["Chợ bán cam, quýt, bưởi v.v.", "Hôm sau lại đông."]


def test_split_address_abbreviations():
    t = "Văn phòng ở 12 Lê Lợi, P. Bến Nghé, Q. 1, TP. Hồ Chí Minh. Mời bạn ghé chơi."
    assert texts(t) == ["Văn phòng ở 12 Lê Lợi, P. Bến Nghé, Q. 1, TP. Hồ Chí Minh.", "Mời bạn ghé chơi."]


def test_split_ellipsis():
    t = "Anh ấy ngập ngừng... rồi bỏ đi. Thế là hết… Chuyện cũ qua rồi."
    assert texts(t) == ["Anh ấy ngập ngừng... rồi bỏ đi.", "Thế là hết…", "Chuyện cũ qua rồi."]


def test_split_quotes():
    t = 'Cô ấy nói: "Em sẽ về." Rồi cô đi. Anh hỏi “Thật không?” Cô gật đầu.'
    assert texts(t) == ['Cô ấy nói: "Em sẽ về."', "Rồi cô đi.", "Anh hỏi “Thật không?”", "Cô gật đầu."]


def test_split_question_exclaim_combos():
    assert texts("Thật sao?! Không thể nào!!! Ừ.") == ["Thật sao?!", "Không thể nào!!!", "Ừ."]


def test_split_newline_is_paragraph_even_without_punct():
    out = split_sentences("Tiêu đề bài viết\nNội dung chính ở đây.\n\nKết luận")
    assert out == [("Tiêu đề bài viết", "paragraph"), ("Nội dung chính ở đây.", "paragraph"),
                   ("Kết luận", "paragraph")]


def test_split_domain_and_decimal_inside_sentence():
    t = "Truy cập dattts.com để thử. Điểm trung bình là 8.5 trên 10."
    assert texts(t) == ["Truy cập dattts.com để thử.", "Điểm trung bình là 8.5 trên 10."]


def test_split_long_sentence_at_commas_near_limit():
    t = ("Việt Nam có nhiều danh lam thắng cảnh nổi tiếng như vịnh Hạ Long, phố cổ Hội An, cố đô Huế, "
         "động Phong Nha, đảo Phú Quốc, ruộng bậc thang Mù Cang Chải, cao nguyên đá Đồng Văn, "
         "đồng bằng sông Cửu Long và rất nhiều nơi khác đang chờ bạn khám phá.")
    out = split_sentences(t, max_chars=80)
    assert all(len(s) <= 80 for s, _ in out)
    assert [k for _, k in out][:-1] == ["clause"] * (len(out) - 1)
    assert out[-1][1] == "paragraph"
    assert all(s.endswith(",") for s, _ in out[:-1])
    assert " ".join(s for s, _ in out) == t


def test_split_long_sentence_prefers_semicolon():
    t = ("Buổi sáng chúng tôi đi chợ mua rau, thịt, cá; buổi chiều cả nhà cùng nấu ăn, dọn dẹp, "
         "trò chuyện vui vẻ bên nhau.")
    out = split_sentences(t, max_chars=70)
    assert out[0][0] == "Buổi sáng chúng tôi đi chợ mua rau, thịt, cá;"


def test_split_long_without_punct_never_mid_word():
    words = ["nghiêng", "ngả", "chuyện", "thương", "trường", "khuya"] * 30
    t = " ".join(words)
    out = split_sentences(t, max_chars=50)
    assert len(out) > 3
    for s, _ in out:
        assert len(s) <= 50
        for w in s.split():
            assert w in words
    assert " ".join(s for s, _ in out) == t


def test_split_huge_token_kept_whole():
    url = "https://example.com/" + "a" * 300
    out = split_sentences(f"Xem {url} nhé", max_chars=50)
    assert any(url in s for s, _ in out)


def test_split_empty_and_whitespace():
    assert split_sentences("") == []
    assert split_sentences("  \n\n  ") == []


def test_split_preserves_all_text():
    t = ("Ngày 2/9/1945, Chủ tịch Hồ Chí Minh đọc Tuyên ngôn Độc lập tại Quảng trường Ba Đình, Hà Nội. "
         "Từ đó, ngày 2-9 trở thành ngày Quốc khánh. GS. Trần Văn Giàu từng viết nhiều về sự kiện này!\n"
         "Năm 2026, GDP tăng 7,5%; thu nhập bình quân đạt 5.000 USD/người/năm.")
    out = texts(t)
    assert len(out) == 4
    assert "".join(t.split()) == "".join("".join(out).split())
