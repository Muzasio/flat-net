# 02. Research and sources

## How research was done

**No web pages were opened during this project.** All research was one of these two kinds:

1. **Asking the real system** with harmless, read-only commands. This was the main method. The PC itself is the most reliable source, because it shows the exact versions in use.
2. **Prior knowledge** of how Flatpak, systemd, cgroups and nftables work (known before the project, not from pages read during it). Every important piece of prior knowledge was then checked on the real system, as listed below.

Rule followed: **a fact goes into the code only after the real system confirms it.**

## Sources used, and what each one told us

| Source (command or file) | What it showed | Used for |
|---|---|---|
| `hostname` | the PC's name (not recorded here: personal) | Confirming the commands run on the main PC |
| `flatpak info --show-permissions com.google.Chrome` | `shared=ipc;network;`, file access only to some XDG folders (Downloads, Documents, ...), not to `~/Tools` | Knowing that a profile folder outside `~/.var/app` needs `--filesystem=<path>` |
| `nft --version` | `nftables v1.1.7` | nftables is installed and new enough for `socket cgroupv2` |
| `stat -fc %T /sys/fs/cgroup` | `cgroup2fs` | cgroup v2 is in use |
| `flatpak ps` | instance number, PID, app ID for each running app | Learning that each run has an instance number |
| `cat /proc/<pid>/cgroup` | `0::/user.slice/.../app-flatpak-<id>-<instance>.scope` | The exact path that the firewall rule must match |
| `ls -l /sys/fs/cgroup/<scope>/cgroup.procs` | Owned by the user | The user can see (and systemd can stop) each scope |
| `python3 -c "import PyQt6"` etc. | tkinter, PyQt6, PySide6 all available | Choosing a GUI toolkit |
| `flatpak run org.kde.falkon --help` | `-r, --no-remote  Starts new browser instance.` and `-p --profile <profileName>` (name, not path) | Falkon needs `--no-remote`; its profile can only be moved with XDG folders |
| `flatpak override --user --show <id>` | Prints nothing when there are no overrides | How to detect "Always offline" (`unshared=...network`) |
| `flatpak --version` | `Flatpak 1.18.4` | Version record |
| `flatpak info -m <id>` | `command=falkon`, `command=chrome`, `command=firefox`, `command=libreoffice` | Finding each app's start command without hardcoding it |
| `flatpak list --app --columns=application,name` | 23 apps | Explaining what is possible |
| `$XDG_RUNTIME_DIR/.flatpak/<instance>/info` | Main instance: `shared=ipc;network;`. Helper sandbox: `sandbox=true` and no `shared=` line | Detecting offline instances and hiding helper sandboxes |
| `/proc/<pid>/root/.flatpak-info` | `shared=ipc;network;` normally, `shared=ipc;` with `--unshare=network` | Fallback for detecting offline instances |
| `/proc/<pid>/cmdline` of Edge processes | Main instance holds `network.mojom.NetworkService`; the helper sandbox holds renderers | Knowing that cutting the main instance cuts Chromium's network |
| `/proc/<pid>/environ` of Edge helper sandbox | No `XDG_CONFIG_HOME`; some files "permission denied" | Helper sandboxes cannot be matched to a profile, so they must be handled another way (hidden, since they have no network) |

## Findings (short)

1. **Each Flatpak launch has its own systemd scope** (`app-flatpak-<id>-<instance>.scope`). This is the key fact that makes live per-app blocking possible.
2. **nftables can match traffic by cgroup** with `socket cgroupv2 level <n> "<path>"`. The level is the number of parts in the path (5 on this PC).
3. **`nft` needs root even for `-c` (check only).**
4. **`flatpak run --env=` cannot change `XDG_CONFIG_HOME`.** Flatpak sets it afterwards.
5. **`flatpak run --command=env <id> XDG_CONFIG_HOME=... <command>` works.** The `env` program inside the sandbox sets the variables, then starts the app.
6. **Chrome** puts its profile in `$XDG_CONFIG_HOME/google-chrome`.
7. **Firefox (this version)** puts its profile in `$XDG_CONFIG_HOME/mozilla/firefox`.
8. **LibreOffice** did not use the redirected config folder in the tests; `-env:UserInstallation=file://...` worked.
9. **Running apps cannot get `--unshare=network` later.** The sandbox's network is decided at launch.
10. **Chromium and Electron apps start helper sandboxes** as separate Flatpak instances (`sandbox=true`) with no network.
11. **`systemctl --user stop <scope>`** closes exactly one Flatpak instance and nothing else.

## How to research the next project the same way

1. Write down every assumption your plan depends on ("each app has its own cgroup").
2. For each one, find a read-only command that proves or disproves it on the real PC.
3. Run it. Record the exact output.
4. If a test must write something, write into a scratch folder and use `--unshare=network` so nothing leaves the PC.
5. Only then write code that depends on it.
