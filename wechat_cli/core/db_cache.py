"""解密数据库缓存 — mtime 检测变化，跨会话复用"""

import hashlib
import json
import os
import sqlite3
import tempfile
import time

from .crypto import full_decrypt, decrypt_wal, SQLITE_HDR
from .key_utils import get_key_info

_DECRYPT_ATTEMPTS = 3
_DECRYPT_RETRY_DELAY = 0.3


def _has_sqlite_header(path):
    try:
        with open(path, 'rb') as f:
            return f.read(len(SQLITE_HDR)) == SQLITE_HDR
    except OSError:
        return False


def _is_valid_sqlite(path):
    """校验解密结果完整性（源库可能被微信实时写入，解密可能读到撕裂页）。"""
    if not _has_sqlite_header(path):
        return False
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            return bool(row) and row[0] == 'ok'
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return False


class DBCache:
    CACHE_DIR = os.path.join(tempfile.gettempdir(), "wechat_cli_cache")
    MTIME_FILE = os.path.join(tempfile.gettempdir(), "wechat_cli_cache", "_mtimes.json")

    def __init__(self, all_keys, db_dir):
        self._all_keys = all_keys
        self._db_dir = db_dir
        self._cache = {}  # rel_key -> (db_mtime, wal_mtime, tmp_path)
        os.makedirs(self.CACHE_DIR, exist_ok=True)
        self._load_persistent_cache()

    def _cache_path(self, rel_key):
        h = hashlib.md5(rel_key.encode()).hexdigest()[:12]
        return os.path.join(self.CACHE_DIR, f"{h}.db")

    def _load_persistent_cache(self):
        if not os.path.exists(self.MTIME_FILE):
            return
        try:
            with open(self.MTIME_FILE, encoding="utf-8") as f:
                saved = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        for rel_key, info in saved.items():
            tmp_path = info["path"]
            if not os.path.exists(tmp_path):
                continue
            rel_path = rel_key.replace('\\', os.sep)
            db_path = os.path.join(self._db_dir, rel_path)
            wal_path = db_path + "-wal"
            try:
                db_mtime = os.path.getmtime(db_path)
                wal_mtime = os.path.getmtime(wal_path) if os.path.exists(wal_path) else 0
            except OSError:
                continue
            if db_mtime == info["db_mt"] and wal_mtime == info["wal_mt"]:
                self._cache[rel_key] = (db_mtime, wal_mtime, tmp_path)

    def _save_persistent_cache(self):
        data = {}
        for rel_key, (db_mt, wal_mt, path) in self._cache.items():
            data[rel_key] = {"db_mt": db_mt, "wal_mt": wal_mt, "path": path}
        try:
            with open(self.MTIME_FILE, 'w', encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass

    def get(self, rel_key):
        key_info = get_key_info(self._all_keys, rel_key)
        if not key_info:
            return None
        rel_path = rel_key.replace('\\', '/').replace('/', os.sep)
        db_path = os.path.join(self._db_dir, rel_path)
        wal_path = db_path + "-wal"
        if not os.path.exists(db_path):
            return None

        try:
            db_mtime = os.path.getmtime(db_path)
            wal_mtime = os.path.getmtime(wal_path) if os.path.exists(wal_path) else 0
        except OSError:
            return None

        if rel_key in self._cache:
            c_db_mt, c_wal_mt, c_path = self._cache[rel_key]
            if c_db_mt == db_mtime and c_wal_mt == wal_mtime and _has_sqlite_header(c_path):
                return c_path

        tmp_path = self._cache_path(rel_key)
        enc_key = bytes.fromhex(key_info["enc_key"])
        for attempt in range(_DECRYPT_ATTEMPTS):
            full_decrypt(db_path, tmp_path, enc_key)
            if os.path.exists(wal_path):
                decrypt_wal(wal_path, tmp_path, enc_key)
            if _is_valid_sqlite(tmp_path):
                break
            if attempt < _DECRYPT_ATTEMPTS - 1:
                time.sleep(_DECRYPT_RETRY_DELAY)
        else:
            self._cache.pop(rel_key, None)
            self._save_persistent_cache()
            return None
        self._cache[rel_key] = (db_mtime, wal_mtime, tmp_path)
        self._save_persistent_cache()
        return tmp_path

    def cleanup(self):
        self._save_persistent_cache()
