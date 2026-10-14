"""Find out which optional parts are missing and how to install them on this Linux distro.

Nothing is installed without the user saying yes. System packages go through pkexec (password prompt).
Parsers for other languages (tree-sitter) go into docmap/.deps/py3.X (one folder per Python version) with pip,
no password, nothing system-wide.

Package names: Arch verified with `pacman -Si` on 2026-10-13. Other distros: usual names, not tested.
"""
import os
import shlex
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from scan.ts_scan import DEPS, available  # noqa: E402

# family -> (install command, {part: [packages]})
SYSTEM = {
    "arch": (["pacman", "-S", "--needed", "--noconfirm"],
             {"window": ["webkit2gtk-4.1", "python-gobject"], "fallback": ["tk"]}),
    "debian": (["apt-get", "install", "-y"],
               {"window": ["gir1.2-webkit2-4.1", "python3-gi"], "fallback": ["python3-tk"]}),
    "fedora": (["dnf", "install", "-y"],
               {"window": ["webkit2gtk4.1", "python3-gobject"], "fallback": ["python3-tkinter"]}),
    "suse": (["zypper", "--non-interactive", "install"],
             {"window": ["typelib-1_0-WebKit2-4_1", "python3-gobject"], "fallback": ["python3-tk"]}),
}
FAMILY_OF = {"arch": "arch", "cachyos": "arch", "manjaro": "arch", "endeavouros": "arch",
             "debian": "debian", "ubuntu": "debian", "linuxmint": "debian", "pop": "debian",
             "fedora": "fedora", "rhel": "fedora", "centos": "fedora",
             "opensuse": "suse", "opensuse-leap": "suse", "opensuse-tumbleweed": "suse", "suse": "suse"}
# pip packages for tree-sitter languages (phase 5)
# tree-sitter 0.22+ has the API docmap uses; pip cannot get it on Python 3.8 (tested), so it stops there with an error.
PARSERS = ["tree-sitter>=0.22", "tree-sitter-rust", "tree-sitter-c", "tree-sitter-cpp", "tree-sitter-c-sharp"]


def distro():
    """(id, family) from /etc/os-release; family is None when not known."""
    info = {}
    try:
        with open("/etc/os-release") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                info[k] = v.strip('"')
    except OSError:
        pass
    ids = [info.get("ID", "")] + info.get("ID_LIKE", "").split()
    for i in ids:
        if i in FAMILY_OF:
            return info.get("ID", "?"), FAMILY_OF[i]
    return info.get("ID", "?"), None


def has_window():
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        for v in ("4.1", "4.0"):
            try:
                gi.require_version("WebKit2", v)
                from gi.repository import WebKit2  # noqa: F401
                return True
            except (ValueError, ImportError):
                continue
    except (ImportError, ValueError):
        pass
    return False


def has_fallback():
    try:
        import tkinter  # noqa: F401
        return True
    except ImportError:
        return False


def has_parsers():
    return available()


def shown(cmd):
    """A command as text that is safe to copy into a shell (quotes around tree-sitter>=0.22 and similar)."""
    return " ".join(shlex.quote(c) for c in cmd)


def system_command(part):
    """The exact command that would install a part, or None if this distro is not known."""
    _, fam = distro()
    if fam is None:
        return None
    cmd, parts = SYSTEM[fam]
    return ["pkexec"] + cmd + parts[part]


def parser_command():
    return [sys.executable, "-m", "pip", "install", "--target", DEPS, "--upgrade"] + PARSERS


def install(cmd):
    """Run an install command. Only call after the user agreed to that exact command."""
    if cmd[0] == "pkexec" and not shutil.which("pkexec"):
        return False, "pkexec not found. Run the command yourself with sudo:\n" + shown(cmd[1:])
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    return r.returncode == 0, r.stdout[-3000:]


def report():
    did, fam = distro()
    lines = ["Distro: %s (family: %s)" % (did, fam or "not known")]
    for name, ok, cmd in (("Window (WebKitGTK + PyGObject)", has_window(), system_command("window")),
                          ("Fallback window (Tkinter)", has_fallback(), system_command("fallback")),
                          ("Other languages (tree-sitter)", has_parsers(), parser_command())):
        lines.append("%-32s %s" % (name, "installed" if ok else "MISSING"))
        if not ok:
            lines.append("    install with: " + (shown(cmd) if cmd else "not known for this distro"))
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
