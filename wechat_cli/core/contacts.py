"""联系人管理 — 加载、缓存、模糊匹配"""

import hashlib
import os
import re
import sqlite3


_contact_names = None  # {username: display_name}
_contact_full = None   # [{username, nick_name, remark}]
_self_username = None
_self_db_scanned = False  # 兜底扫描是否已跑（成功失败都算）


# ---- extra_buffer protobuf 解码 ----
# contact.extra_buffer 是 protobuf BLOB，多个字段共用一列，靠 field number 区分：
#   field 30        → 逗号分隔的 contact_label.label_id_ 列表（标签）
#   field 14 → 2 → 1 → 手机号（嵌套子消息，纯字符串，无国家码）

def _read_varint(buf, i):
    result = 0
    shift = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7f) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, i


def _parse_protobuf_fields(data):
    """通用 protobuf 解析。返回 [(field_no, wire_type, value)]，wt=0→int, wt=2→bytes。
    wt=1/5 (fixed64/fixed32) 跳过其定长字节继续解析；group (wt=3/4) 不支持，停止。"""
    i = 0
    fields = []
    while i < len(data):
        tag, i = _read_varint(data, i)
        fno, wt = tag >> 3, tag & 7
        if wt == 0:
            val, i = _read_varint(data, i)
        elif wt == 2:
            ln, i = _read_varint(data, i)
            val = data[i:i + ln]
            i += ln
        elif wt == 1:
            i += 8
            continue
        elif wt == 5:
            i += 4
            continue
        else:
            break
        fields.append((fno, wt, val))
    return fields


def _split_label_ids(raw):
    ids = []
    for part in re.split(r'[,，;；|\s]+', raw):
        if part.isdigit():
            ids.append(int(part))
    return ids


def _decode_extra_labels(extra_buffer, label_names=None):
    """extra_buffer field 30 → (label_ids, label_names)。"""
    try:
        fields = _parse_protobuf_fields(extra_buffer)
    except Exception:
        return [], []
    for fno, wt, val in fields:
        if fno == 30 and wt == 2:
            raw = val.decode('utf-8', errors='replace')
            ids = _split_label_ids(raw)
            names = [label_names.get(i) for i in ids] if label_names else []
            return ids, [n for n in names if n]
    return [], []


def _decode_extra_phone(extra_buffer):
    """extra_buffer field 14 → field 2 → field 1 → 手机号字符串。"""
    try:
        for fno, wt, val in _parse_protobuf_fields(extra_buffer):
            if fno != 14 or wt != 2:
                continue
            for fno2, wt2, val2 in _parse_protobuf_fields(val):
                if fno2 != 2 or wt2 != 2:
                    continue
                for fno3, wt3, val3 in _parse_protobuf_fields(val2):
                    if fno3 == 1 and wt3 == 2:
                        return val3.decode('utf-8', errors='replace')
    except Exception:
        pass
    return ''


def _decode_extra_buffer(extra_buffer, label_names=None):
    """返回 (label_ids, labels, phone)。"""
    if not extra_buffer:
        return [], [], ''
    label_ids, labels = _decode_extra_labels(extra_buffer, label_names)
    phone = _decode_extra_phone(extra_buffer)
    return label_ids, labels, phone


def _load_label_names(conn):
    try:
        return {lid: name for lid, name in conn.execute(
            "SELECT label_id_, label_name_ FROM contact_label"
        ).fetchall()}
    except sqlite3.Error:
        return {}


def _load_contacts_from(db_path):
    names = {}
    full = []
    conn = sqlite3.connect(db_path)
    try:
        label_names = _load_label_names(conn)
        for r in conn.execute(
            "SELECT username, nick_name, remark, extra_buffer FROM contact"
        ).fetchall():
            uname, nick, remark, extra_buffer = r
            display = remark if remark else nick if nick else uname
            names[uname] = display
            label_ids, labels, phone = _decode_extra_buffer(extra_buffer, label_names)
            full.append({
                'username': uname,
                'nick_name': nick or '',
                'remark': remark or '',
                'labels': labels,
                'label_ids': label_ids,
                'phone': phone,
            })
    finally:
        conn.close()
    return names, full


def get_contact_names(cache, decrypted_dir):
    global _contact_names, _contact_full
    if _contact_names is not None:
        return _contact_names

    pre_decrypted = os.path.join(decrypted_dir, "contact", "contact.db")
    if os.path.exists(pre_decrypted):
        try:
            _contact_names, _contact_full = _load_contacts_from(pre_decrypted)
            return _contact_names
        except Exception:
            pass

    path = cache.get(os.path.join("contact", "contact.db"))
    if path:
        try:
            _contact_names, _contact_full = _load_contacts_from(path)
            return _contact_names
        except Exception:
            pass

    return {}


def get_contact_full(cache, decrypted_dir):
    global _contact_full
    if _contact_full is None:
        get_contact_names(cache, decrypted_dir)
    return _contact_full or []


def resolve_username(chat_name, cache, decrypted_dir):
    names = get_contact_names(cache, decrypted_dir)
    if chat_name in names or chat_name.startswith('wxid_') or '@chatroom' in chat_name:
        return chat_name
    chat_lower = chat_name.lower()
    for uname, display in names.items():
        if chat_lower == display.lower():
            return uname
    for uname, display in names.items():
        if chat_lower in display.lower():
            return uname
    return None


def _self_username_from_dm_table(names, msg_db_keys, cache):
    """目录名推断失败时的兜底：从任一私聊消息表的 Name2Id 反推自己的 wxid。

    私聊表的发送者只有联系人和自己，所以 Name2Id 里"不是该联系人"的
    wxid 就是自己。覆盖 Linux 旧版布局（~/.local/share/weixin/data/db_storage）
    这类目录名里不含账号名的情况。
    """
    # 先一次性收集所有 Msg_ 表名，避免逐联系人逐个库查 sqlite_master
    table_to_db = {}
    for rel_key in msg_db_keys:
        path = cache.get(rel_key)
        if not path:
            continue
        try:
            conn = sqlite3.connect(path)
            try:
                for (t,) in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
                ):
                    table_to_db.setdefault(t, path)
            finally:
                conn.close()
        except sqlite3.Error:
            continue
    for uname in names:
        if '@chatroom' in uname or uname.startswith('gh_'):
            continue
        table = f"Msg_{hashlib.md5(uname.encode()).hexdigest()}"
        db_path = table_to_db.get(table)
        if not db_path:
            continue
        try:
            conn = sqlite3.connect(db_path)
            try:
                # Name2Id 是全库映射（rowid→wxid），不能按行序取——必须先用
                # 该私聊表实际出现的 real_sender_id 限定范围。私聊表的发送者
                # 只有对方和自己，排除对方后剩下的就是自己。
                sender_ids = [r[0] for r in conn.execute(
                    f'SELECT DISTINCT real_sender_id FROM "{table}" WHERE real_sender_id > 0'
                )]
                if not sender_ids:
                    continue
                placeholders = ','.join('?' * len(sender_ids))
                for (sender_uname,) in conn.execute(
                    f"SELECT user_name FROM Name2Id WHERE rowid IN ({placeholders})",
                    sender_ids,
                ):
                    # 非联系人那一方即自己；要求也在联系人表里以排除异常残留
                    if (sender_uname and sender_uname != uname
                            and '@chatroom' not in sender_uname
                            and not sender_uname.startswith('gh_')
                            and sender_uname in names):
                        return sender_uname
            finally:
                conn.close()
        except sqlite3.Error:
            continue
    return ''


def get_self_username(db_dir, cache, decrypted_dir, msg_db_keys=None):
    global _self_username, _self_db_scanned
    if _self_username:
        return _self_username
    if not db_dir:
        return ''
    names = get_contact_names(cache, decrypted_dir)
    account_dir = os.path.basename(os.path.dirname(db_dir))
    candidates = [account_dir]
    m = re.fullmatch(r'(.+)_([0-9a-fA-F]{4,})', account_dir)
    if m:
        candidates.insert(0, m.group(1))
    for candidate in candidates:
        if candidate and candidate in names:
            _self_username = candidate
            return _self_username
    # 兜底扫描只做一次——display_name_fn 每条消息都会调到这里，
    # 失败结果（''）也要缓存，否则旧版布局下每条消息都会全库重扫。
    # 只有拿到 msg_db_keys 的调用才标记已扫，早期没传 key 的调用不封死后续兜底。
    if msg_db_keys and not _self_db_scanned:
        _self_db_scanned = True
        _self_username = _self_username_from_dm_table(names, msg_db_keys, cache)
    return _self_username or ''


def get_group_members(chatroom_username, cache, decrypted_dir):
    """获取群聊成员列表。

    通过 contact.db 的 chatroom_member 关联表查询。

    Returns:
        dict: {'members': [...], 'owner': str}
        每个 member: {'username': ..., 'nick_name': ..., 'remark': ..., 'display_name': ...}
    """
    pre_decrypted = os.path.join(decrypted_dir, "contact", "contact.db")
    if os.path.exists(pre_decrypted):
        db_path = pre_decrypted
    else:
        db_path = cache.get(os.path.join("contact", "contact.db"))

    if not db_path:
        return {'members': [], 'owner': ''}

    names = get_contact_names(cache, decrypted_dir)
    conn = sqlite3.connect(db_path)
    try:
        # 1. 找到 chatroom 的 contact.id
        row = conn.execute("SELECT id FROM contact WHERE username = ?", (chatroom_username,)).fetchone()
        if not row:
            return {'members': [], 'owner': ''}
        room_id = row[0]

        # 2. 获取群主
        owner = ''
        owner_row = conn.execute("SELECT owner FROM chat_room WHERE id = ?", (room_id,)).fetchone()
        if owner_row and owner_row[0]:
            owner = names.get(owner_row[0], owner_row[0])

        # 3. 获取成员 ID 列表
        member_ids = [r[0] for r in conn.execute(
            "SELECT member_id FROM chatroom_member WHERE room_id = ?", (room_id,)
        ).fetchall()]
        if not member_ids:
            return {'members': [], 'owner': owner}

        # 4. 批量查询成员信息
        placeholders = ','.join('?' * len(member_ids))
        members = []
        for uid, username, nick, remark in conn.execute(
            f"SELECT id, username, nick_name, remark FROM contact WHERE id IN ({placeholders})",
            member_ids
        ):
            display = remark if remark else nick if nick else username
            members.append({
                'username': username,
                'nick_name': nick or '',
                'remark': remark or '',
                'display_name': display,
            })

        # 按 display_name 排序，群主排最前
        members.sort(key=lambda m: (0 if m['username'] == (owner_row[0] if owner_row else '') else 1, m['display_name']))

        return {'members': members, 'owner': owner}
    finally:
        conn.close()


def get_contact_detail(username, cache, decrypted_dir):
    """获取联系人详情。

    Returns:
        dict or None: 联系人详细信息
    """
    pre_decrypted = os.path.join(decrypted_dir, "contact", "contact.db")
    if os.path.exists(pre_decrypted):
        db_path = pre_decrypted
    else:
        db_path = cache.get(os.path.join("contact", "contact.db"))
    if not db_path:
        return None

    conn = sqlite3.connect(db_path)
    try:
        label_names = _load_label_names(conn)
        row = conn.execute(
            "SELECT username, nick_name, remark, alias, description, "
            "small_head_url, big_head_url, verify_flag, local_type, extra_buffer "
            "FROM contact WHERE username = ?",
            (username,)
        ).fetchone()
        if not row:
            return None
        uname, nick, remark, alias, desc, small_url, big_url, verify, ltype, extra_buffer = row
        label_ids, labels, phone = _decode_extra_buffer(extra_buffer, label_names)
        return {
            'username': uname,
            'nick_name': nick or '',
            'remark': remark or '',
            'alias': alias or '',
            'description': desc or '',
            'avatar': small_url or big_url or '',
            'verify_flag': verify or 0,
            'local_type': ltype,
            'labels': labels,
            'label_ids': label_ids,
            'phone': phone,
            'is_group': '@chatroom' in uname,
            'is_subscription': uname.startswith('gh_'),
        }
    finally:
        conn.close()


def display_name_for_username(username, names, db_dir, cache, decrypted_dir, msg_db_keys=None):
    if not username:
        return ''
    if username == get_self_username(db_dir, cache, decrypted_dir, msg_db_keys):
        return 'me'
    return names.get(username, username)
