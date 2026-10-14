# 05. Review and improvements

## Versions of the program

| Version | Folder | Toolkit | What it did |
|---|---|---|---|
| Commands | none | terminal | `flatpak run` commands plus `nft` commands typed by hand |
| Temp Chrome | `chrome-temp/chrome_temp.py` (deleted) | PyQt6 | 4 buttons for one Chrome profile; `pkexec` for every firewall action |
| FlatNet v1 | `flatnet/flatnet.py` | Tkinter | Any Flatpak app, profiles for 4 apps, `apps.json`, root helper, counters |
| FlatNet v2 | same | Tkinter | + Cut (switchable) mode, + default network per app |
| FlatNet v3 | same | Tkinter | Rewrite of the network part after the self-review (below) |

## Problems found while building v1

| Problem | Found by | Fix |
|---|---|---|
| `--env=XDG_CONFIG_HOME` ignored | Experiment (04-testing.md) | Start through `--command=env` |
| Firefox `--profile` folder not created | Profile location test | Removed `--profile` |
| LibreOffice profile not redirected | Profile location test | Added `-env:UserInstallation={url}/lo` |
| LAN-only rule could be up to 2 s late | Reading own code | Fast polling after launch (later replaced by the 0.2 s loop) |
| Keep button changed the profile's mode to whatever the dropdown showed | Reading own code | Keep only marks the profile as saved |

## The self-review (v2 to v3)

Method: read the whole file top to bottom, asking for each function: "What if this fails? What if the user cancels? What if an app starts or stops at this moment? What if two windows run? What text reaches the root helper?" Then the suspicious parts were checked on the real system.

| # | Problem | Kind | Fix |
|---|---|---|---|
| 1 | Cancelling the password still showed "Cut" in the list | Wrong state (privacy) | `apply()` only saves the new state if nft succeeds; the list shows `self.applied` |
| 2 | A block covered one instance; a new instance of the same app was online | Logic (privacy) | Blocks belong to groups (`app:<id>` or profile path) |
| 3 | LAN/Cut at launch could wait up to 2 s | Timing (privacy) | 0.2 s loop (`tick()`) |
| 4 | Two FlatNet windows would overwrite each other's rules | Logic | `.lock` file with `fcntl.flock` |
| 5 | Cgroup path text went into a root script unchecked | Security | `SAFE_PATH` check |
| 6 | Temp profile folders readable by other users | Security | Permission `700` |
| 7 | After a cancelled password, the error box could repeat every 2 s | Flow | `self.denied` stops automatic retries until a click |
| 8 | Two temp profiles in the same second crashed | Bug | Random names with `tempfile.mkdtemp` |
| 9 | Keep with an existing name crashed | Bug | Check before rename |
| 10 | Clicking an app that just closed crashed | Bug | Ignore selections that are no longer running |
| 11 | Chrome, Edge, Discord showed twice | Display | Hide helper sandboxes (`sandbox=true`) |

Investigation for #11: Edge had two instances. Reading `$XDG_RUNTIME_DIR/.flatpak/<instance>/info` showed the second one has `sandbox=true` and no network. Reading process command lines showed Chromium's network service runs in the **main** instance. So hiding the helper sandbox is safe, and cutting the main instance cuts Chromium's network.

## What made the biggest difference
1. **Truthful status** (#1). A privacy tool that shows the wrong status is worse than none.
2. **Group-based blocks** (#2). It matched how apps really behave (they start more processes and instances).
3. **Experiments on the real system** caught three wrong assumptions (`--env`, Firefox `--profile`, LibreOffice XDG) before the user hit them.
