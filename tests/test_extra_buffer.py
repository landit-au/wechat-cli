"""extra_buffer protobuf 解码器单元测试"""

from wechat_cli.core.contacts import (
    _decode_extra_buffer,
    _decode_extra_labels,
    _decode_extra_phone,
    _parse_protobuf_fields,
    _read_varint,
    _split_label_ids,
)
from conftest import encode_varint, field_bytes, field_varint, make_extra_buffer


def test_read_varint_single_byte():
    assert _read_varint(b"\x08", 0) == (8, 1)


def test_read_varint_multi_byte():
    assert _read_varint(encode_varint(300), 0) == (300, 2)


def test_parse_protobuf_fields_mixed():
    blob = field_varint(1, 42) + field_bytes(30, b"1,5")
    fields = _parse_protobuf_fields(blob)
    assert fields == [(1, 0, 42), (30, 2, b"1,5")]


def test_parse_protobuf_fields_truncated():
    # 截断的 len-delimited 字段不应崩溃
    blob = field_bytes(4, b"abcdef")[:4]
    _parse_protobuf_fields(blob)


def test_parse_protobuf_fields_skips_fixed_width():
    # wt=1 (fixed64) / wt=5 (fixed32) 字段应跳过而非中断解析
    blob = (
        encode_varint((7 << 3) | 1) + b"\x00" * 8
        + encode_varint((8 << 3) | 5) + b"\x00" * 4
        + field_bytes(30, b"1")
    )
    fields = _parse_protobuf_fields(blob)
    assert fields == [(30, 2, b"1")]


def test_split_label_ids_ascii():
    assert _split_label_ids("226,251") == [226, 251]


def test_split_label_ids_cjk_and_misc_delimiters():
    assert _split_label_ids("1，2;3；4|5 6") == [1, 2, 3, 4, 5, 6]


def test_split_label_ids_ignores_non_numeric():
    assert _split_label_ids("1,abc,,3") == [1, 3]


def test_decode_labels_with_names():
    blob = make_extra_buffer(labels_raw="1,5")
    ids, names = _decode_extra_labels(blob, {1: "客户", 5: "Sydney"})
    assert ids == [1, 5]
    assert names == ["客户", "Sydney"]


def test_decode_labels_missing_name_dropped():
    blob = make_extra_buffer(labels_raw="1,99")
    ids, names = _decode_extra_labels(blob, {1: "客户"})
    assert ids == [1, 99]
    assert names == ["客户"]


def test_decode_labels_empty_blob():
    assert _decode_extra_labels(b"") == ([], [])
    assert _decode_extra_labels(make_extra_buffer(labels_raw="")) == ([], [])


def test_decode_phone_nested():
    blob = make_extra_buffer(phone="+61411225796")
    assert _decode_extra_phone(blob) == "+61411225796"


def test_decode_phone_flag_only():
    # field 14 仅有 "phone present" flag、无 field 2 → 无号码
    blob = field_bytes(14, field_varint(1, 0))
    assert _decode_extra_phone(blob) == ""


def test_decode_phone_absent():
    assert _decode_extra_phone(make_extra_buffer(labels_raw="1")) == ""
    assert _decode_extra_phone(b"") == ""


def test_decode_extra_buffer_full():
    blob = make_extra_buffer(labels_raw="1", phone="0451122734")
    ids, names, phone = _decode_extra_buffer(blob, {1: "客户"})
    assert ids == [1]
    assert names == ["客户"]
    assert phone == "0451122734"


def test_decode_extra_buffer_none_and_garbage():
    assert _decode_extra_buffer(None) == ([], [], "")
    assert _decode_extra_buffer(b"\xff\xff\xff\xff") == ([], [], "")
