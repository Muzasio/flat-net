# 01. Timeline: how the project went

This is the real order of events, including wrong turns. Dates: everything happened on 2026-10-13 on one PC (CachyOS, KDE).

## Stage 0: Inventory (before this project)
A browser inventory was made in an earlier chat:
- Native (pacman): Brave (`brave-bin`), LibreWolf.
- Flatpak (system): Chrome, Edge, Falkon, Firefox.
- Leftover data folders from removed browsers (ChromeDev, ungoogled Chromium, Chromium, Flatpak LibreWolf, native Chrome folders).

## Stage 1: Commands only
**Goal stated by the user:** a 2nd Chrome profile, one command to open it, one to open it without internet.

First answer: `flatpak run com.google.Chrome --profile-directory="Profile 2"` and `flatpak run --unshare=network ...`.
Limit found: the network cut works only at launch, and a second launch can attach to an already running Chrome.

**Lesson:** the first one-line summary of the goal ("possible? yes, with one limit") was useful, because the user then corrected the goal.

## Stage 2: The real goal
The user clarified:
1. Real Chrome stays untouched.
2. A separate profile folder.
3. Cut internet **while the app is running**.
4. Delete the profile completely afterwards.

Research on the real PC (read-only commands): Chrome Flatpak permissions, `nft --version` (1.1.7), cgroup type (`cgroup2fs`), and `/proc/<pid>/cgroup` of a running Flatpak app. Finding: each Flatpak launch runs in its own systemd scope, for example
`user.slice/user-1000.slice/user@1000.service/app.slice/app-flatpak-org.mozilla.firefox-1026156536.scope`.
That made the plan possible: an nftables rule that matches one scope with `socket cgroupv2`.

Attempt to dry-run the rule with `nft -c` failed: it needs root.

The answer was a set of commands: separate `--user-data-dir`, nft rules, `rm -rf`.

## Stage 3: The user asked "is Flatpak the problem? Should we use the pacman browser?"
Answer: no. Flatpak helps, because each launch has its own scope. A native app would need to be started in its own scope (for example with `systemd-run --user --scope`).

## Stage 4: First GUI (Temp Chrome)
User asked for a click-only GUI. Built `chrome-temp/chrome_temp.py` with PyQt6: 4 buttons (Open, Cut, Restore, Close and delete). One `pkexec` password per firewall action.
Smoke test: opened offscreen, found the temp Chrome that the user had already started from Stage 2 commands.

## Stage 5: Live test refused, cleanup done
A live firewall test was prepared (password prompt plus moving a test process into the Chrome scope). **The user rejected it.** Then the user asked to delete the running profile: done with `systemctl --user stop <scope>` and `rm -rf` (the folder was 220 MB).

**Lesson:** ask before actions that pop up password prompts or change the firewall, even for testing.

## Stage 6: "What is possible for other Flatpak apps?"
Listed the 23 installed Flatpak apps and explained the options: live cut for any app, start offline, temp profiles (depends on the app), data counter, cut all, LAN only, always offline.

## Stage 7: FlatNet
User chose: Chrome, Firefox, Falkon, LibreOffice profiles, plus all extras, plus a **Keep (save)** option, "no hardcoded logic", "less code", and "runs on older distros".

Decisions (details in 03-design-decisions.md): Tkinter instead of PyQt6, `apps.json` for app list, one root helper per session, profiles inside the program folder.

Research by experiment (details in 02 and 04):
- `--env=XDG_CONFIG_HOME` is ignored by Flatpak, but `--command=env` works.
- Firefox follows XDG folders; `--profile` was dropped.
- LibreOffice needed `-env:UserInstallation`.

The old `chrome-temp` folder and its menu entry were removed and replaced by FlatNet.

## Stage 8: Offline vs switchable
User noticed: "Offline" could not be switched back without restarting. Added a fourth mode, **Cut (switchable)**, and renamed the old one **Offline (sandbox)**. Added **Default network per app** dropdowns (`defaults.json`), default Online.

## Stage 9: Self-review
User asked to check for security, bugs, logic and flow problems. Found 11 problems (see 05-review-and-improvements.md). Two could show "Cut" while the app was actually online. Investigated Edge's two instances and found internal helper sandboxes without network. Rewrote the network part around **groups**.

## Stage 10: Documentation
This folder.

## What was never done
- The firewall part was never tested live with a password (user rejected the test in Stage 5; no later test was recorded).
- No other distro was tested.
- Falkon was not opened as a full window with a redirected profile; only its environment redirect was checked.
