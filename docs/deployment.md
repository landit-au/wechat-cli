# Deployment & configuration (Mac mini)

How this fork is installed and pinned on LANDIT's Mac Mini. Companion docs:
[troubleshooting.md](./troubleshooting.md) (symptom → fix),
[extra-buffer-decode.md](./extra-buffer-decode.md) (data layout).

## Environment

- **Infra:** Mac Mini, WeChat for Mac at `/Applications` (standard location) +
  external SSD — the SSD holds the daily WeChat *mobile backup* import only;
  it is unrelated to this tool's live data path (live reads come off the
  internal disk's sandboxed Containers path).
- **Runtime:** Python 3.12 via Homebrew venv; `pip install -e .` source
  install — **not** the npm `@canghe_ai/wechat-cli` prebuilt binary.
- **Fork:** LANDIT org fork of `huohuoer/wechat-cli` (tracked under ROAD-354).

## Path sensitivity

The venv's `activate` script and entry-point shebangs bake in the absolute
project path at creation time — renaming or moving the project folder breaks
`wechat-cli` (`command not found` while `(.venv)` still shows in the prompt).
Fix is recreating the venv from the new path:

```bash
rm -rf .venv && python3.12 -m venv .venv && .venv/bin/pip install -e .
```

`~/.wechat-cli/` config and keys are unaffected.

## WeChat version — pinned

Pinned to **4.1.8.106** (confirmed working). **4.1.15.18 is confirmed
broken** — it silently hid local chat history (fewer messages returned than
actually exist) and caused `task_for_pid` to fail on every attempt, even
after a correct re-sign with `get-task-allow` and a full WeChat restart.
Do not upgrade past 4.1.8.106.

Auto-update is disabled in WeChat preferences — **verify this after every
reinstall**, since a fresh install can silently re-enable it.

Treat any WeChat version change as requiring `sudo wechat-cli init --force`
plus a sanity check against known message counts for a test contact.

## Permissions

- `init` / `init --force` requires `sudo` (needs `task_for_pid` access to
  WeChat's live process memory). All other commands run as a normal user —
  after a `sudo init`, run `sudo chown -R $(whoami):staff ~/.wechat-cli` or
  later non-sudo reads fail on the root-owned state files.
- **Terminal requirement:** run `init` from an app that itself has Full Disk
  Access (e.g. Terminal.app). A shell spawned from an IDE fails
  `task_for_pid` even under sudo — macOS TCC attributes the access to the
  hosting *application*, not the process's uid.
- The launchd consumer (wechat-relay) hits TCC the same way via its `node`
  executable — see landit-digital `wechat-relay/docs/operations.md` for the
  Full Disk Access grants the scheduled job needs.
