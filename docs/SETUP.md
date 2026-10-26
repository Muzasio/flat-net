# Set up FlatNet on a new Linux PC

Follow the steps in order. Each step has a command to run and the result you should see. If the result is different, stop and check [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

**Tested on:** CachyOS (Arch based), kernel 7.2.7, Python 3.14.7, Tk 8.6, Flatpak 1.18.4, nftables 1.1.7, KDE Plasma. **Other distros: not tested.** The package names below for other distros are the usual names, but they were not tested in this project.

**Shell note:** the commands are written for `bash`. If your terminal uses `fish` or `zsh` and a command fails, type `bash` and press Enter first, then run the command.

---

## Step 1: Check what the program needs

FlatNet needs all of these:

| Need | Why |
|---|---|
| Python 3.6 or newer | The program language |
| Tkinter (Python's window toolkit) | The window and buttons |
| Flatpak | Runs the apps |
| nftables (`nft` command) | The firewall used for LAN only, Cut and data counters |
| pkexec (part of polkit) | The password prompt for firewall changes |
| A polkit agent | Shows the password window. Desktops like KDE and GNOME already have one. |
| systemd and cgroup v2 | Each Flatpak app runs in its own "scope", which the firewall rules point to |
| Linux kernel with nftables socket cgroupv2 support | Matching firewall rules to one app. Older kernels may not support it; the minimum version was not recorded. |

Run these checks one at a time:

```bash
python3 --version
```
Expected: `Python 3.6` or higher, for example `Python 3.14.7`.

```bash
python3 -c "import tkinter; print('tk ok', tkinter.TkVersion)"
```
Expected: `tk ok 8.6`. If you see `ModuleNotFoundError`, install Tkinter (Step 2).

```bash
flatpak --version
```
Expected: something like `Flatpak 1.18.4`.

```bash
nft --version
```
Expected: something like `nftables v1.1.7`. If "command not found", install nftables (Step 2).

```bash
which pkexec
```
Expected: `/usr/bin/pkexec`.

```bash
stat -fc %T /sys/fs/cgroup
```
Expected: `cgroup2fs`. Any other answer means the PC uses cgroup v1 or hybrid mode. FlatNet's firewall features were only tested with `cgroup2fs`.

```bash
systemctl --user --version | head -1
```
Expected: `systemd` and a number.

## Step 2: Install what is missing

Pick your distro. Run only the line for your distro.

| Distro | Command (not tested except Arch/CachyOS) |
|---|---|
| Arch, CachyOS, Manjaro, EndeavourOS | `sudo pacman -S --needed python tk flatpak nftables polkit` |
| Debian 12+, Ubuntu 22.04+ | `sudo apt install python3 python3-tk flatpak nftables pkexec` |
| Older Debian/Ubuntu | `sudo apt install python3 python3-tk flatpak nftables policykit-1` |
| Fedora | `sudo dnf install python3 python3-tkinter flatpak nftables polkit` |
| openSUSE | `sudo zypper install python3 python3-tk flatpak nftables polkit` |

Note: installing the `nftables` package does **not** turn on any firewall by itself. FlatNet only adds its own table `inet flatnet`.

After installing, run Step 1 again until every check passes.

## Step 3: Add Flathub and install the apps (only the ones you want)

```bash
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
```

Install the apps that FlatNet has profiles for:

```bash
flatpak install flathub com.google.Chrome
flatpak install flathub org.mozilla.firefox
flatpak install flathub org.kde.falkon
flatpak install flathub org.libreoffice.LibreOffice
```

Check:

```bash
flatpak list --app
```
Expected: the apps you installed are in the list.

## Step 4: Copy the FlatNet folder

Copy the whole `flatnet` folder to the new PC. Any place inside your home folder works. This guide uses `~/Tools/flatnet` as the example.

What must be inside:

```bash
ls ~/Tools/flatnet
```
Expected: at least `apps.json`, `flatnet.py`, `docs`. The `profiles` folder is created by FlatNet when the first profile is made.

Do **not** copy `state.json` or `.lock` from the old PC. `state.json` holds instance numbers and folder paths that only mean something on the old PC.

Make the program runnable:

```bash
chmod +x ~/Tools/flatnet/flatnet.py
```

Folder paths with spaces or unusual characters were not tested.

## Step 5: First start

```bash
python3 ~/Tools/flatnet/flatnet.py
```

Expected: a window titled **FlatNet** opens. The **Running Flatpak apps** table lists your open Flatpak apps (it may be empty if none are open). No password is asked at this point.

## Step 6: Add FlatNet to the app menu

FlatNet writes the menu entry itself, using the folder and Python it finds when it runs. No path to type:

```bash
python3 ~/Tools/flatnet/flatnet.py --install-menu
```

Expected: `Menu entry written: ...applications/flatnet.desktop`. Search **FlatNet** in your app menu. On some desktops it takes a few seconds or a logout to appear.

If you move the FlatNet folder later, start it once from the new place (`python3 flatnet.py` inside the folder). It notices the move from its path cache and updates the menu entry and saved blocks. See [README.md, "Path cache"](README.md#path-cache-cacheflatnetpathsjson).

If you see *"The FlatNet path has characters a menu entry can't hold safely"*, move the folder to a path with only letters, numbers, spaces and `@ . + - _ /`.

## Step 7: Test that the firewall works

Do this once on every new PC. It is the only way to know the firewall part works there.

1. Open FlatNet.
2. In the top row choose **App: Firefox** (or any app you installed) and **Network: Online**. Click **New temp profile**.
3. When the app opens, load any website. It should work.
4. In **Running Flatpak apps**, select the row whose **Profile** column shows `Firefox/temp-...`. Click **Cut**. Type your password.
5. Expected: the **Network** column for that row shows `Cut (switchable)`.
6. In the app, load a different website. Expected: it does **not** load.
7. Check that another app (not cut) still loads websites.
8. Click **Online**. Expected: websites load again in the temp app.
9. In **Profiles**, select the temp profile and click **Delete**, then **Yes**.

You can also look at the firewall table directly:

```bash
sudo nft list table inet flatnet
```
Expected while something is cut: a rule with `socket cgroupv2 level ... drop`.

If step 4 shows an error or step 6 still loads pages, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## Instructions for an AI helper

If an AI assistant is helping you set this up, give it this summary:

- The program is `flatnet.py`, a single Python 3 file using only the standard library plus Tkinter. Do not install pip packages.
- Data paths are relative to the folder that contains `flatnet.py`.
- Required system commands: `flatpak`, `nft`, `pkexec`, `systemctl --user`. Required: cgroup v2 (`stat -fc %T /sys/fs/cgroup` prints `cgroup2fs`).
- Run Step 1 checks, install missing packages for the distro, then run Step 5 and Step 7.
- Never edit `/etc/nftables.conf` for this program. FlatNet manages only the table `inet flatnet` at runtime.
