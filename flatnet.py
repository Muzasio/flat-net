#!/usr/bin/env python3
"""FlatNet: per-app internet control and throwaway profiles for Flatpak apps.

Apps for profiles come from apps.json. Profiles live in ./profiles/<app>/<name>/.
Network control works on any running Flatpak app (each run has its own cgroup scope).
Blocks are kept per group (one profile, or one normal app), so new instances inherit them.
Needs: python3 (3.6+) with tkinter, flatpak, nftables, pkexec, cgroup v2.
"""
import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from subprocess import DEVNULL, PIPE, STDOUT
from tkinter import messagebox, simpledialog, ttk
from urllib.request import pathname2url

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILES = os.path.join(HERE, "profiles")
STATE = os.path.join(HERE, "state.json")
DEFAULTS = os.path.join(HERE, "defaults.json")
RUNTIME = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid()


def xdg(var, fallback):
    path = os.environ.get(var, "")
    return path if os.path.isabs(path) else os.path.expanduser(fallback)  # the XDG spec says ignore relative paths


# Paths are found at runtime and remembered in a private cache, never written into the code or docs.
CACHE = os.path.join(xdg("XDG_CACHE_HOME", "~/.cache"), "flatnet")
PATHS = os.path.join(CACHE, "paths.json")
MENU = os.path.join(xdg("XDG_DATA_HOME", "~/.local/share"), "applications", "flatnet.desktop")
MENU_MARK = "X-FlatNet-Managed=true"
SAFE_EXEC = re.compile(r"^[\w@.+/ -]+$")  # anything else would need desktop-file escaping, so it is refused
with open(os.path.join(HERE, "apps.json")) as _f:
    APPS = json.load(_f)

# lan/cut are firewall rules (switchable while running); offline is a no-network sandbox (zero leak, restart to change).
MODES = {"online": "Online", "lan": "LAN only", "cut": "Cut (switchable)", "offline": "Offline (sandbox)"}
SCOPE_RE = re.compile(r"app-flatpak-(.+)-(\d+)\.scope$")
SAFE_PATH = re.compile(r"^[\w@.\-/]+$")  # cgroup paths go into a root nft script, so nothing that could break quoting
LAN_ONLY = ("ip daddr != { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8, "
            "169.254.0.0/16, 224.0.0.0/4, 255.255.255.255 }",
            "ip6 daddr != { ::1, fc00::/7, fe80::/10, ff00::/8 }")
# Root side: runs nft scripts sent one JSON line at a time, so one password covers the session.
HELPER = ("import sys,json,subprocess\n"
          "for l in iter(sys.stdin.readline,''):\n"
          " r=subprocess.run(['nft','-f','-'],input=json.loads(l),stdout=subprocess.PIPE,"
          "stderr=subprocess.STDOUT,universal_newlines=True)\n"
          " print(json.dumps([r.returncode==0,r.stdout]),flush=True)\n")


def read(path, binary=False):
    try:
        with open(path, "rb" if binary else "r") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return b"" if binary else ""


def write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)


def run(*cmd):
    return subprocess.run(cmd, stdout=PIPE, stderr=STDOUT, universal_newlines=True).stdout


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.0f %s" % (n, unit)
        n /= 1024.0
    return "%.1f TB" % n


def scan():
    """Running Flatpak instances: {instance: {id, path, blob, offline}}. Network-less sub-sandboxes are left out."""
    found = {}
    for pid in filter(str.isdigit, os.listdir("/proc")):
        cg = [l[3:].lstrip("/") for l in read("/proc/%s/cgroup" % pid).splitlines() if l.startswith("0::")]
        m = SCOPE_RE.search(cg[0]) if cg else None
        if not m or not SAFE_PATH.match(cg[0]):
            continue
        k = m.group(2)
        if k not in found:
            info = read("%s/.flatpak/%s/info" % (RUNTIME, k)) or read("/proc/%s/root/.flatpak-info" % pid)
            shared = re.search(r"^shared=(.*)$", info, re.M)
            found[k] = {"id": m.group(1), "path": cg[0], "blob": b"",
                        "sub": "sandbox=true" in info,
                        "offline": bool(info) and "network" not in (shared.group(1) if shared else "")}
        found[k]["blob"] += read("/proc/%s/cmdline" % pid, True) + read("/proc/%s/environ" % pid, True)
    return {k: i for k, i in found.items() if not i["sub"]}


def load_profiles():
    res = []
    for app in sorted(os.listdir(PROFILES)) if os.path.isdir(PROFILES) else []:
        for name in sorted(os.listdir(os.path.join(PROFILES, app))):
            d = os.path.join(PROFILES, app, name)
            meta = {"mode": "online", "saved": False}
            meta.update(json.loads(read(d + "/profile.json") or "{}"))
            res.append(dict(meta, app=app, name=name, dir=d))
    return res


def save_meta(p):
    write_json(p["dir"] + "/profile.json", {"mode": p["mode"], "saved": p["saved"]})


# ---- paths found at runtime ----
def current_paths():
    return {"root": HERE, "script": os.path.abspath(__file__), "python": sys.executable or "python3"}


def private_dir(path):
    """Create `path` as a 0700 folder; refuse it if it is a symlink or belongs to someone else."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise OSError("Unsafe cache folder (symlink or not yours): " + path)
    os.chmod(path, 0o700)


def write_private(path, text):
    """Atomic write with mode 0600: a crash never leaves half a file."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def cached_paths():
    try:
        data = json.loads(read(PATHS) or "{}")
    except ValueError:
        return {}  # damaged cache: treated as a first run
    return {k: v for k, v in data.items() if isinstance(v, str) and os.path.isabs(v)} if isinstance(data, dict) else {}


def menu_entry(paths):
    if not all(SAFE_EXEC.match(paths[k]) for k in ("python", "script")):
        raise OSError("The FlatNet path has characters a menu entry can't hold safely: " + paths["script"])
    quote = lambda s: '"%s"' % s if " " in s else s
    return ("[Desktop Entry]\nType=Application\nName=FlatNet\n"
            "Comment=Internet control and throwaway profiles for Flatpak apps\n"
            "Exec=%s %s\nIcon=network-wired\nTerminal=false\nCategories=Network;\n%s\n"
            % (quote(paths["python"]), quote(paths["script"]), MENU_MARK))


def install_menu():
    os.makedirs(os.path.dirname(MENU), exist_ok=True)
    with open(MENU, "w") as f:
        f.write(menu_entry(current_paths()))
    print("Menu entry written: " + MENU)


def remember_paths():
    """Write the private path cache once per machine: on the first start (no cache yet). Later starts only
    read it. It is written again only if the folder was moved; then saved blocks and the menu entry follow it."""
    old, new = cached_paths(), current_paths()
    if old == new:
        return  # known machine, same folder: nothing is written
    moved = old.get("root")
    if moved and moved != HERE and os.path.exists(STATE):
        prefix = os.path.join(moved, "profiles") + "/"
        net = json.loads(read(STATE) or "{}")
        write_json(STATE, {(PROFILES + "/" + g[len(prefix):] if g.startswith(prefix) else g): m for g, m in net.items()})
    if MENU_MARK in read(MENU):  # only an entry made by --install-menu, never one the user wrote
        with open(MENU, "w") as f:
            f.write(menu_entry(new))
    private_dir(CACHE)
    write_private(PATHS, json.dumps(new, indent=1))


class FlatNet(tk.Tk):
    def __init__(self):
        tk.Tk.__init__(self)
        self.title("FlatNet")
        self.helper, self.denied, self.applied = None, False, None
        self.net = json.loads(read(STATE) or "{}")  # group (profile dir or "app:<id>") -> "lan" | "cut"
        self.defaults = json.loads(read(DEFAULTS) or "{}")  # app -> mode; missing means online
        self.totals, self.counts, self.launched, self.insts, self.profs, self.owner = {}, {}, {}, {}, [], {}
        self.ticks = 0

        box = ttk.LabelFrame(self, text="Default network per app (used for new profiles)", padding=6)
        box.pack(fill="x", padx=6, pady=4)
        for col, app in enumerate(sorted(APPS)):
            var = tk.StringVar(value=MODES[self.defaults.get(app, "online")])
            var.trace_add("write", lambda *_, app=app, var=var: self.set_default(app, var.get()))
            ttk.Label(box, text=app).grid(row=0, column=col, sticky="w", padx=4)
            ttk.Combobox(box, textvariable=var, values=list(MODES.values()), state="readonly", width=16).grid(
                row=1, column=col, padx=4)

        top = ttk.Frame(self, padding=6)
        top.pack(fill="x")
        self.app_var = tk.StringVar(value=sorted(APPS)[0])
        self.mode_var = tk.StringVar()
        self.app_var.trace_add("write", lambda *_: self.mode_var.set(MODES[self.defaults.get(self.app_var.get(), "online")]))
        self.app_var.set(self.app_var.get())
        ttk.Label(top, text="App").pack(side="left")
        ttk.Combobox(top, textvariable=self.app_var, values=sorted(APPS), state="readonly", width=12).pack(side="left", padx=4)
        ttk.Label(top, text="Network").pack(side="left")
        ttk.Combobox(top, textvariable=self.mode_var, values=list(MODES.values()), state="readonly", width=16).pack(side="left", padx=4)
        ttk.Button(top, text="New temp profile", command=self.new_temp).pack(side="left", padx=4)

        self.ptree = self.table("Profiles (temp ones are deleted when the app closes, unless kept)",
                                ("App", "Profile", "Network", "Kept", "Status"), [
                                    ("Launch", self.launch_sel), ("Set network", self.set_profile_mode),
                                    ("Keep (save)", self.keep), ("Delete", self.delete)])
        self.rtree = self.table("Running Flatpak apps (a block covers the whole app or profile, also future windows)",
                                ("App", "Instance", "Profile", "Network", "Sent", "Received"), [
                                    ("Online", lambda: self.set_net("online")), ("LAN only", lambda: self.set_net("lan")),
                                    ("Cut", lambda: self.set_net("cut")), ("Cut all", lambda: self.set_net("cut", True)),
                                    ("Restore all", lambda: self.set_net("online", True)),
                                    ("Always offline on/off", self.always_offline), ("Data usage", self.user_apply)])
        ttk.Label(self, padding=6, text="Network changes ask for your password once per session. "
                                        "Keep this window open: it applies blocks to newly started apps.").pack(fill="x")
        self.tick()

    def table(self, title, cols, buttons):
        box = ttk.LabelFrame(self, text=title, padding=6)
        box.pack(fill="both", expand=True, padx=6, pady=4)
        tree = ttk.Treeview(box, columns=cols, show="headings", height=6)
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=110)
        tree.pack(fill="both", expand=True)
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(4, 0))
        for text, cmd in buttons:
            ttk.Button(row, text=text, command=cmd).pack(side="left", padx=2)
        return tree

    # ---- root helper and firewall ----
    def nft(self, script):
        try:
            if self.helper is None or self.helper.poll() is not None:
                self.helper = subprocess.Popen(["pkexec", "python3", "-c", HELPER], stdin=PIPE, stdout=PIPE,
                                               universal_newlines=True, bufsize=1)
            self.helper.stdin.write(json.dumps(script) + "\n")
            self.helper.stdin.flush()
            line = self.helper.stdout.readline()
        except (OSError, ValueError) as e:
            line = "" if not isinstance(e, FileNotFoundError) else json.dumps([False, "pkexec not found."])
        if not line:
            self.helper, self.denied = None, True  # don't re-ask on every tick after a cancel
            return False, "Password cancelled or root helper stopped."
        return json.loads(line)

    def read_counters(self):
        ok, out = self.nft("list table inet flatnet")
        return {k: int(b) for b, k in re.findall(r'bytes (\d+) comment "([io]\d+)"', out)} if ok else {}

    def group(self, k):
        return self.owner.get(k) or "app:" + self.insts[k]["id"]

    def desired(self, net):
        return frozenset((k, net.get(self.group(k), "online")) for k, i in self.insts.items() if not i["offline"])

    def apply(self, net=None):
        """Rebuild the firewall table for `net` (default: current). The new state counts only if nft succeeds."""
        net = dict(self.net if net is None else net)
        want = self.desired(net)
        out, inn = [], []
        for k, mode in sorted(want):
            path = self.insts[k]["path"]
            match = 'socket cgroupv2 level %d "%s"' % (path.count("/") + 1, path)
            if mode == "cut":
                out.append(match + " drop")
            elif mode == "lan":
                out += [match + " " + rule + " drop" for rule in LAN_ONLY]
            out.append(match + ' counter comment "o%s"' % k)
            inn.append(match + ' counter comment "i%s"' % k)
        before = self.read_counters() if self.helper else {}
        head = "table inet flatnet\ndelete table inet flatnet\ntable inet flatnet {\n"
        chain = " chain %s {\n  type filter hook %s priority 0; policy accept;\n  %s\n }\n"
        ok, err = self.nft(head + chain % ("out", "output", "\n  ".join(out)) + chain % ("inn", "input", "\n  ".join(inn)) + "}\n")
        if not ok and self.helper:  # older kernels may not match sockets on input; keep the blocking part
            ok, err = self.nft(head + chain % ("out", "output", "\n  ".join(out)) + "}\n")
        if not ok:
            self.denied = True  # no automatic retries until the user clicks something
            messagebox.showerror("FlatNet", "Firewall change failed, nothing was changed:\n" + err)
            return False
        for key, b in before.items():
            self.totals[key] = self.totals.get(key, 0) + b
        self.net, self.applied = {g: m for g, m in net.items() if m in ("lan", "cut")}, want
        write_json(STATE, self.net)
        return True

    def user_apply(self, net=None):
        self.denied = False
        self.apply(net)
        self.refresh()

    def set_net(self, mode, everyone=False):
        keys = [k for k, i in self.insts.items() if not i["offline"] and (everyone or k in self.rtree.selection())]
        if not keys:
            return messagebox.showinfo("FlatNet", "Select a running app first.")
        net = {} if everyone and mode == "online" else dict(self.net)
        for k in keys:
            net[self.group(k)] = mode
        self.user_apply(net)

    def always_offline(self):
        sel = self.rtree.selection()
        app_id = self.insts[sel[0]]["id"] if sel and sel[0] in self.insts else APPS[self.app_var.get()]["id"]
        on = re.search(r"^unshared=.*network", run("flatpak", "override", "--user", "--show", app_id), re.M)
        run("flatpak", "override", "--user", "--share=network" if on else "--unshare=network", app_id)
        messagebox.showinfo("FlatNet", "%s is now %s.\nApplies from its next start." % (
            app_id, "allowed internet again" if on else "ALWAYS offline"))

    # ---- profiles ----
    def running_insts(self, p):
        return [k for k, d in self.owner.items() if d == p["dir"]]

    def selected_profile(self):
        sel = self.ptree.selection()
        p = next((p for p in self.profs if p["dir"] == (sel[0] if sel else None)), None)
        if p is None:
            messagebox.showinfo("FlatNet", "Select a profile first.")
        return p

    def mode_key(self, label=None):
        return next(k for k, v in MODES.items() if v == (label or self.mode_var.get()))

    def set_default(self, app, label):
        self.defaults[app] = self.mode_key(label)
        write_json(DEFAULTS, self.defaults)
        if app == self.app_var.get():
            self.mode_var.set(label)

    def new_temp(self):
        app = self.app_var.get()
        os.makedirs(os.path.join(PROFILES, app), exist_ok=True)
        d = tempfile.mkdtemp(prefix="temp-", dir=os.path.join(PROFILES, app))
        os.chmod(d, 0o700)
        p = {"app": app, "name": os.path.basename(d), "dir": d, "saved": False, "mode": self.mode_key()}
        save_meta(p)
        self.launched[d] = time.time()  # protects the empty folder from cleanup if launch is cancelled below
        self.launch(p)

    def launch_sel(self):
        p = self.selected_profile()
        if p and self.running_insts(p):
            messagebox.showinfo("FlatNet", "That profile is already running.")
        elif p:
            self.launch(p)

    def launch(self, p):
        a, data, mode = APPS[p["app"]], p["dir"] + "/data", p["mode"]
        if mode in ("lan", "cut"):
            self.denied = False
            if not self.nft("list tables")[0]:
                return messagebox.showerror("FlatNet", "Not launched: the password is needed first for this network mode.")
        self.net[p["dir"]] = mode  # picked up by the 200 ms tick the moment the app's first process appears
        os.makedirs(data, exist_ok=True)
        cmd = a.get("command") or re.search(r"^command=(.*)$", run("flatpak", "info", "-m", a["id"]), re.M).group(1)
        env = ["XDG_%s_HOME=%s/%s" % (x, data, x.lower()) for x in ("CONFIG", "DATA", "CACHE", "STATE")]
        args = [x.format(dir=data, url="file://" + pathname2url(data)) for x in a.get("args", [])]
        net = ["--unshare=network"] if mode == "offline" else []
        subprocess.Popen(["flatpak", "run", "--filesystem=" + data] + net + ["--command=env", a["id"]] + env + [cmd] + args,
                         stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL, start_new_session=True)
        self.launched[p["dir"]] = time.time()

    def set_profile_mode(self):
        p = self.selected_profile()
        if not p:
            return
        p["mode"] = self.mode_key()
        save_meta(p)
        if any(not self.insts[k]["offline"] for k in self.running_insts(p)):
            # A running app can't enter the sandbox, so offline means cut until its next start.
            self.user_apply(dict(self.net, **{p["dir"]: "cut" if p["mode"] == "offline" else p["mode"]}))
        self.refresh()

    def keep(self):
        p = self.selected_profile()
        if not p:
            return
        name = simpledialog.askstring("Keep profile", "Name for this profile:", initialvalue=p["name"], parent=self)
        if not name:
            return
        if not re.match(r"^[\w.-]+$", name):
            return messagebox.showerror("FlatNet", "Use only letters, numbers, dot, dash, underscore.")
        p["saved"] = True
        save_meta(p)
        new = os.path.join(PROFILES, p["app"], name)
        if name != p["name"]:
            if self.running_insts(p):
                messagebox.showinfo("FlatNet", "Kept, but close the app before renaming it.")
            elif os.path.exists(new):
                messagebox.showinfo("FlatNet", "Kept, but a profile named %s already exists." % name)
            else:
                os.rename(p["dir"], new)
                if p["dir"] in self.net:
                    self.net[new] = self.net.pop(p["dir"])
        self.refresh()

    def delete(self):
        p = self.selected_profile()
        if not p or not messagebox.askyesno("FlatNet", "Close this profile's app and delete it forever?\n\n" + p["dir"]):
            return
        for k in self.running_insts(p):  # stops only that instance's scope
            run("systemctl", "--user", "stop", self.insts[k]["path"].rsplit("/", 1)[1])
        for _ in range(50):
            self.scan_all()
            if not self.running_insts(p):
                break
            time.sleep(0.2)
        else:
            return messagebox.showerror("FlatNet", "The app did not close. Nothing was deleted.")
        shutil.rmtree(p["dir"], ignore_errors=True)
        self.net.pop(p["dir"], None)
        self.refresh()
        if os.path.exists(p["dir"]):
            messagebox.showerror("FlatNet", "Some files could not be deleted:\n" + p["dir"])

    # ---- loop ----
    def scan_all(self):
        self.insts, self.profs = scan(), load_profiles()
        tags = [(p["dir"], (p["dir"] + "/").encode()) for p in self.profs]
        self.owner = {k: d for k, i in self.insts.items() for d, tag in tags if tag in i["blob"]}

    def tick(self):
        """Every 200 ms: find new instances and block them at once if their group is blocked. Every 2 s: redraw."""
        self.scan_all()
        want = self.desired(self.net)
        blocked = any(m != "online" for _, m in want)
        if want != self.applied and (self.helper or blocked) and not self.denied:
            self.apply()
        self.ticks += 1
        if self.ticks % 10 == 1:
            self.refresh(False)
        self.after(200, self.tick)

    def refresh(self, rescan=True):
        if rescan:
            self.scan_all()
        for p in list(self.profs):
            if not p["saved"] and not self.running_insts(p) and time.time() - self.launched.get(p["dir"], 0) > 30:
                shutil.rmtree(p["dir"], ignore_errors=True)  # temp profile whose app has closed
                self.net.pop(p["dir"], None)
                self.profs.remove(p)
        self.counts = self.read_counters() if self.helper else {}

        self.fill(self.ptree, [(p["dir"], (p["app"], p["name"], MODES[p["mode"]], "yes" if p["saved"] else "no",
                                           "running" if self.running_insts(p) else "closed")) for p in self.profs])
        names = {p["dir"]: p["app"] + "/" + p["name"] for p in self.profs}
        on = dict(self.applied or ())
        self.fill(self.rtree, [(k, (i["id"], k, names.get(self.owner.get(k), "normal"),
                                    MODES["offline"] if i["offline"] else MODES[on.get(k, "online")],
                                    self.bytes("o" + k), self.bytes("i" + k))) for k, i in sorted(self.insts.items())])

    def bytes(self, key):
        return human(self.totals.get(key, 0) + self.counts[key]) if key in self.counts else "-"

    @staticmethod
    def fill(tree, rows):
        sel = tree.selection()
        tree.delete(*tree.get_children())
        for iid, values in rows:
            tree.insert("", "end", iid=iid, values=values)
        keep = [i for i in sel if tree.exists(i)]
        if keep:
            tree.selection_set(keep)


if __name__ == "__main__":
    if sys.argv[1:] == ["--install-menu"]:
        try:
            install_menu()
        except OSError as e:
            sys.exit("FlatNet: " + str(e))
        sys.exit(0)
    lock = open(os.path.join(HERE, ".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        tk.Tk().withdraw()
        messagebox.showerror("FlatNet", "FlatNet is already open.")
        sys.exit(1)
    try:
        remember_paths()  # under the lock, before the window reads state.json
    except (OSError, ValueError) as e:
        print("FlatNet: path cache not updated: %s" % e, file=sys.stderr)
    FlatNet().mainloop()
