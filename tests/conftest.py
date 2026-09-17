"""测试辅助 — 合成 protobuf 编码与 SQLite fixture 库（不触碰真实微信数据）"""

import hashlib
import sqlite3

import pytest


# ---- protobuf 编码辅助 ----

def encode_varint(value):
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def field_bytes(fno, data):
    return encode_varint((fno << 3) | 2) + encode_varint(len(data)) + data


def field_varint(fno, value):
    return encode_varint(fno << 3) + encode_varint(value)


def make_extra_buffer(labels_raw=None, phone=None):
    """构造 contact.extra_buffer：field 30 = 标签 ID 字符串，field 14→2→1 = 手机号。"""
    buf = b""
    if phone is not None:
        inner = field_bytes(1, phone.encode())
        buf += field_bytes(14, field_varint(1, 1) + field_bytes(2, inner))
    if labels_raw is not None:
        buf += field_bytes(30, labels_raw.encode())
    return buf


# ---- SQLite fixture ----

CONTACT_SCHEMA = """
CREATE TABLE contact(
  id INTEGER PRIMARY KEY, username TEXT, local_type INTEGER, alias TEXT,
  encrypt_username TEXT, flag INTEGER, delete_flag INTEGER, verify_flag INTEGER,
  remark TEXT, remark_quan_pin TEXT, remark_pin_yin_initial TEXT, nick_name TEXT,
  pin_yin_initial TEXT, quan_pin TEXT, big_head_url TEXT, small_head_url TEXT,
  head_img_md5 TEXT, chat_room_notify INTEGER, is_in_chat_room INTEGER,
  description TEXT, extra_buffer BLOB, chat_room_type INTEGER);
CREATE TABLE contact_label(label_id_ INTEGER PRIMARY KEY, label_name_ TEXT, sort_order_ INTEGER);
"""


def msg_table_name(username):
    return "Msg_" + hashlib.md5(username.encode()).hexdigest()


MSG_SCHEMA = """
CREATE TABLE {table}(
  local_id INTEGER PRIMARY KEY, server_id INTEGER, local_type INTEGER,
  sort_seq INTEGER, real_sender_id INTEGER, create_time INTEGER, status INTEGER,
  upload_status INTEGER, download_status INTEGER, server_seq INTEGER,
  origin_source INTEGER, source TEXT, message_content TEXT,
  compress_content TEXT, packed_info_data BLOB,
  WCDB_CT_message_content INTEGER, WCDB_CT_source INTEGER);
CREATE TABLE Name2Id(user_name TEXT);
"""


def insert_contact(conn, username, nick_name="", remark="", extra_buffer=None, **kw):
    cols = {"username": username, "nick_name": nick_name, "remark": remark,
            "extra_buffer": extra_buffer}
    cols.update(kw)
    keys = ", ".join(cols)
    conn.execute(
        f"INSERT INTO contact({keys}) VALUES ({', '.join('?' * len(cols))})",
        list(cols.values()),
    )


@pytest.fixture
def contact_db_path(tmp_path):
    """带真实 schema 的 contact.db，预置 2 个标签 + 2 个联系人。"""
    path = tmp_path / "contact.db"
    conn = sqlite3.connect(path)
    conn.executescript(CONTACT_SCHEMA)
    conn.executemany(
        "INSERT INTO contact_label VALUES (?, ?, ?)",
        [(1, "客户", 0), (5, "Sydney", 1)],
    )
    insert_contact(
        conn, "wxid_alice", nick_name="Alice", remark="爱丽丝",
        extra_buffer=make_extra_buffer(labels_raw="1,5", phone="0412345678"),
    )
    insert_contact(conn, "wxid_bob", nick_name="Bob")
    conn.commit()
    conn.close()
    return path
