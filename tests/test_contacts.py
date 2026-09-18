"""contacts 加载/详情测试 — 使用合成 contact.db，不触碰真实微信数据"""

import sqlite3

from wechat_cli.core.contacts import (
    _load_contacts_from,
    _self_username_from_dm_table,
    get_contact_detail,
)
from conftest import MSG_SCHEMA, msg_table_name


class _NullCache:
    def get(self, rel_key):
        return None


class _MapCache:
    def __init__(self, mapping):
        self._mapping = mapping

    def get(self, rel_key):
        return self._mapping.get(rel_key)


def test_load_contacts_labels_and_phone(contact_db_path):
    names, full = _load_contacts_from(str(contact_db_path))
    assert names["wxid_alice"] == "爱丽丝"
    alice = next(c for c in full if c["username"] == "wxid_alice")
    assert alice["labels"] == ["客户", "Sydney"]
    assert alice["label_ids"] == [1, 5]
    assert alice["phone"] == "0412345678"
    bob = next(c for c in full if c["username"] == "wxid_bob")
    assert bob["labels"] == []
    assert bob["label_ids"] == []
    assert bob["phone"] == ""


def test_get_contact_detail(contact_db_path, tmp_path):
    # get_contact_detail 优先读 decrypted_dir/contact/contact.db
    decrypted_dir = tmp_path / "decrypted"
    (decrypted_dir / "contact").mkdir(parents=True)
    import shutil
    shutil.copy(contact_db_path, decrypted_dir / "contact" / "contact.db")

    info = get_contact_detail("wxid_alice", _NullCache(), str(decrypted_dir))
    assert info["labels"] == ["客户", "Sydney"]
    assert info["label_ids"] == [1, 5]
    assert info["phone"] == "0412345678"
    assert info["is_group"] is False


def test_get_contact_detail_missing(contact_db_path, tmp_path):
    decrypted_dir = tmp_path / "decrypted"
    (decrypted_dir / "contact").mkdir(parents=True)
    import shutil
    shutil.copy(contact_db_path, decrypted_dir / "contact" / "contact.db")
    assert get_contact_detail("wxid_nobody", _NullCache(), str(decrypted_dir)) is None


def _make_msg_db(path, chat_username, senders, msg_sender_ids=None):
    """合成消息库：<chat_username> 的 Msg_ 表 + Name2Id 映射。
    senders 按序作为 Name2Id 的 rowid→user_name；msg_sender_ids 为该表
    消息实际出现的 real_sender_id（= Name2Id.rowid）。"""
    conn = sqlite3.connect(path)
    table = msg_table_name(chat_username)
    conn.executescript(MSG_SCHEMA.format(table=table))
    conn.executemany(
        "INSERT INTO Name2Id(rowid, user_name) VALUES (?, ?)",
        list(enumerate(senders, start=1)),
    )
    for i, sid in enumerate(msg_sender_ids or [], start=1):
        conn.execute(
            f"INSERT INTO {table}(local_id, real_sender_id, create_time, message_content)"
            " VALUES (?, ?, ?, ?)",
            (i, sid, 1700000000, "hi"),
        )
    conn.commit()
    conn.close()


def test_self_username_from_dm_table(tmp_path):
    """目录名推断失败（如 Linux 旧版 .../weixin/data/db_storage）时的兜底：
    私聊表里非对方的发送者就是自己。"""
    db = tmp_path / "message_0.db"
    _make_msg_db(db, "wxid_alice", ["wxid_alice", "me_wxid"], [1, 2])
    cache = _MapCache({"message/message_0.db": str(db)})
    names = {"wxid_alice": "Alice", "me_wxid": "Me"}
    assert (
        _self_username_from_dm_table(names, ["message/message_0.db"], cache)
        == "me_wxid"
    )


def test_self_username_from_dm_table_ignores_unrelated_name2id(tmp_path):
    """Name2Id 是全库映射——行序靠前但从未在该私聊表发言的联系人
    不能被误认成自己。"""
    db = tmp_path / "message_0.db"
    # bob 在 Name2Id 行序最前，但 alice 的私聊表里只有 alice(2) 和自己(3)
    _make_msg_db(
        db, "wxid_alice",
        ["wxid_bob", "wxid_alice", "me_wxid"], [2, 3],
    )
    cache = _MapCache({"message/message_0.db": str(db)})
    names = {"wxid_alice": "Alice", "wxid_bob": "Bob", "me_wxid": "Me"}
    assert (
        _self_username_from_dm_table(names, ["message/message_0.db"], cache)
        == "me_wxid"
    )


def test_self_username_from_dm_table_skips_group_and_unknown(tmp_path):
    """群聊表不可用于推断；不在联系人表的残留 wxid 也不可信。"""
    db = tmp_path / "message_0.db"
    _make_msg_db(db, "room@chatroom", ["wxid_alice", "ghost_wxid"], [1, 2])
    cache = _MapCache({"message/message_0.db": str(db)})
    # names 只含 room@chatroom（被 '@chatroom' 过滤）→ 找不到任何私聊表
    assert _self_username_from_dm_table(
        {"room@chatroom": "Room"}, ["message/message_0.db"], cache
    ) == ""
    # 私聊表存在但另一方不在联系人表 → 不可信，返回 ''
    db2 = tmp_path / "message_1.db"
    _make_msg_db(db2, "wxid_alice", ["wxid_alice", "ghost_wxid"], [1, 2])
    cache2 = _MapCache({"message/message_1.db": str(db2)})
    names = {"wxid_alice": "Alice"}
    assert _self_username_from_dm_table(names, ["message/message_1.db"], cache2) == ""


def test_get_self_username_caches_failed_scan(tmp_path, monkeypatch):
    """兜底扫描失败也要缓存——display_name_fn 每条消息都调 get_self_username，
    旧版布局下不能让每条消息都全库重扫。"""
    import wechat_cli.core.contacts as contacts_mod

    monkeypatch.setattr(contacts_mod, "_self_username", None)
    monkeypatch.setattr(contacts_mod, "_self_db_scanned", False)
    monkeypatch.setattr(
        contacts_mod, "get_contact_names",
        lambda cache, decrypted_dir: {"wxid_alice": "Alice"},
    )
    db = tmp_path / "message_0.db"
    _make_msg_db(db, "wxid_alice", ["wxid_alice"], [1])  # 表里只有对方 → 推断失败
    calls = []

    class CountingCache(_MapCache):
        def get(self, rel_key):
            calls.append(rel_key)
            return super().get(rel_key)

    ccache = CountingCache({"message/message_0.db": str(db)})
    db_dir = str(tmp_path / "data" / "db_storage")  # 目录名不含账号 → 目录推断失败
    keys = ["message/message_0.db"]
    assert contacts_mod.get_self_username(db_dir, ccache, str(tmp_path), keys) == ""
    first_scan_gets = [k for k in calls if k.startswith("message/")]
    assert first_scan_gets  # 第一次确实扫了库
    assert contacts_mod.get_self_username(db_dir, ccache, str(tmp_path), keys) == ""
    assert [k for k in calls if k.startswith("message/")] == first_scan_gets
