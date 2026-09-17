"""Hermetic tests for wechat_cli.core.media and image media resolution."""

import hashlib
import os

import pytest
from Crypto.Cipher import AES

from wechat_cli.core import media
from wechat_cli.core import messages
from wechat_cli.core.media import (
    decrypt_wechat_image,
    detect_image_extension,
    detect_wechat_image_xor_key,
    _is_wechat_v2_dat,
    _strict_remove_pkcs7_padding,
    _unwrap_wxgf,
)

FAKE_JPG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    + bytes(range(200))
)
FAKE_PNG = (
    b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0d" + b"IHDR" + bytes(range(80))
)
V2_MAGIC = b"\x07\x08V2\x08\x07"


def xor_crypt(data, key):
    return bytes(b ^ key for b in data)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


# ---- raw/plaintext images ----

def test_raw_jpg_passthrough(tmp_path):
    src = write(tmp_path / "a.dat", FAKE_JPG)
    out, ext = decrypt_wechat_image(src, output_dir=str(tmp_path / "out"))
    assert ext == "jpg"
    assert out.endswith(".jpg")
    assert open(out, "rb").read() == FAKE_JPG


def test_raw_image_source_never_modified(tmp_path):
    src = write(tmp_path / "a.dat", FAKE_JPG)
    before = open(src, "rb").read()
    decrypt_wechat_image(src, output_dir=str(tmp_path / "out"))
    assert open(src, "rb").read() == before


def test_output_cache_stable(tmp_path):
    src = write(tmp_path / "a.dat", FAKE_JPG)
    out1, _ = decrypt_wechat_image(src, output_dir=str(tmp_path / "out"))
    out2, _ = decrypt_wechat_image(src, output_dir=str(tmp_path / "out"))
    assert out1 == out2


# ---- XOR legacy .dat ----

def test_detect_xor_key_jpg(tmp_path):
    src = write(tmp_path / "enc.dat", xor_crypt(FAKE_JPG, 0x5A))
    key, ext = detect_wechat_image_xor_key(src)
    assert key == 0x5A
    assert ext == "jpg"


def test_detect_xor_key_png(tmp_path):
    src = write(tmp_path / "enc.dat", xor_crypt(FAKE_PNG, 0xAB))
    key, ext = detect_wechat_image_xor_key(src)
    assert key == 0xAB
    assert ext == "png"


def test_detect_xor_key_rejects_garbage(tmp_path):
    src = write(tmp_path / "g.dat", os.urandom(128))
    assert detect_wechat_image_xor_key(src) == (None, None)


def test_decrypt_xor_dat_roundtrip(tmp_path):
    src = write(tmp_path / "enc.dat", xor_crypt(FAKE_JPG, 0x33))
    out, ext = decrypt_wechat_image(src, output_dir=str(tmp_path / "out"))
    assert ext == "jpg"
    assert open(out, "rb").read() == FAKE_JPG


# ---- failure fallbacks ----

def test_missing_file(tmp_path):
    assert decrypt_wechat_image(str(tmp_path / "nope.dat"),
                                output_dir=str(tmp_path / "out")) == (None, None)


def test_garbage_file(tmp_path):
    src = write(tmp_path / "g.dat", os.urandom(256))
    assert decrypt_wechat_image(src, output_dir=str(tmp_path / "out")) == (None, None)


def test_v2_magic_without_keys_fails(tmp_path):
    # V2 header present but no kvcomm dir exists under tmp_path → no codes → fail
    src = write(tmp_path / "xwechat_files/wxid_x/msg/pic.dat",
                V2_MAGIC + os.urandom(300))
    assert decrypt_wechat_image(src, output_dir=str(tmp_path / "out")) == (None, None)


# ---- V2 .dat ----

def _build_v2_dat(plaintext, code, wxid, aes_size, xor_size):
    aes_key = hashlib.md5((str(code) + wxid).encode()).hexdigest()[:16]
    xor_key = code & 0xFF
    remainder = aes_size % 16
    aligned = aes_size + (16 - remainder)
    aes_plain = plaintext[:aes_size]
    pad = 16 - (len(aes_plain) % 16)
    aes_ct = AES.new(aes_key.encode("ascii")[:16], AES.MODE_ECB).encrypt(
        aes_plain + bytes([pad]) * pad
    )
    assert len(aes_ct) == aligned
    rest = plaintext[aes_size:]
    if xor_size:
        raw = rest[: len(rest) - xor_size]
        tail = xor_crypt(rest[len(rest) - xor_size:], xor_key)
    else:
        raw, tail = rest, b""
    header = (
        V2_MAGIC
        + aes_size.to_bytes(4, "little")
        + xor_size.to_bytes(4, "little")
        + b"\x00"
    )
    assert len(header) == 0x0F
    return header + aes_ct + raw + tail


def _v2_fixture(tmp_path, code=12345, wxid="wxid_test123"):
    """Place a crafted V2 .dat + matching kvcomm code file under tmp_path."""
    kv = tmp_path / "app_data" / "net" / "kvcomm"
    kv.mkdir(parents=True)
    (kv / f"key_{code}_abc.statistic").write_bytes(b"x")
    dat = (
        tmp_path / "xwechat_files" / wxid / "msg" / "attach"
        / "deadbeef" / "2026-09" / "Img" / "pic.dat"
    )
    return kv, dat


def test_v2_dat_roundtrip(tmp_path):
    code, wxid = 12345, "wxid_test123"
    kv, dat = _v2_fixture(tmp_path, code, wxid)
    write(dat, _build_v2_dat(FAKE_JPG, code, wxid, aes_size=16, xor_size=8))
    out, ext = decrypt_wechat_image(str(dat), output_dir=str(tmp_path / "out"))
    assert ext == "jpg"
    assert open(out, "rb").read() == FAKE_JPG


def test_v2_dat_wrong_code_fails(tmp_path):
    # kvcomm offers a code that doesn't match the file's encryption
    kv, dat = _v2_fixture(tmp_path, code=99999)
    write(dat, _build_v2_dat(FAKE_JPG, 12345, "wxid_test123", 16, 8))
    assert decrypt_wechat_image(str(dat),
                                output_dir=str(tmp_path / "out")) == (None, None)


def test_v2_dat_device_suffix_wxid(tmp_path):
    # Account dir has a device suffix (wxid_x_a0e4); key derived from stripped name
    code, real_wxid = 777, "wxid_dev1"
    kv = tmp_path / "app_data" / "net" / "kvcomm"
    kv.mkdir(parents=True)
    (kv / f"key_{code}_z.statistic").write_bytes(b"x")
    dat = (tmp_path / "xwechat_files" / "wxid_dev1_a0e4" / "msg" / "attach"
           / "h" / "2026-09" / "Img" / "p.dat")
    write(dat, _build_v2_dat(FAKE_JPG, code, real_wxid, 16, 0))
    out, ext = decrypt_wechat_image(str(dat), output_dir=str(tmp_path / "out"))
    assert ext == "jpg"


def test_is_wechat_v2_dat():
    assert _is_wechat_v2_dat(V2_MAGIC + bytes(30))
    assert not _is_wechat_v2_dat(b"\xff\xd8\xff" + bytes(30))
    assert not _is_wechat_v2_dat(V2_MAGIC)  # too short


# ---- wxgf unwrap / pkcs7 ----

def test_unwrap_wxgf_embedded_jpg():
    wrapped = b"wxgf" + os.urandom(20) + FAKE_JPG
    assert _unwrap_wxgf(wrapped) == FAKE_JPG


def test_unwrap_wxgf_passthrough_non_wxgf():
    assert _unwrap_wxgf(FAKE_JPG) == FAKE_JPG


def test_pkcs7_strict():
    data = b"ABC" + b"\x03" * 3
    assert _strict_remove_pkcs7_padding(data) == b"ABC"
    with pytest.raises(ValueError):
        _strict_remove_pkcs7_padding(b"ABC" + b"\x05" * 3)
    with pytest.raises(ValueError):
        _strict_remove_pkcs7_padding(b"")
    with pytest.raises(ValueError):
        _strict_remove_pkcs7_padding(b"A" * 16 + b"\x11")


def test_detect_image_extension():
    assert detect_image_extension(FAKE_JPG) == "jpg"
    assert detect_image_extension(FAKE_PNG) == "png"
    assert detect_image_extension(b"GIF89a" + bytes(10)) == "gif"
    assert detect_image_extension(os.urandom(32)) is None


# ---- messages.py integration ----

CHAT_USER = "chat_user"


def _img_dir(tmp_path, month="2026-09"):
    base = tmp_path / "wx"
    db_dir = base / "db_storage"
    attach = (
        base / "msg" / "attach" / hashlib.md5(CHAT_USER.encode()).hexdigest()
        / month / "Img"
    )
    attach.mkdir(parents=True)
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir, attach


IMG_XML = '<msg><img hdlength="0" length="0" cdnthumblength="0"/></msg>'
SEP_TS = 1788192000  # 2026-09-01 12:00 UTC-ish


def _fmt(db_dir, content=IMG_XML, ts=SEP_TS, resolve_media=True):
    return messages._format_message_text(
        7, 3, content, False, CHAT_USER, "chat", {},
        lambda u: u, db_dir=str(db_dir), create_time_ts=ts,
        resolve_media=resolve_media,
    )


def test_format_image_no_media_flag(tmp_path):
    db_dir, attach = _img_dir(tmp_path)
    write(attach / "x.dat", xor_crypt(FAKE_JPG, 0x11))
    _, text = _fmt(db_dir, resolve_media=False)
    assert text == "[图片] (local_id=7)"


def test_format_image_decrypts_dat(tmp_path, monkeypatch):
    db_dir, attach = _img_dir(tmp_path)
    write(attach / "x.dat", xor_crypt(FAKE_JPG, 0x11))
    out_dir = tmp_path / "mout"
    real = media.decrypt_wechat_image
    monkeypatch.setattr(
        messages, "decrypt_wechat_image",
        lambda p, output_dir=None: real(p, output_dir=str(out_dir)),
    )
    _, text = _fmt(db_dir)
    assert text.startswith("[图片] ")
    path = text.split(" ", 1)[1].split(" (")[0]
    assert path.endswith(".jpg")
    assert open(path, "rb").read() == FAKE_JPG


def test_format_image_undecryptable_note(tmp_path, monkeypatch):
    db_dir, attach = _img_dir(tmp_path)
    write(attach / "x.dat", os.urandom(200))
    out_dir = tmp_path / "mout"
    real = media.decrypt_wechat_image
    monkeypatch.setattr(
        messages, "decrypt_wechat_image",
        lambda p, output_dir=None: real(p, output_dir=str(out_dir)),
    )
    _, text = _fmt(db_dir)
    assert "(无法解密)" in text


def test_format_image_thumbnail_fallback(tmp_path, monkeypatch):
    db_dir, attach = _img_dir(tmp_path)
    # Full-size file is undecryptable; thumbnail decrypts fine.
    write(attach / "x.dat", os.urandom(200))
    write(attach / "x_t.dat", xor_crypt(FAKE_JPG, 0x77))
    out_dir = tmp_path / "mout"
    real = media.decrypt_wechat_image
    monkeypatch.setattr(
        messages, "decrypt_wechat_image",
        lambda p, output_dir=None: real(p, output_dir=str(out_dir)),
    )
    _, text = _fmt(db_dir)
    # Resolver promotes/picks candidates; decrypt fallback marks thumbnail use.
    assert "[图片] " in text and "无法解密" not in text


def test_promote_and_thumbnail_variants(tmp_path):
    d = tmp_path
    write(d / "a_t.dat", b"x")
    write(d / "a_h.dat", b"x")
    write(d / "b.dat", b"x")
    write(d / "b_t.dat", b"x")
    assert messages._promote_image_variant(str(d / "a_t.dat")).endswith("a_h.dat")
    assert messages._promote_image_variant(str(d / "b.dat")).endswith("b.dat")
    assert messages._thumbnail_image_variant(str(d / "b.dat")).endswith("b_t.dat")
    assert messages._thumbnail_image_variant(str(d / "a_h.dat")).endswith("a_t.dat")
    assert messages._thumbnail_image_variant(str(d / "missing.dat")) is None


def test_image_size_hints_and_scoring(tmp_path):
    xml = '<msg><img hdlength="1000" length="500" cdnthumblength="100"/></msg>'
    hints = messages._image_size_hints(xml)
    assert hints == {"hd": 1000, "main": {500}, "thumb": 100}

    d = tmp_path
    write(d / "hd_h.dat", b"x" * (1000 + 31))  # hd size + V2 overhead
    write(d / "main.dat", b"x" * 500)
    write(d / "th_t.dat", b"x" * 100)
    entries = {e.name: e for e in os.scandir(d)}
    assert messages._score_image_candidate(entries["hd_h.dat"], hints) == 50
    assert messages._score_image_candidate(entries["th_t.dat"], hints) == 10
    assert messages._score_image_candidate(entries["main.dat"], hints) == 40


def test_resolve_media_path_prefers_chat_dir(tmp_path):
    # A foreign chat dir also has files for the month; chat-scoped hash wins.
    db_dir, mine = _img_dir(tmp_path)
    other = mine.parent.parent / ("f" * 32) / "2026-09" / "Img"
    other.mkdir(parents=True)
    write(mine / "mine.dat", b"1" * 500)
    write(other / "other.dat", b"2" * 500)
    path, exists = messages._resolve_media_path(
        str(db_dir), IMG_XML, 3, SEP_TS, CHAT_USER
    )
    assert exists and path.endswith("mine.dat")


def test_resolve_media_path_wrong_month(tmp_path):
    db_dir, attach = _img_dir(tmp_path, month="2026-08")
    write(attach / "x.dat", b"x")
    path, exists = messages._resolve_media_path(
        str(db_dir), IMG_XML, 3, SEP_TS, CHAT_USER
    )
    assert (path, exists) == (None, False)
