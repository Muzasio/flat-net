# 04. Testing methods

## Principle
Test each risky assumption with the smallest harmless experiment. Never test on the user's real data, and never send data to the internet during tests.

## Tests that were run

### 1. Syntax check
```bash
python3 -m py_compile flatnet.py
```
Run after every change. Result: passed every time.

### 2. Window smoke test (no clicks)
Create the window from a script, let it run, print the table rows, close it:
```bash
python3 -c "
import flatnet, time
w = flatnet.FlatNet()
end = time.time() + 3
while time.time() < end: w.update(); time.sleep(0.05)
print('ticks', w.ticks, 'helper', w.helper, 'applied', w.applied)
print([w.rtree.item(i)['values'] for i in w.rtree.get_children()])
w.destroy()"
```
Result (last run): `ticks 15` in 3 seconds (matches the 0.2 s loop), `helper None` (no password asked when nothing is blocked), 3 running apps listed (Firefox, Edge, Discord), helper sandboxes hidden.

For the PyQt6 version, the same idea was used with `QT_QPA_PLATFORM=offscreen`.

### 3. Profile location test
Goal: prove that an app writes its profile into the profile folder and **not** into its normal folder. Method:
- Use a test folder under `profiles/_test/`.
- Start the app **headless** (no window) and **with no network** (`--unshare=network`), through the same `--command=env` wrapper the program uses.
- List the test folder. Then use `find ~/.var/app/<id> -newer <marker file>` to check that the real folder did not change.

Commands used:
```bash
flatpak run --filesystem=$T --unshare=network --command=env com.google.Chrome XDG_CONFIG_HOME=$T/config ... chrome --headless --dump-dom about:blank
flatpak run --filesystem=$T --unshare=network --command=env org.mozilla.firefox XDG_CONFIG_HOME=$T/config ... firefox --headless --no-remote about:blank
flatpak run --filesystem=$T --unshare=network --command=env org.libreoffice.LibreOffice XDG_CONFIG_HOME=$T/config ... libreoffice -env:UserInstallation=file://$T/lo --headless --convert-to pdf --outdir $T $T/a.txt
```
Results:
| App | Result |
|---|---|
| Chrome | `config/google-chrome` created in the test folder |
| Firefox with `--profile <dir>/firefox` | that folder was not created |
| Firefox without `--profile` | profiles created in `config/mozilla/firefox` |
| LibreOffice, XDG only (`--terminate_after_init`, then `--convert-to`) | only `cache/fontconfig` created, no profile |
| LibreOffice with `-env:UserInstallation` | `lo/user/...` created |
| Real folders | No changes found by `find -newer` |

Falkon: only checked that `--command=env` sets `XDG_CONFIG_HOME` inside the sandbox (`cfg=/x/c`). A full Falkon window was not tested.

### 4. Flatpak behaviour tests
```bash
flatpak run --env=XDG_CONFIG_HOME=<dir> --command=sh org.kde.falkon -c 'echo cfg=$XDG_CONFIG_HOME; grep shared= /.flatpak-info'
```
Result: `cfg=~/.var/app/org.kde.falkon/config` (so `--env` was ignored) and `shared=ipc;network;`.

```bash
flatpak run --unshare=network --command=sh org.kde.falkon -c 'grep shared= /.flatpak-info'
```
Result: `shared=ipc;` (no `network`), which proved how to detect offline instances.

### 5. Default setting test
Called `set_default('Chrome', 'LAN only')`, read `defaults.json` (`{"Chrome": "lan"}`), switched the App dropdown to Falkon (Network showed `Online`). Then deleted `defaults.json` to reset.

### 6. Cleanup after tests
After each test: removed `profiles/_test`, checked `flatpak ps` for leftovers. One leftover headless Firefox was found and stopped with `systemctl --user stop app-flatpak-org.mozilla.firefox-<n>.scope`. Checked its command line first to make sure it was the test copy and not the user's Firefox.

## Tests that were NOT run
- **Live firewall test** (Cut, LAN only, counters). A test was prepared: one password prompt, apply a cut, move a small Python test process into the app's cgroup, try to connect to `1.1.1.1:443`, compare with a normal process, then remove the rule. The user rejected this test. Use SETUP.md Step 7 instead.
- Older Python, older kernels, other distros.
- Received counters on the input chain.

## How to test a similar project
1. Syntax check after every edit.
2. Smoke test the window from a script; print what it shows.
3. For anything that writes files: use a scratch folder and a "newer than marker" check on the real folder.
4. For anything that uses the network: run it with `--unshare=network` during tests.
5. For anything that needs root: ask the user before running it, and show exactly what will happen.
6. After each test: list running processes and leftover files, and clean them.
