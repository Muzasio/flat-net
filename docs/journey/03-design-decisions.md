# 03. Design decisions

Each decision lists what was chosen, what else was possible, and why.

## 1. Block running apps with nftables + cgroup matching
- **Chosen:** one nftables table `inet flatnet` with rules like `socket cgroupv2 level 5 "<scope path>" drop`.
- **Other options:**
  - `flatpak run --unshare=network`: only works at launch. Kept as the **Offline (sandbox)** mode.
  - systemd `IPAddressDeny=` on the scope: not used. Applying it to user scopes needs extra privileges (prior knowledge, not tested).
- **Why:** it is the only option found that works on an already running app and touches only that app.

## 2. Rebuild the whole table each time
- **Chosen:** every change sends one script: `table inet flatnet` + `delete table inet flatnet` + the full new table.
- **Why:** the first two lines make "delete" safe even if the table does not exist. Rebuilding from the state means the firewall always matches what the program thinks, with no leftover rules.
- **Cost:** counters reset on each rebuild, so the program adds the old counter values to a running total (`self.totals`) before each rebuild.

## 3. One root helper per session
- **Chosen:** `pkexec python3 -c HELPER` runs once. It reads one JSON line per request, runs `nft -f -`, and replies with one JSON line.
- **Other option:** `pkexec nft ...` for each action (used in the first Temp Chrome GUI).
- **Why:** live data counters need root every 2 seconds. One password per session is usable; one per action is not.
- **Security:** the helper only runs `nft`, and only the FlatNet process can write to it (a private pipe).

## 4. Tkinter instead of PyQt6
- **Chosen:** Tkinter (`tkinter`, `ttk`).
- **Why:** the user wanted it to run on older distros. Tkinter is part of Python and packaged on almost every distro. Code also avoids newer Python features (no `removeprefix`, no `capture_output`, no `text=`), aiming at Python 3.6+. **Running on Python 3.6 was not tested.**

## 5. App list in `apps.json`, not in code
- **Chosen:** apps, IDs and extra arguments live in `apps.json`. The start command is read from `flatpak info -m`.
- **Why:** the user asked for no hardcoded logic. Adding an app is a one-line change.

## 6. Profiles in the program folder, with XDG redirect
- **Chosen:** `profiles/<App>/<name>/data/`, given to the app with `--filesystem=<data>` and `--command=env ... XDG_CONFIG_HOME=<data>/config ...`.
- **Other options:** per-app flags only (`--user-data-dir`, `--profile`), or folders inside `~/.var/app/<id>/`.
- **Why:** the user wanted profiles inside the program folder. The XDG redirect works for most apps without app-specific flags. App flags are added in `apps.json` only when needed (LibreOffice).

## 7. Four network modes
- **Online**, **LAN only**, **Cut (switchable)** use the firewall and can be changed while running.
- **Offline (sandbox)** uses `--unshare=network`: zero leak, but cannot be changed while running.
- **Why:** the user found that "offline" without switching was a problem, but zero-leak is still useful. Both are kept, and the user chooses.

## 8. Blocks belong to groups, not instances
- **Chosen:** `self.net` maps a group to a mode. A group is `app:<id>` for a normal app, or the profile folder path.
- **Why:** an app can start new instances at any time. Per-instance blocks let new instances go online. Groups make new instances inherit the block automatically.

## 9. Fast 0.2 s loop for new instances
- **Chosen:** `tick()` scans every 0.2 s and applies blocks to new instances at once. The window redraws every 2 s.
- **Why:** a block cannot be added before the scope exists, so the gap must be as short as possible.

## 10. Show only real firewall state
- **Chosen:** the Network column in the running table uses `self.applied` (what nft accepted), not what was requested. `apply()` keeps the old state if nft fails.
- **Why:** showing "Cut" when the app is online is the worst possible bug for a privacy tool.

## 11. Profile matching by path in process details
- **Chosen:** an instance belongs to a profile if the profile path (plus `/`) is found in any of its processes' `cmdline` or `environ`.
- **Why:** it works for every app, because the XDG variables always contain the path. The trailing `/` stops `temp-1` from matching `temp-10`.

## 12. Safety rules
- Cgroup paths must match `^[\w@.\-/]+$` before they go into the root script (prevents a fake scope name from injecting nft commands).
- Temp profile folders: random names (`tempfile.mkdtemp`) and permission `700`.
- One FlatNet at a time (`fcntl.flock` on `.lock`).
- After a cancelled password, no automatic retries (`self.denied`).

## 13. Keep code small
- Generic helpers (`read`, `run`, `write_json`, `table`, `fill`) are reused everywhere.
- Buttons are defined as lists of `(label, function)`.
- Result: one file, 406 lines (499 after the path cache and `--install-menu` were added).
