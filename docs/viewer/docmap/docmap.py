#!/usr/bin/env python3
"""docmap: accurate diagrams of a project, linked to its docs and source lines.

  python3 docmap.py scan <project folder> [--docs docs] [--out <folder>] [--ignore a,b]   (facts + coverage report)
  python3 docmap.py view [<project folder>] [--docs docs] [--ignore a,b]                  (open the window)
When docmap lives in <project>/docs/viewer/docmap/, the project folder can be left out.
Settings can also live in <project>/<docs>/viewer/docmap.json: {"ignore": ["profiles"]}
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from check import ConfigError, check, coverage_report, load_layout, stale_edits  # noqa: E402
from link import link  # noqa: E402
from scan.docs_scan import scan_markdown  # noqa: E402
from scan.python_scan import scan_python  # noqa: E402
from scan import ts_scan  # noqa: E402

ALWAYS_IGNORE = {".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache", ".pytest_cache"}
# Build output, skipped only when the project file next to it proves what it is: folder -> file ending.
BUILD_DIRS = {"target": "Cargo.toml", "bin": ".csproj", "obj": ".csproj"}


def list_files(root, ignore, build_dirs=None):
    """All files to scan. Build output folders found are added to build_dirs (shown in the coverage report)."""
    out = []
    for d, dirs, files in os.walk(root):
        rel_d = os.path.relpath(d, root)
        keep = []
        for x in sorted(dirs):
            rel_x = os.path.normpath(os.path.join(rel_d, x))
            if x in ALWAYS_IGNORE or rel_x in ignore or x in ignore:
                continue
            if x in BUILD_DIRS and any(f.endswith(BUILD_DIRS[x]) for f in files) \
                    or os.path.exists(os.path.join(d, x, "CMakeCache.txt")):
                if build_dirs is not None:
                    build_dirs.append(rel_x)
                continue
            keep.append(x)
        dirs[:] = keep
        out += [os.path.normpath(os.path.join(rel_d, f)) for f in sorted(files)]
    return out


def load_config(root, docs):
    """<project>/<docs>/viewer/docmap.json, or {} when there is none. A broken file stops with a clear message."""
    rel = os.path.join(docs, "viewer", "docmap.json")
    path = os.path.join(root, rel)
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        raise ConfigError("%s cannot be read: %s" % (rel, e))
    if not isinstance(cfg, dict) or not isinstance(cfg.get("ignore", []), list):
        raise ConfigError('%s must look like {"ignore": ["folder", ...]}' % rel)
    return cfg


def build(root, docs="docs", ignore=()):
    root = os.path.abspath(root)
    cfg = load_config(root, docs)
    ignore = set(ignore) | set(cfg.get("ignore", [])) | {os.path.join(docs, "viewer")}
    build_dirs = []
    files = list_files(root, ignore, build_dirs)
    facts = {"version": 1, "root": root, "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
             "nodes": [], "edges": [], "unknown": [], "python_files": [], "doc_files": [],
             "builtin_calls": 0, "ts_files": [], "skipped": [], "build_dirs": build_dirs}
    terms, links = [], []
    for rel in files:
        if rel.endswith(".py"):
            s = scan_python(root, rel)
            facts["python_files"].append(rel)
            facts["nodes"] += s.nodes
            facts["edges"] += s.edges
            facts["unknown"] += s.unknown
            facts["builtin_calls"] += s.stats["builtin_calls"]
        elif rel.endswith(".md"):
            n, e, t, l = scan_markdown(root, rel)
            facts["doc_files"].append(rel)
            facts["nodes"] += n
            facts["edges"] += e
            terms += t
            links += l
    ts_files = sorted(ts_scan.languages_for(files))
    if ts_files and ts_scan.available():
        p = ts_scan.scan_tree_sitter(root, ts_files)
        facts["ts_files"] = ts_files
        facts["nodes"] += p.nodes
        facts["edges"] += p.edges
        facts["unknown"] += p.unknown
    elif ts_files:
        facts["skipped"] = ts_files  # shown in the coverage report: these files were NOT read
    # outside modules/commands/files are found once per module scan: keep one of each
    seen, unique = set(), []
    for n in facts["nodes"]:
        if n["id"] not in seen:
            seen.add(n["id"])
            unique.append(n)
    facts["nodes"] = unique
    edges, facts["ambiguous"], facts["unlinked"], facts["link_errors"] = link(
        facts["nodes"], terms, links, files, root)
    facts["edges"] += edges
    facts["problems"] = check(facts, root)
    return facts


def view(a):
    project = a.project
    if not project:  # <project>/docs/viewer/docmap/docmap.py -> <project>
        project = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
        if os.path.basename(os.path.dirname(HERE)) != "viewer":
            sys.exit("Give the project folder: python3 docmap.py view <project>")
    root = os.path.abspath(project)
    out = os.path.join(root, a.docs, "viewer")
    ignore = [x for x in a.ignore.split(",") if x]

    def rebuild():
        facts = build(root, a.docs, ignore)
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "facts.json"), "w") as f:
            json.dump(facts, f, indent=1)
        stale = stale_edits(load_layout(os.path.join(out, "layout.json")), facts)
        with open(os.path.join(out, "coverage.md"), "w") as f:
            f.write(coverage_report(facts, facts["problems"], stale))
        return facts

    project_info = {"root": root, "out": out}
    reason = "forced by DOCMAP_FORCE_TK" if os.environ.get("DOCMAP_FORCE_TK") else None
    if not reason:
        try:
            import viewer
            return viewer.run(project_info, rebuild, a.snapshot)
        except (ImportError, ValueError) as e:
            reason = "WebKitGTK window not available (%s)" % e
    try:
        import fallback
    except ImportError as e:
        sys.exit("No window possible: %s and Tkinter is missing (%s).\nRun: python3 docmap.py setup" % (reason, e))
    print(reason + "; using the simple Tkinter view. Run 'python3 docmap.py setup' to install the full window.")
    return fallback.run(project_info, rebuild, reason, a.snapshot)


def setup(a):
    import setup_deps
    print(setup_deps.report())
    if not a.install:
        print("\nTo install a part: python3 docmap.py setup --install window|fallback|parsers")
        return 0
    cmd = setup_deps.parser_command() if a.install == "parsers" else setup_deps.system_command(a.install)
    if not cmd:
        print("Install command for this distro is not known. Install WebKitGTK 4.1 + PyGObject (or Tkinter) by hand.")
        return 1
    print("\nThis will run:\n  " + setup_deps.shown(cmd))
    if a.dry_run:
        print("(dry run: nothing was run)")
        return 0
    if input("Run it? [y/N] ").strip().lower() != "y":
        print("Nothing was installed.")
        return 0
    ok, output = setup_deps.install(cmd)
    print(output)
    print("Installed." if ok else "Install FAILED.")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="docmap: scan a project and check the result")
    sub = ap.add_subparsers(dest="cmd")
    sc = sub.add_parser("scan", help="scan a project folder")
    sc.add_argument("project")
    sc.add_argument("--docs", default="docs", help="docs folder inside the project (default: docs)")
    sc.add_argument("--out", help="output folder (default: <project>/<docs>/viewer)")
    sc.add_argument("--ignore", default="", help="comma-separated folders to skip")
    vw = sub.add_parser("view", help="open the docmap window")
    vw.add_argument("project", nargs="?", help="project folder (default: found from where docmap is)")
    vw.add_argument("--docs", default="docs")
    vw.add_argument("--ignore", default="")
    vw.add_argument("--snapshot", help=argparse.SUPPRESS)  # test helper: save a PNG of the window and quit
    st = sub.add_parser("setup", help="show missing parts and how to install them")
    st.add_argument("--install", choices=["window", "fallback", "parsers"])
    st.add_argument("--dry-run", action="store_true", help="only print the command")
    a = ap.parse_args()
    if a.cmd == "setup":
        return setup(a)
    if a.cmd not in ("scan", "view"):
        ap.print_help()
        return 2
    if a.cmd == "view":
        return view(a)
    facts = build(a.project, a.docs, [x for x in a.ignore.split(",") if x])
    out = a.out or os.path.join(os.path.abspath(a.project), a.docs, "viewer")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "facts.json"), "w") as f:
        json.dump(facts, f, indent=1)
    stale = stale_edits(load_layout(os.path.join(out, "layout.json")), facts)
    with open(os.path.join(out, "coverage.md"), "w") as f:
        f.write(coverage_report(facts, facts["problems"], stale))
    if stale:
        print("hand edits that no longer match the code: %d (see coverage.md)" % len(stale))
    print("nodes %d, links %d, not understood %d, doc terms linked %d / unlinked %d / ambiguous %d" % (
        len(facts["nodes"]), len(facts["edges"]), len(facts["unknown"]),
        sum(1 for e in facts["edges"] if e["kind"] == "documents"), len(facts["unlinked"]), len(facts["ambiguous"])))
    print("self-check:", "PASSED" if not facts["problems"] else "FAILED (%d)" % len(facts["problems"]))
    for p in facts["problems"]:
        print("  -", p)
    print("written:", out)
    return 0 if not facts["problems"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConfigError as e:
        sys.exit("docmap: " + str(e))
