"""contacts 加载/详情测试 — 使用合成 contact.db，不触碰真实微信数据"""

from wechat_cli.core.contacts import _load_contacts_from, get_contact_detail


class _NullCache:
    def get(self, rel_key):
        return None


def test_load_contacts_labels_and_phone(contact_db_path):
    names, full = _load_contacts_from(str(contact_db_path))
    assert names["wxid_alice"] == "爱丽丝"
    alice = next(c for c in full if c["username"] == "wxid_alice")
    assert alice["labels"] == ["客户", "Sydney"]
    assert alice["phone"] == "0412345678"
    bob = next(c for c in full if c["username"] == "wxid_bob")
    assert bob["labels"] == []
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
