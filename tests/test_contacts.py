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


def _make_msg_db(path, chat_username, senders):
    """合成消息库：一张 <chat_username> 的 Msg_ 表 + Name2Id 映射。"""
    conn = sqlite3.connect(path)
    conn.executescript(MSG_SCHEMA.format(table=msg_table_name(chat_username)))
    conn.executemany(
        "INSERT INTO Name2Id(rowid, user_name) VALUES (?, ?)",
        list(enumerate(senders, start=1)),
    )
    conn.commit()
    conn.close()


def test_self_username_from_dm_table(tmp_path):
    """目录名推断失败（如 Linux 旧版 .../weixin/data/db_storage）时的兜底：
    私聊表 Name2Id 里非联系人那一方就是自己。"""
    db = tmp_path / "message_0.db"
    _make_msg_db(db, "wxid_alice", ["wxid_alice", "me_wxid"])
    cache = _MapCache({"message/message_0.db": str(db)})
    names = {"wxid_alice": "Alice", "me_wxid": "Me"}
    assert (
        _self_username_from_dm_table(names, ["message/message_0.db"], cache)
        == "me_wxid"
    )


def test_self_username_from_dm_table_skips_group_and_unknown(tmp_path):
    """群聊表不可用于推断；Name2Id 里不在联系人表的残留 wxid 也不可信。"""
    db = tmp_path / "message_0.db"
    _make_msg_db(db, "room@chatroom", ["wxid_alice", "ghost_wxid"])
    cache = _MapCache({"message/message_0.db": str(db)})
    # names 只含 room@chatroom（被 '@chatroom' 过滤）→ 找不到任何私聊表
    assert _self_username_from_dm_table(
        {"room@chatroom": "Room"}, ["message/message_0.db"], cache
    ) == ""
    # 私聊表存在但另一方不在联系人表 → 不可信，返回 ''
    db2 = tmp_path / "message_1.db"
    _make_msg_db(db2, "wxid_alice", ["wxid_alice", "ghost_wxid"])
    cache2 = _MapCache({"message/message_1.db": str(db2)})
    names = {"wxid_alice": "Alice"}
    assert _self_username_from_dm_table(names, ["message/message_1.db"], cache2) == ""
