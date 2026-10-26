# FlatNet user guide

This guide covers every button and every automatic action in FlatNet.

## Starting FlatNet

- From the app menu: search **FlatNet**.
- From a terminal, inside the FlatNet folder: `python3 flatnet.py`

Only one FlatNet window can be open. If you start a second one, it shows **"FlatNet is already open."** and closes.

## Words used in this guide

| Word | Meaning |
|---|---|
| **Flatpak app** | An app installed with Flatpak. Only Flatpak apps can be controlled. |
| **Instance** | One running copy of a Flatpak app. Each copy has its own number (instance ID). |
| **Profile** | A separate data folder for an app (its own cookies, history, settings). |
| **Temp profile** | A profile that is deleted automatically when its app closes. |
| **Kept profile** | A profile you saved with **Keep (save)**. It is never deleted automatically. |
| **Group** | What a block applies to: either one normal app (all its copies) or one profile. |
| **Root helper** | A small background process with admin rights that changes the firewall. Starting it asks for your password once per session. |

## The four network modes

| Mode | Internet | Home network (LAN) | Change while running? | How it works |
|---|---|---|---|---|
| **Online** | Yes | Yes | Yes | No rule. |
| **LAN only** | No | Yes | Yes | Firewall rule. Blocks every address that is not a local/private address. |
| **Cut (switchable)** | No | No | Yes | Firewall rule. Blocks all outgoing traffic of that app. |
| **Offline (sandbox)** | No | No | **No** | The app starts with no network at all (`flatpak run --unshare=network`). |

Which one to pick:
- Want **zero leak**, even in the first second? Use **Offline (sandbox)**. To get internet back you must close and reopen the app.
- Want to **switch later without restarting**? Use **Cut (switchable)** or **LAN only**. These start a split second after the app appears, so the very first moment of the app is not covered.

"Home network" means these addresses: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`, `169.254.0.0/16`, `224.0.0.0/4`, `255.255.255.255`, and for IPv6 `::1`, `fc00::/7`, `fe80::/10`, `ff00::/8`.

---

## Window layout

The window has four parts, top to bottom:
1. **Default network per app** box
2. **Top row**: App, Network, New temp profile
3. **Profiles** table with its buttons
4. **Running Flatpak apps** table with its buttons

A line at the bottom reminds you: *"Network changes ask for your password once per session. Keep this window open: it applies blocks to newly started apps."*

---

## 1. Default network per app

One dropdown per app from `apps.json` (Chrome, Falkon, Firefox, LibreOffice).

- Every app starts as **Online**.
- Change a dropdown and it is saved at once in `defaults.json`.
- The default is used for **new temp profiles** of that app. When you choose that app in the top row, the **Network** dropdown fills in with its default.

## 2. Top row

| Control | What it does |
|---|---|
| **App** dropdown | Choose which app the next temp profile is for. Choosing an app also sets **Network** to that app's default. Also used by **Always offline on/off** when no running app is selected. |
| **Network** dropdown | The network mode for the next action: used by **New temp profile** and **Set network**. You can change it just for this time; the default stays the same. |
| **New temp profile** | Creates a new temp profile folder for the chosen app and starts the app with it, using the chosen network mode. |

What **New temp profile** does, step by step:
1. Creates `profiles/<App>/temp-<random>/` with permission `700` (only your user can open it).
2. Writes `profile.json` with the chosen mode and `"saved": false`.
3. If the mode is **LAN only** or **Cut (switchable)**: asks for the password first. If you cancel, you see *"Not launched: the password is needed first for this network mode."* and the app is not started (the empty folder is removed about 30 seconds later).
4. Starts the app. The app keeps all its data inside the profile's `data/` folder.
5. If the mode is LAN only or Cut, the block is applied within about 0.2 seconds of the app appearing.

## 3. Profiles table

Columns:

| Column | Meaning |
|---|---|
| App | Which app the profile belongs to |
| Profile | Folder name of the profile |
| Network | The mode saved for this profile (used when it starts) |
| Kept | `yes` = kept, `no` = temp (auto-deleted when closed) |
| Status | `running` or `closed` |

Buttons (select a profile row first):

| Button | What it does |
|---|---|
| **Launch** | Starts the app with this profile, using the profile's saved **Network** mode. If it is already running you get *"That profile is already running."* Same password rule as above for LAN only and Cut. |
| **Set network** | Saves the mode from the top **Network** dropdown into this profile. If the profile is running and has network, the change is applied right away. If you choose **Offline (sandbox)** while it runs, it is **cut** now (a running app cannot enter the sandbox) and starts in sandbox mode next time. |
| **Keep (save)** | Asks for a name. Allowed characters: letters, numbers, `.`, `-`, `_`. Marks the profile as kept so it is never auto-deleted. Renaming only happens if the app is closed and the name is free; otherwise it is kept under the old name and you see a message. |
| **Delete** | Asks *"Close this profile's app and delete it forever?"*. If yes: closes only that profile's app, waits up to 10 seconds, then deletes the whole profile folder and removes its block. If the app does not close you see *"The app did not close. Nothing was deleted."* |

**Automatic temp cleanup:** a temp profile (Kept = `no`) is deleted when its app is not running and it was not launched in the last 30 seconds. This also happens when FlatNet starts, for old temp profiles. To keep a temp profile, click **Keep (save)** **while the app is still open**.

## 4. Running Flatpak apps table

Shows every running Flatpak app, not only the ones FlatNet started.

| Column | Meaning |
|---|---|
| App | Flatpak app ID, for example `com.discordapp.Discord` |
| Instance | The instance number of that running copy |
| Profile | `<App>/<name>` if it is a FlatNet profile, else `normal` |
| Network | The mode that is **really active** in the firewall right now. `Offline (sandbox)` means it was started without network. |
| Sent | Data this instance has sent (shows `-` until the root helper is running) |
| Received | Data this instance has received (shows `-` until the root helper is running) |

Hidden on purpose: Chromium-based apps (Chrome, Edge) and Electron apps (Discord) start internal helper sandboxes that have no network. They are not shown.

Buttons (select one or more rows first, except "all" buttons):

| Button | What it does |
|---|---|
| **Online** | Removes the block from the selected app's group. |
| **LAN only** | Allows only home-network addresses for the selected app's group. |
| **Cut** | Blocks all network for the selected app's group. |
| **Cut all** | Blocks every running app that has network. |
| **Restore all** | Removes every block. |
| **Always offline on/off** | Permanent setting in Flatpak itself. Turns network off (or back on) for the selected app, or, if nothing is selected, for the app in the top **App** dropdown. Takes effect from the app's **next start**. Runs `flatpak override --user --unshare=network <id>` (on) or `--share=network` (off). |
| **Data usage** | Starts the root helper (password) so the Sent and Received columns start counting. |

**Important about groups:** a block covers the whole group, not only the row you clicked.
- Normal app: cutting Discord cuts every Discord copy, including copies started later (while FlatNet is open).
- Profile: cutting a profile cuts only that profile, not the normal app.

If a firewall change fails you see *"Firewall change failed, nothing was changed:"* plus the error. The table keeps showing the real state.

## Automatic actions (no click needed)

| When | What happens |
|---|---|
| Every 0.2 s | FlatNet looks for new app instances. If one belongs to a blocked group, the block is applied at once. |
| Every 2 s | Window redraws, counters update, closed temp profiles are deleted. |
| On start | If `state.json` lists blocks, FlatNet asks for the password to re-apply them. |
| After you cancel a password | FlatNet stops asking automatically until you click a button. |

## Closing FlatNet

- Blocks already applied **stay on** after you close the window.
- Apps started **while FlatNet is closed** are **not** blocked. When you open FlatNet again, it re-applies saved blocks.
- To remove all blocks: click **Restore all** before closing, or see TROUBLESHOOTING.md for the emergency command.

## Add another app (for profiles)

1. Find the app ID: `flatpak list --app`
2. Open `apps.json` and add a line. Example for Microsoft Edge (**not tested**):
   ```json
   "Edge": {"id": "com.microsoft.Edge", "args": ["--no-first-run"]}
   ```
   Put a comma after the line before it. Use `{dir}` or `{url}` in `args` if the app needs a profile path.
3. Restart FlatNet.

How to check that a new app really keeps its data in the profile folder: start it once with a test profile, close it, then run `find ~/.var/app/<app id> -newer <marker file>` (make the marker with `touch` just before). No output means the app's normal folder was not touched.

Internet control (the Running table) does **not** need `apps.json`. It works for every Flatpak app.
