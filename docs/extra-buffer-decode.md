# `contact.extra_buffer` protobuf decode

Reference for the fields this fork surfaces from `contact.extra_buffer` —
labels (field 30) and mobile number (field 14→2→1). Written up from live
analysis; the working decoder lives in `core/contacts.py` — extend there,
don't fork a second parser. All example values in this doc are synthetic —
never paste real account data into docs.

## Schema

```sql
CREATE TABLE contact(id INTEGER PRIMARY KEY, username TEXT, local_type INTEGER, alias TEXT,
  encrypt_username TEXT, flag INTEGER, delete_flag INTEGER, verify_flag INTEGER, remark TEXT,
  remark_quan_pin TEXT, remark_pin_yin_initial TEXT, nick_name TEXT, pin_yin_initial TEXT,
  quan_pin TEXT, big_head_url TEXT, small_head_url TEXT, head_img_md5 TEXT,
  chat_room_notify INTEGER, is_in_chat_room INTEGER, description TEXT, extra_buffer BLOB,
  chat_room_type INTEGER);
CREATE INDEX contact_localType ON contact(local_type);

CREATE TABLE contact_label(label_id_ INTEGER PRIMARY KEY, label_name_ TEXT, sort_order_ INTEGER);
```

No separate contact↔label junction table exists. Tag membership lives entirely
inside `contact.extra_buffer` (protobuf field 30) as a comma-delimited list of
`contact_label.label_id_` values — `contact_label` itself only holds the label
ID→name→sort-order definitions, not membership.

Upstream `contacts.py` only selects `username, nick_name, remark, alias,
description, ...` from the `contact` table and ignores `extra_buffer` entirely
— both label and phone data live in that one shared BLOB column, disambiguated
only by protobuf field number inside it, not separate columns.

## Generic decoder

No WeChat-specific framing needed — parses byte-for-byte:

```python
def read_varint(buf, i):
    result = 0; shift = 0
    while True:
        b = buf[i]; i += 1
        result |= (b & 0x7f) << shift
        if not (b & 0x80): break
        shift += 7
    return result, i

def parse(data):
    i = 0; fields = []
    while i < len(data):
        tag, i = read_varint(data, i)
        fno, wt = tag >> 3, tag & 7
        if wt == 0: val, i = read_varint(data, i)
        elif wt == 2:
            ln, i = read_varint(data, i)
            val = data[i:i+ln]; i += ln
        else: break
        fields.append((fno, wt, val))
    return fields
```

## Confirmed fields (from a live 329-byte blob)

| Field | Contents |
| --- | --- |
| 4 | Signature/bio, e.g. `一个普通的个性签名` |
| 5 / 6 / 7 | Country / State / City, e.g. `AU` / `New South Wales` / `Sydney` |
| 14 → 2 → 1 | Mobile number — nested submessage, plain string, no country code (walkthrough below) |
| 27 → sub 2 | Moments cover-photo URL (`http://mmsns.qpic.cn/mmsns/...`) |
| 30 | Comma-delimited `contact_label.label_id_` list (labels/标签) |
| 41 | Unix timestamp, last profile/Moments sync |

Fields present but not confidently decoded: 8, 10, 11, 24, 37, 38, and nested
43 — numeric, no reliable reference, not worth guessing at.

## Field 30 (labels) — cross-referencing example

```sql
sqlite3 contact.db "SELECT label_id_, label_name_ FROM contact_label WHERE label_id_=7;"
-- 7|VIP客户|3   (id|name|sort_order — synthetic example)
```

**Provenance:** field 30's meaning was confirmed independently in
`r266-tech/wechat-cli`'s source (`cmd/wechat-cli/contact_labels.go:13-15`),
read for reference only — the fact was independently derivable from raw bytes
before that was found, and the ~20-line extraction (splitting on `, ， ; ； |`
and whitespace, parsing each as an int64 label ID) is a generic
top-level-field scanner, not WeChat-specific IP. It was reimplemented
independently rather than copied, since r266-tech carries active DMCA §1201
exposure.

## Field 14 (mobile number) — decode walkthrough

Confirmed by a live before/after diff: a mobile number was added to a test
contact via WeChat mobile (synced to desktop, confirming it's account-level not
device-local), then the blob was re-decrypted and diffed:

```
extra_buffer length: 329 → 343 bytes

CHANGED field 14: old=b'\x08\x00'  new=b'\x08\x01\x12\x0c\n\n0412345678'
CHANGED field 11: old=3 → 1        (likely an internal revision counter)
CHANGED field 19: old=0 → 5        (likely an internal revision counter)
```

Fully decoding the new field 14 as a nested submessage:

```
field 14 raw: 0801120c0a0a30343132333435363738
  field14.1 (int): 1                     # "phone present" flag, was 0
  field14.2 (nested):
    field14.2.1: b'0412345678'           # the phone number, plain string, no country code
```

So the path is `contact.extra_buffer → field 14 → field 2 → field 1`. To
extract: run `parse()` on `extra_buffer`, take field 14's bytes, `parse()`
again, take that result's field 2's bytes, `parse()` a third time, take field
1's byte string.

**Validation:** writing a deliberately malformed number (`0412345678+61`) via
WeChat's contact editor was rejected/wiped before being stored. Both
`0412345678` (domestic) and `+61412345678` (E.164) were accepted — WeChat
enforces basic format validity client-side before this field is ever
populated, a meaningful trust signal for anything synced from it.

**Caveat:** the field 14→2→1 mapping was derived from a single before/after
diff on one contact — not cross-checked against a second contact or an
external reference (unlike field 30, which r266-tech's source independently
corroborates). Worth testing on a second contact before depending on it
broadly.
