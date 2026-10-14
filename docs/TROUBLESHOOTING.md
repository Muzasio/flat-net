# Troubleshooting, limits and fixes

Each entry says whether it was **seen** during this project or is **possible** (known from how the code works, but not seen).

## Emergency commands

Remove every FlatNet firewall rule (all apps get internet back):

```bash
sudo nft delete table inet flatnet
```

See what FlatNet has applied:

```bash
sudo nft list table inet flatnet
```

Remove "Always offline" from an app (example Discord):

```bash
flatpak override --user --share=network com.discordapp.Discord
```

Check the app's permanent Flatpak overrides:

```bash
flatpak override --user --show com.discordapp.Discord
```

Close one running Flatpak instance without touching other copies (replace the number with the **Instance** number from the table, and the ID with the app ID):

```bash
systemctl --user stop app-flatpak-com.google.Chrome-1234567890.scope
```

---

## Error messages shown by FlatNet

| Message | Cause | Fix |
|---|---|---|
| **FlatNet is already open.** | A second FlatNet was started. | Use the window that is already open. If none is visible, find it with `pgrep -af flatnet.py`. |
| **Select a running app first.** | You clicked Online/LAN only/Cut with no row selected in Running Flatpak apps. | Click a row first. |
| **Select a profile first.** | You clicked a Profiles button with no profile selected. | Click a profile row first. |
| **That profile is already running.** | You clicked Launch on a running profile. | Use the open app window. |
| **Not launched: the password is needed first for this network mode.** | LAN only or Cut needs the root helper before the app starts. The password was cancelled or failed. | Click again and type your password. Or choose Online or Offline (sandbox), which need no password. |
| **Firewall change failed, nothing was changed:** + `Password cancelled or root helper stopped.` | Password window was cancelled, or the root helper closed. | Click the button again. FlatNet does not retry by itself after a cancel. |
| **Firewall change failed, nothing was changed:** + `pkexec not found.` | pkexec is not installed. | Install polkit / pkexec (SETUP.md Step 2). |
| **Firewall change failed, nothing was changed:** + an `nft` error | The kernel or nftables refused the rules. Possible causes: an app closed at the same moment, the kernel does not support `socket cgroupv2`, or cgroup v1/hybrid mode. **Not seen during this project.** | Click again. If it repeats, run `uname -r`, `nft --version` and `stat -fc %T /sys/fs/cgroup` and compare with SETUP.md Step 1. |
| **Use only letters, numbers, dot, dash, underscore.** | The name typed in Keep (save) had other characters (for example a space). | Use a name like `work-falkon`. |
| **Kept, but close the app before renaming it.** | The profile was kept, but the folder cannot be renamed while the app uses it. | Close the app, select the profile, click Keep (save) again with the new name. |
| **Kept, but a profile named X already exists.** | Name already used for that app. | Pick another name. |
| **The app did not close. Nothing was deleted.** | After 10 seconds the app was still running. | Close the app window yourself, then click Delete again. |
| **Some files could not be deleted:** + path | Some files in the profile folder could not be removed (for example, permission problems). **Not seen.** | Check the folder with `ls -la <path>` and remove it by hand. |

## Problems without an error message

| Problem | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'tkinter'` in the terminal | Tkinter not installed. | Install it (SETUP.md Step 2). |
| The password window never appears, and the button seems frozen | No polkit agent is running (common on minimal window managers). **Not seen.** | Start a polkit agent for your desktop, or use a full desktop like KDE or GNOME. |
| Window freezes while the password window is open | Normal. FlatNet waits for the password answer. | Type or cancel the password. |
| Sent / Received show `-` | The root helper is not running yet. | Click **Data usage**. |
| Received stays at 0 or shows `-` while Sent works | The kernel may not support counting on incoming traffic; FlatNet then keeps only the outgoing chain. **Not seen.** | No fix needed; blocking still works. |
| An app is missing in Running Flatpak apps | It is not a Flatpak app, or it is an internal helper sandbox with no network (hidden on purpose), or its process path contained characters FlatNet refuses for safety. | Native (pacman/apt) apps cannot be controlled by FlatNet. |
| A profile shows `closed` while its app is open | FlatNet recognises a profile by finding the profile path in the app's process details. If those cannot be read, it cannot match. **Not seen** for Chrome, Firefox, LibreOffice. | Report it. Do not click Delete in this state. |
| A temp profile disappeared after closing the app | Normal: temp profiles are deleted when the app closes. | Click **Keep (save)** while the app is still open. |
| A temp profile was deleted while the app was still starting | The app took more than 30 seconds to show up as running. **Not seen.** | Start it again. |
| An app opened with Cut or LAN only still loaded something at the very start | The block is added about 0.2 seconds after the app appears. | Use **Offline (sandbox)** for zero leak. |
| An app started while FlatNet was closed is not blocked | Blocks for new copies are applied by the open FlatNet window. | Keep FlatNet open, or use **Always offline on/off** for a permanent block. |
| After clicking **Offline (sandbox)** with Set network, the app still shows Cut | A running app cannot be moved into the no-network sandbox. It is cut now, and starts in sandbox mode next time. | Close and relaunch the profile. |

## Known limits (by design)

1. **Only Flatpak apps.** Native apps (for example Brave and LibreWolf from pacman) do not get their own scope, so FlatNet cannot target them.
2. **Startup gap for LAN only and Cut.** About 0.2 seconds after the app appears. Offline (sandbox) has no gap.
3. **LAN only still allows local addresses (`127.0.0.0/8`).** That means:
   - the app can ask your PC's DNS service (for example `127.0.0.53`) to look up website names, so the names of sites it tries can reach your DNS server;
   - a proxy or VPN program running on your own PC could give it internet.
4. **Cut blocks local addresses too**, including `127.0.0.1`. Apps that talk to themselves over local network sockets may misbehave.
5. **Desktop requests are not blocked.** A cut app can still ask the desktop to do things through D-Bus, for example open a link in your default browser.
6. **Offline (sandbox) cannot be switched on while an app runs.** Restart needed.
7. **Data already sent cannot be taken back.** Deleting a profile removes local data only. Anything sent before the block (logins, sync) stays with that service.
8. **Data counters reset** if FlatNet restarts. Counters are kept in memory only.
9. **Only Cut was tested live** (Chrome, CachyOS). LAN only, switching back to Online and the counters were not. Use SETUP.md Step 7 on each PC.

## Things that were seen during the project

| What happened | Cause | What was done |
|---|---|---|
| `nft -c` (dry-run check) failed with `cache initialization failed: Operation not permitted` | `nft` needs root even for a dry-run check. | Rules could not be checked without a password. |
| `flatpak run --env=XDG_CONFIG_HOME=...` was ignored (app still saw `~/.var/app/.../config`) | Flatpak sets its own XDG variables after `--env`. | Start the app through `env` inside the sandbox instead (`--command=env`). |
| Firefox with `--profile <dir>/firefox` did not create that folder in a headless test | Not recorded. | Removed `--profile`; Firefox already follows `XDG_CONFIG_HOME`. |
| LibreOffice wrote no profile into the redirected config folder | Not recorded. | Added `-env:UserInstallation={url}/lo`. Then it worked. |
| A test Firefox stayed running in the background after a headless test | `timeout` stopped `flatpak run` but not the app inside. | Stopped it with `systemctl --user stop app-flatpak-org.mozilla.firefox-<n>.scope`. |
| Chrome/Edge/Discord appeared twice in the running list | Internal helper sandboxes (`sandbox=true`, no network). | They are now hidden. |
| `pacman` warning: `directive 'IgnorePkg' in section 'multilib' not recognized` | `IgnorePkg` line is in the wrong section of `/etc/pacman.conf`. Not related to FlatNet. | Move that line into `[options]`. |
