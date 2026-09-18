"""history 消息 ID 测试 — 合成 Msg_ 表验证 local_id/server_id 暴露"""

import sqlite3

import pytest

from wechat_cli.core.messages import (
    _query_messages,
    collect_chat_history,
)
from conftest import MSG_SCHEMA, msg_table_name


@pytest.fixture
def msg_db(tmp_path):
    """单表消息库：Msg_<md5('friend')>，3 条消息。"""
    path = tmp_path / "message_0.db"
    table = msg_table_name("friend")
    conn = sqlite3.connect(path)
    conn.executescript(MSG_SCHEMA.format(table=table))
    conn.execute("INSERT INTO Name2Id(rowid, user_name) VALUES (1, 'friend')")
    conn.execute("INSERT INTO Name2Id(rowid, user_name) VALUES (2, 'me_wxid')")
    rows = [
        (1, 111111, 1, 0, 1, 1700000000, "hello"),
        (2, 222222, 1, 0, 2, 1700000060, "hi back"),
        (3, 333333, 1, 0, 1, 1700000120, "see you"),
    ]
    conn.executemany(
        f"INSERT INTO [{table}](local_id, server_id, local_type, sort_seq, "
        f"real_sender_id, create_time, message_content) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return str(path), table


def _ctx(db_path, table):
    return {
        "query": "friend", "username": "friend", "display_name": "Friend",
        "db_path": db_path, "table_name": table,
        "message_tables": [{"db_path": db_path, "table_name": table}],
        "is_group": False,
    }


def test_query_messages_returns_server_id(msg_db):
    db_path, table = msg_db
    conn = sqlite3.connect(db_path)
    rows = _query_messages(conn, table, limit=10)
    conn.close()
    assert len(rows[0]) == 7  # local_id, server_id, local_type, create_time, sender, content, ct
    local_ids = [r[0] for r in rows]
    server_ids = [r[1] for r in rows]
    assert sorted(local_ids) == [1, 2, 3]
    assert sorted(server_ids) == [111111, 222222, 333333]


def test_collect_chat_history_entries_have_ids(msg_db):
    db_path, table = msg_db
    names = {"friend": "Friend", "me_wxid": "me"}
    entries, failures = collect_chat_history(
        _ctx(db_path, table), names, lambda u, n: n.get(u, u), limit=10,
    )
    assert not failures
    assert len(entries) == 3
    for e in entries:
        assert set(e) >= {"local_id", "server_id", "timestamp", "time", "sender", "sender_id", "text", "line"}
    by_local = {e["local_id"]: e for e in entries}
    assert by_local[1]["server_id"] == 111111
    assert by_local[1]["text"] == "hello"
    assert by_local[2]["sender"] == "me"
    # sender_id 是 real_sender_id → Name2Id 解析出的 wxid（显示名只是 label）
    assert by_local[1]["sender_id"] == "friend"
    assert by_local[2]["sender_id"] == "me_wxid"
    # 时间升序输出
    assert [e["local_id"] for e in entries] == [1, 2, 3]


@pytest.fixture
def group_db(tmp_path):
    """群聊库：Msg_<md5('room@chatroom')>，覆盖 sender_id 三种解析路径。"""
    path = tmp_path / "message_0.db"
    table = msg_table_name("room@chatroom")
    conn = sqlite3.connect(path)
    conn.executescript(MSG_SCHEMA.format(table=table))
    conn.execute("INSERT INTO Name2Id(rowid, user_name) VALUES (1, 'room@chatroom')")
    conn.execute("INSERT INTO Name2Id(rowid, user_name) VALUES (10, 'alice_wxid')")
    rows = [
        # Name2Id 命中 → sender_id = alice_wxid
        (1, 111111, 1, 0, 10, 1700000000, "alice_wxid:\nhello"),
        # Name2Id 未命中 → 回退到内容解析出的 wxid
        (2, 222222, 1, 0, 99, 1700000060, "bob_wxid:\nhi"),
        # 系统消息：real_sender_id 未命中且无 "wxid:\n" 前缀 → sender_id ''
        (3, 333333, 10000, 0, 0, 1700000120, "system notice"),
        # real_sender_id 解析成群聊自身 → 按内容回退，无前缀则 ''
        (4, 444444, 1, 0, 1, 1700000180, "no sender prefix"),
    ]
    conn.executemany(
        f"INSERT INTO [{table}](local_id, server_id, local_type, sort_seq, "
        f"real_sender_id, create_time, message_content) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return str(path), table


def test_collect_chat_history_group_sender_id(group_db):
    db_path, table = group_db
    ctx = {
        "query": "room@chatroom", "username": "room@chatroom", "display_name": "Room",
        "db_path": db_path, "table_name": table,
        "message_tables": [{"db_path": db_path, "table_name": table}],
        "is_group": True,
    }
    names = {"alice_wxid": "Alice", "bob_wxid": "Bob"}
    entries, failures = collect_chat_history(
        ctx, names, lambda u, n: n.get(u, u), limit=10,
    )
    assert not failures
    by_local = {e["local_id"]: e for e in entries}
    assert by_local[1]["sender"] == "Alice"
    assert by_local[1]["sender_id"] == "alice_wxid"
    assert by_local[2]["sender"] == "Bob"
    assert by_local[2]["sender_id"] == "bob_wxid"
    assert by_local[3]["sender_id"] == ""
    assert by_local[4]["sender_id"] == ""
