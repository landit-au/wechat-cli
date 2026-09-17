# AGENTS.md

Read-only WeChat data query CLI (fork of `huohuoer/wechat-cli`) — decrypts local
WeChat 4.x databases and exposes messages, contacts, sessions, etc. as JSON for
LLM/agent consumption.

## Hard rules

- **Read-only by design.** No send/write capability — do not add one (security
  posture per LANDIT ROAD-336 review). UI automation of WeChat is out of scope.
- **Sensitive data everywhere.** `~/.wechat-cli/all_keys.json` holds SQLCipher
  keys; decrypted DBs land in `$TMPDIR/wechat_cli_cache` and
  `~/.wechat-cli/decrypted`. Never commit keys, `*.db*`, or `*.json` output dumps
  (already gitignored). Don't echo key material or bulk personal data into logs.
- **Exports go in `exports/`.** Dump query/export output there — the dir is
  gitignored (except `.gitkeep`) and is the designated place for personal data
  on disk, e.g. `wechat-cli history "X" --limit 500 > exports/x.json`.
- **Chinese comments/docstrings** are the codebase convention — match them.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/wechat-cli init        # one-time: extract keys (WeChat must be running + logged in)
.venv/bin/wechat-cli sessions    # smoke test
```

`init` runs a bundled C binary (`wechat_cli/bin/find_all_keys_macos.*`) that
reads WeChat process memory via `task_for_pid`. If blocked, it re-signs
WeChat preserving entitlements (adds `get-task-allow`), then you must restart
WeChat and re-run `init`. Works without sudo once re-signed.
`wechat_ent.plist` produced during re-sign is a temp artifact — delete it.

## Layout

- `wechat_cli/commands/` — one click command per file, registered in `main.py`
- `wechat_cli/core/` — `context` (AppContext singleton), `config` (`~/.wechat-cli`),
  `db_cache` (mtime-keyed decrypt cache), `crypto` (SQLCipher AES-256-CBC
  page/WAL decrypt), `contacts`, `messages`, `key_utils`
- `wechat_cli/keys/` — platform key scanners; `output/formatter.py` — `output(data, fmt)`

## Data model gotchas

- `contact.db`: `contact` table + `contact_label` (label id→name only; membership
  is **not** a table — it's `contact.extra_buffer` protobuf field 30).
- `extra_buffer` protobuf: field 30 = comma-separated label_ids; field 14→2→1 =
  mobile number. Shared decoder lives in `core/contacts.py` — extend there, don't
  fork a second parser.
- Message tables: `Msg_<md5(username)>` across `message/message_*.db`;
  `Name2Id` maps `real_sender_id`→username. Message identity = `local_id`/`server_id`.
- Live DBs are read while WeChat writes — `db_cache` validates decrypts with
  `PRAGMA integrity_check` and retries; don't bypass it by copying DB files.
- `init` reuses `config.json`'s `db_dir`; machines may have several
  `xwechat_files/*/db_storage` accounts — never auto-switch.

## Verify

Hermetic suite (synthetic fixture DBs — never touches real WeChat data):

```bash
.venv/bin/pip install -e ".[dev]" && .venv/bin/python -m pytest tests/
```

Covers the `extra_buffer` decoder, contact loading/detail, history message IDs,
db_cache torn-read poisoning, and init `db_dir` preservation. For end-to-end
checks against the live DB:

```bash
.venv/bin/wechat-cli contacts --detail "<wxid>"   # labels/phone
.venv/bin/wechat-cli history "<chat>" --limit 3    # local_id/server_id in JSON
```
