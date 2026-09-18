# wechat-cli troubleshooting

Symptom → cause → fix for this CLI on the Mac Mini. For the scheduled
consumer's side (launchd ticks, Slack alerts, HubSpot sync), see
landit-digital `wechat-relay/docs/troubleshooting.md`.

| Symptom | Cause | Fix |
| --- | --- | --- |
| `task_for_pid failed` even under sudo | The terminal-hosting **app** lacks Full Disk Access (macOS TCC, independent of sudo) | Run `init` from Terminal.app with Full Disk Access granted — not an IDE-spawned shell |
| `codesign: ... Operation not permitted` during auto re-sign | The tool's own re-sign fallback failed | Manual `codesign --entitlements :wechat_ent.plist` workaround — the colon prefix is required for valid XML output |
| WeChat "can't be opened", `spctl` reports `rejected` | A re-sign corrupted the app's code signature | Clean reinstall of WeChat — do **not** disable Gatekeeper system-wide |
| `init` reports "提取到 0 个密钥" despite a successful scan | Cosmetic misreport bug in huohuoer (indexes on a `"salt"` key that doesn't exist in the C binary's JSON schema) | Harmless — fixed by upstream PR #4, cherry-pick tracked in ROAD-354 |
| `PermissionError` on `new-messages`/`sessions` | `~/.wechat-cli` left root-owned by an earlier `sudo` call | `sudo chown -R $(whoami):staff ~/.wechat-cli` — only `init` needs sudo; keep everything else non-sudo |
| `wechat-cli: command not found`, `(.venv)` still in prompt | Project folder renamed/moved — venv shebangs + `activate` bake in the absolute path | `rm -rf .venv && python3.12 -m venv .venv && .venv/bin/pip install -e .` — `~/.wechat-cli` config/keys untouched |
| `history "NAME"` resolves to the wrong contact | Ambiguous display-name substring match — returns the first hit, no warning (`core/contacts.py` `resolve_username`) | Always resolve via `contacts --query` to an exact wxid first; never pass a bare name into automation |
| Raw voice/image XML with live `aeskey`/`voiceurl` in `history` output | Non-text `msg_type` content passed through unparsed | Consumers must detect non-text types and emit placeholders — never forward/store the raw XML (wechat-relay's `sanitize.ts` is the reference) |
| `history` returns far fewer messages than the app shows, or `task_for_pid failed` recurs after a correct re-sign + full WeChat restart | WeChat auto-updated past the ceiling — 4.1.15.18 confirmed broken (hid history **and** blocked debug-attach) | Downgrade to 4.1.8.106, disable auto-update in WeChat preferences, `sudo wechat-cli init --force`, sanity-check message counts |
| `init` seems fine but later polls see nothing new | `init` ran while WeChat was closed/not logged in, or `config.json` points at a stale `db_dir` (multi-account machines) | WeChat must be running and logged in for `init`; check `~/.wechat-cli/config.json`'s `db_dir` — never auto-switch accounts |
