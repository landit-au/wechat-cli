"""DBCache 回归测试 — 防止撕裂解密结果被缓存（曾导致全部查询失败的 bug）"""

import json
import os
import sqlite3

import pytest

import wechat_cli.core.db_cache as db_cache_mod
from wechat_cli.core.db_cache import DBCache, _has_sqlite_header, _is_valid_sqlite


REL_KEY = "contact/contact.db"
ENC_KEY_HEX = "ab" * 32


def _make_valid_db(path):
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    conn.executescript(
        "DROP TABLE IF EXISTS t;"
        "CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT);"
        "INSERT INTO t(v) VALUES ('x');"
    )
    conn.commit()
    conn.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """独立 db_dir + cache 目录，full_decrypt/decrypt_wal 可注入。"""
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(DBCache, "CACHE_DIR", str(cache_dir))
    monkeypatch.setattr(DBCache, "MTIME_FILE", str(cache_dir / "_mtimes.json"))
    monkeypatch.setattr(db_cache_mod, "_DECRYPT_RETRY_DELAY", 0)

    db_dir = tmp_path / "db_storage"
    (db_dir / "contact").mkdir(parents=True)
    (db_dir / "contact" / "contact.db").write_bytes(b"encrypted")

    all_keys = {REL_KEY: {"enc_key": ENC_KEY_HEX}}
    cache = DBCache(all_keys, str(db_dir))
    calls = {"decrypt": 0}

    def set_decrypt_result(valid):
        def fake_full_decrypt(db_path, out_path, enc_key):
            calls["decrypt"] += 1
            if valid:
                _make_valid_db(out_path)
            else:
                with open(out_path, "wb") as f:
                    f.write(b"\x00" * 8192)
        monkeypatch.setattr(db_cache_mod, "full_decrypt", fake_full_decrypt)

    monkeypatch.setattr(db_cache_mod, "decrypt_wal", lambda *a: 0)
    return cache, set_decrypt_result, calls, db_dir


def test_get_decrypts_and_caches(env):
    cache, set_decrypt, calls, _ = env
    set_decrypt(True)
    p = cache.get(REL_KEY)
    assert p and _is_valid_sqlite(p)
    # 第二次命中缓存，不再解密
    assert cache.get(REL_KEY) == p
    assert calls["decrypt"] == 1


def test_torn_decrypt_not_cached(env):
    """撕裂读取（微信写入中）不得进入缓存 — 曾导致缓存中毒的核心回归。"""
    cache, set_decrypt, calls, _ = env
    set_decrypt(False)
    assert cache.get(REL_KEY) is None
    assert calls["decrypt"] == db_cache_mod._DECRYPT_ATTEMPTS
    # 失败结果不写入持久缓存
    assert not os.path.exists(DBCache.MTIME_FILE) or REL_KEY not in json.load(
        open(DBCache.MTIME_FILE)
    )


def test_poisoned_cache_file_not_served(env):
    """已存在的损坏缓存文件不得被直接返回。"""
    cache, set_decrypt, calls, db_dir = env
    set_decrypt(True)
    good = cache.get(REL_KEY)
    assert _is_valid_sqlite(good)

    # 模拟中毒：缓存文件被撕裂内容覆盖（mtime 记录不变）
    with open(good, "wb") as f:
        f.write(b"\x00" * 8192)

    p2 = cache.get(REL_KEY)
    assert p2 is not None
    assert _is_valid_sqlite(p2)


def test_is_valid_sqlite(tmp_path):
    good = tmp_path / "good.db"
    _make_valid_db(str(good))
    assert _is_valid_sqlite(str(good))
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"\x00" * 8192)
    assert not _is_valid_sqlite(str(bad))
    assert not _has_sqlite_header(str(bad))
    assert not _is_valid_sqlite(str(tmp_path / "nonexistent.db"))
