"""docmap fallback window (Tkinter): used when WebKitGTK is missing. Same facts and views, plainer look.

Read-only: shows positions, groups, labels and notes from layout.json but does not edit them.
"""
import os
import re
import subprocess
import tkinter as tk
from tkinter import messagebox, ttk

import render
import setup_deps
from check import load_layout, stale_edits
from scan.docs_scan import term_text

FILEOPS = {"reads", "writes", "lists", "creates", "deletes", "moves", "changes", "checks"}
CALLS = {"calls", "calls_later", "references", "instantiates"}
CODE = {"function", "method", "class", "namespace", "constant", "module"}
COLORS = {"module": "#2f5bd3", "class": "#6c3fc5", "function": "#0f7b8a", "method": "#11806a", "constant": "#7a6a12",
          "external_module": "#8a5a00", "command": "#b23a48", "file": "#4f5b66", "folder": "#4f5b66", "doc": "#1d7a46",
          "heading": "#1d7a46", "namespace": "#5a6b8c"}


def views(by_id):
    def up(i):
        n = by_id.get(i)
        return n["parent"] if n and n["kind"] in ("function", "method", "constant", "heading") and n["parent"] else i

    def doc_up(i):
        n = by_id.get(i)
        return n["parent"] if n and n["kind"] == "heading" else i

    ident = lambda i: i  # noqa: E731
    return {  # same rules as web/app.js VIEWS
        "Overview": (lambda n: n["kind"] in ("module", "class", "namespace", "external_module", "command", "folder", "file"),
                     lambda e: e["kind"] in ("imports", "includes", "inherits", "runs_command", "contains") or e["kind"] in FILEOPS, up),
        "Functions & calls": (lambda n: n["kind"] in ("function", "method", "class", "module"),
                              lambda e: e["kind"] in CALLS and e["to"].startswith(("py:", "ts:")), ident),
        "Files & commands": (lambda n: n["kind"] in ("function", "method", "module", "command", "file", "folder"),
                             lambda e: e["kind"] == "runs_command" or e["kind"] in FILEOPS, ident),
        "Docs": (lambda n: n["kind"] == "doc" or n["kind"] in CODE or n["kind"] in ("command", "file", "folder", "external_module"),
                 lambda e: e["kind"] in ("documents", "links"), doc_up),
        "Runtime": (lambda n: True, lambda e: e["kind"] in ("references", "calls_later") and e.get("callback_of"), ident),
    }


def graph(facts, by_id, name):
    keep, use, mp = views(by_id)[name]
    agg, used = {}, set()
    for e in facts["edges"]:
        if not use(e):
            continue
        a, b = mp(e["from"]), mp(e["to"])
        if (a == b and name != "Runtime") or a not in by_id or b not in by_id or not keep(by_id[a]) or not keep(by_id[b]):
            continue
        if name == "Overview" and e["kind"] == "contains" and by_id[b]["kind"] not in ("class", "namespace"):
            continue
        g = agg.setdefault((a, b), {"from": a, "to": b, "certain": False, "notes": set()})
        g["certain"] |= e["certainty"] == "certain"
        if e.get("callback_of"):
            g["notes"].add(e["callback_of"] + (" %s ms" % e["delay"] if e.get("delay") is not None else ""))
        used |= {a, b}
    nodes = [by_id[i] for i in used]
    if name == "Functions & calls":
        nodes += [n for n in facts["nodes"] if n["kind"] in ("function", "method") and n["id"] not in used]
    return nodes, list(agg.values())


def label(n, layout):
    return layout.get("labels", {}).get(n["id"]) or (n["id"].split(":")[-1] if n["kind"] == "method" else n["name"])


def layered(nodes, edges, layout_fn):
    """Same idea as the page: rank by longest path (cycles ignored), order by barycenter, place in columns."""
    ids = [n["id"] for n in nodes]
    out, inn, state, fwd = {i: [] for i in ids}, {i: [] for i in ids}, {}, []

    def dfs(u):
        state[u] = 1
        for e in edges:
            if e["from"] == u and state.get(e["to"]) != 1:
                fwd.append(e)
                if not state.get(e["to"]):
                    dfs(e["to"])
        state[u] = 2
    for n in sorted(nodes, key=lambda n: n.get("line") or 0):
        if not state.get(n["id"]):
            dfs(n["id"])
    for e in fwd:
        out[e["from"]].append(e["to"])
        inn[e["to"]].append(e["from"])
    indeg = {i: len(inn[i]) for i in ids}
    queue, rank = [i for i in ids if not indeg[i]], {}
    while queue:
        u = queue.pop(0)
        rank.setdefault(u, 0)
        for w in out[u]:
            rank[w] = max(rank.get(w, 0), rank[u] + 1)
            indeg[w] -= 1
            if not indeg[w]:
                queue.append(w)
    cols = {}
    for i in ids:
        cols.setdefault(rank.get(i, 0), []).append(i)
    pos = {u: k for c in cols.values() for k, u in enumerate(c)}
    for p in range(8):
        for r in sorted(cols, reverse=bool(p % 2)):
            nb = out if p % 2 else inn
            cols[r].sort(key=lambda u: sum(pos[w] for w in nb[u]) / len(nb[u]) if nb[u] else pos[u])
            for k, u in enumerate(cols[r]):
                pos[u] = k
    at, x = {}, 0
    tallest = max(len(c) for c in cols.values()) if cols else 0
    gap = 70 if any(e["from"] == e["to"] for e in edges) else 16
    for r in sorted(cols):
        w = max(layout_fn(u) for u in cols[r])
        top = (tallest - len(cols[r])) * (38 + gap) / 2.0
        for k, u in enumerate(cols[r]):
            at[u] = [x, top + k * (38 + gap), layout_fn(u), 38]
        x += w + 110
    return at


class Fallback(object):
    def __init__(self, project, build, reason, snapshot=None):
        self.project, self.build, self.snapshot = project, build, snapshot
        self.facts = build()
        self.by_id = {n["id"]: n for n in self.facts["nodes"]}
        self.linker = render.Linker(self.facts)
        self.layout = load_layout(os.path.join(project["out"], "layout.json"))
        self.stale = stale_edits(self.layout, self.facts)
        self.root = tk.Tk()
        self.root.title("docmap (simple view): " + os.path.basename(project["root"]))
        self.root.geometry("1400x860")
        self.tabs = {}

        bar = ttk.Frame(self.root, padding=4)
        bar.pack(fill="x")
        cmd = setup_deps.system_command("window")
        ttk.Label(bar, text="Simple view: %s" % reason, foreground="#b7791f").pack(side="left")
        if cmd:
            ttk.Button(bar, text="Install the full window...", command=lambda: self.offer_install(cmd)).pack(side="left", padx=8)
        problems = len(self.facts["problems"])
        ttk.Label(bar, text=("Self-check FAILED (%d)" % problems) if problems else
                  ("%d stale edit(s)" % len(self.stale)) if self.stale else "Self-check passed",
                  foreground="#c0392b" if problems or self.stale else "#1d7a46").pack(side="right")

        main = ttk.PanedWindow(self.root, orient="horizontal")
        main.pack(fill="both", expand=True)
        left = ttk.Frame(main)
        right = ttk.Frame(main)
        main.add(left, weight=3)
        main.add(right, weight=2)

        self.view = tk.StringVar(value="Overview")
        vb = ttk.Frame(left)
        vb.pack(fill="x")
        for v in list(views(self.by_id)) + ["Coverage"]:
            ttk.Radiobutton(vb, text=v, value=v, variable=self.view, command=self.draw).pack(side="left", padx=2)
        self.canvas = tk.Canvas(left, background="white", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B1-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.canvas.bind("<Button-4>", lambda e: self.zoom(1.15, e))
        self.canvas.bind("<Button-5>", lambda e: self.zoom(1 / 1.15, e))
        self.canvas.bind("<MouseWheel>", lambda e: self.zoom(1.15 if e.delta > 0 else 1 / 1.15, e))
        self.details = tk.Text(left, height=12, wrap="word", cursor="arrow")
        self.details.pack(fill="x")

        tb = ttk.Frame(right)
        tb.pack(fill="x")
        ttk.Button(tb, text="Open in default app", command=self.external).pack(side="right")
        ttk.Button(tb, text="Close tab", command=self.close_tab).pack(side="right")
        self.book = ttk.Notebook(right)
        self.book.pack(fill="both", expand=True)
        self.draw()
        if snapshot:
            self.root.after(2500, self.take_snapshot)

    # ---- install ----
    def offer_install(self, cmd):
        if messagebox.askyesno("Install", "This will run (asks for your password):\n\n%s\n\nThen restart docmap." % setup_deps.shown(cmd)):
            ok, out = setup_deps.install(cmd)
            (messagebox.showinfo if ok else messagebox.showerror)("Install", ("Done. Restart docmap.\n\n" if ok else "Failed:\n\n") + out[-1500:])

    # ---- diagram ----
    def zoom(self, k, ev):
        self.canvas.scale("all", self.canvas.canvasx(ev.x), self.canvas.canvasy(ev.y), k, k)

    def draw(self):
        c = self.canvas
        c.delete("all")
        name = self.view.get()
        if name == "Coverage":
            return self.show_coverage()
        nodes, edges = graph(self.facts, self.by_id, name)
        width = lambda i: max(90, 7 * len(label(self.by_id[i], self.layout)) + 30)  # noqa: E731
        at = layered(nodes, edges, width)
        for i, p in (self.layout.get("positions", {}).get({"Overview": "overview", "Functions & calls": "calls",
                                                           "Files & commands": "files", "Docs": "docs",
                                                           "Runtime": "runtime"}[name], {}) or {}).items():
            if i in at:
                at[i][0], at[i][1] = p
        for e in edges:
            x1, y1, w1, h1 = at[e["from"]]
            x2, y2, w2, h2 = at[e["to"]]
            dash = () if e["certain"] else (5, 4)
            if e["from"] == e["to"]:
                c.create_line(x1 + w1 - 30, y1, x1 + w1 + 30, y1 - 40, x1 + w1 + 40, y1 + h1 / 2, x1 + w1, y1 + h1 / 2,
                              smooth=True, dash=dash, arrow="last", fill="#777")
            else:
                c.create_line(x1 + w1, y1 + h1 / 2, (x1 + w1 + x2) / 2, y1 + h1 / 2, (x1 + w1 + x2) / 2, y2 + h2 / 2,
                              x2, y2 + h2 / 2, smooth=True, dash=dash, arrow="last", fill="#999")
            if e["notes"]:
                tx, ty = ((x1 + 6, y1 - 8) if e["from"] == e["to"] else ((x1 + w1 + x2) / 2, (y1 + y2 + h1) / 2 - 8))
                c.create_text(tx, ty, text=", ".join(sorted(e["notes"])), fill="#2f5bd3", font=("TkDefaultFont", 8),
                              anchor="w" if e["from"] == e["to"] else "center")
        for n in nodes:
            x, y, w, h = at[n["id"]]
            tag = "n%d" % id(n)
            col = COLORS.get(n["kind"], "#555")
            c.create_rectangle(x, y, x + w, y + h, outline=col, width=2, fill="#ffffff", tags=(tag,))
            c.create_rectangle(x, y, x + 5, y + h, outline=col, fill=col, tags=(tag,))
            sub = n["kind"] + ("  line %d" % n["line"] if n.get("line") and n["kind"] in CODE - {"module"} else "")
            c.create_text(x + 12, y + 12, text=label(n, self.layout), anchor="w", font=("TkDefaultFont", 9, "bold"), tags=(tag,))
            c.create_text(x + 12, y + 27, text=sub, anchor="w", fill="#6b7280", font=("TkDefaultFont", 8), tags=(tag,))
            if self.layout.get("notes", {}).get(n["id"]):
                c.create_text(x + w - 10, y + 10, text="✎", fill="#8e44ad", tags=(tag,))
            c.tag_bind(tag, "<Button-1>", lambda ev, i=n["id"]: self.select(i))
        box = c.bbox("all")
        if box:
            c.configure(scrollregion=(box[0] - 40, box[1] - 40, box[2] + 40, box[3] + 40))
            c.xview_moveto(0)
            c.yview_moveto(0)

    # ---- details and tabs ----
    def link(self, text_widget, text, action):
        tag = "a%d" % len(text_widget.tag_names())
        text_widget.insert("end", text, (tag, "link"))
        text_widget.tag_bind(tag, "<Button-1>", lambda ev: action())

    def select(self, nid):
        n = self.by_id.get(nid)
        d = self.details
        d.configure(state="normal")
        d.delete("1.0", "end")
        d.tag_configure("link", foreground="#2f5bd3", underline=True)
        d.tag_configure("h", font=("TkDefaultFont", 11, "bold"))
        d.tag_configure("mine", foreground="#8e44ad")
        if not n:
            d.insert("end", "Unknown item " + nid)
            return
        d.insert("end", label(n, self.layout) + "\n", "h")
        d.insert("end", n["kind"] + "  ")
        if n.get("file") and n.get("line"):
            self.link(d, "%s, line %s" % (n["file"], n["line"]), lambda: self.open(n["file"], n["line"]))
            self.open(n["file"], n["line"])
        elif n["kind"] == "file" and n.get("on_disk"):
            self.link(d, n["name"], lambda: self.open(n["name"], 1))
        d.insert("end", "\n")
        if self.layout.get("notes", {}).get(nid):
            d.insert("end", "Your note: " + self.layout["notes"][nid] + "\n", "mine")
        kids = {nid} | {m["id"] for m in self.facts["nodes"] if m.get("parent") == nid}
        docs = [e for e in self.facts["edges"] if e["kind"] == "documents" and e["to"] in kids]
        d.insert("end", "Explained in docs (%d): " % len(docs))
        for e in docs:
            self.link(d, "%s:%d" % (e["file"], e["line"]), lambda e=e: self.open(e["file"], e["line"]))
            d.insert("end", "  ")
        for title, rows, other in (("\nUses: ", [e for e in self.facts["edges"] if e["from"] == nid and e["kind"] != "contains"], "to"),
                                   ("\nUsed by: ", [e for e in self.facts["edges"] if e["to"] == nid and e["kind"] not in ("contains", "documents")], "from")):
            if rows:
                d.insert("end", title)
                for e in rows:
                    o = self.by_id.get(e[other])
                    self.link(d, label(o, self.layout) if o else e[other], lambda i=e[other]: self.select(i))
                    d.insert("end", " (%s, %s:%s%s)  " % (e["kind"], e["file"], e["line"], "" if e["certainty"] == "certain" else ", indirect"))
        d.configure(state="disabled")

    def open(self, rel, line):
        if rel not in self.tabs:
            frame = ttk.Frame(self.book)
            t = tk.Text(frame, wrap="none", font=("monospace", 10), cursor="arrow")
            sy = ttk.Scrollbar(frame, command=t.yview)
            t.configure(yscrollcommand=sy.set)
            sy.pack(side="right", fill="y")
            t.pack(fill="both", expand=True)
            self.fill(t, rel)
            self.book.add(frame, text=os.path.basename(rel))
            self.tabs[rel] = (frame, t)
        frame, t = self.tabs[rel]
        self.book.select(frame)
        t.tag_remove("hit", "1.0", "end")
        t.tag_add("hit", "%d.0" % line, "%d.end" % line)
        t.see("%d.0" % line)

    def fill(self, t, rel):
        path = os.path.normpath(os.path.join(self.project["root"], rel))
        try:
            with open(path) as f:
                lines = f.read().splitlines()
        except (OSError, UnicodeDecodeError) as e:
            t.insert("end", "Cannot show this file as text: %s" % e)
            return
        t.tag_configure("no", foreground="#9aa3ad")
        t.tag_configure("def", font=("monospace", 10, "bold"))
        t.tag_configure("head", font=("monospace", 11, "bold"))
        t.tag_configure("link", foreground="#2f5bd3", underline=True)
        t.tag_configure("chip", foreground="#2f5bd3")
        t.tag_configure("hit", background="#dbe4ff")
        md = rel.endswith(".md")
        for no, line in enumerate(lines, 1):
            t.insert("end", "%5d  " % no, "no")
            if md:
                pos = 0
                head = "head" if re.match(r"^#{1,6}\s", line) else ()
                for m in render.CODE_SPAN.finditer(line):
                    t.insert("end", line[pos:m.start()], head)
                    term = term_text(m.group(2), line)
                    hit = self.linker.term_at.get((rel, no, term))
                    if hit:
                        self.link(t, m.group(0), lambda i=hit[0]: self.select(i))
                    else:
                        t.insert("end", m.group(0), head)
                    pos = m.end()
                t.insert("end", line[pos:], head)
            else:
                t.insert("end", line, "def" if (rel, no) in self.linker.defs else ())
                for e, n in self.linker.from_line.get((rel, no), []):
                    t.insert("end", "  ")
                    self.link(t, "→ " + n["name"], lambda i=n["id"]: self.select(i))
            t.insert("end", "\n")
        t.configure(state="disabled")

    def current_file(self):
        sel = self.book.select()
        return next((rel for rel, (f, _) in self.tabs.items() if str(f) == sel), None)

    def external(self):
        rel = self.current_file()
        if rel:
            subprocess.Popen(["xdg-open", os.path.join(self.project["root"], rel)])

    def close_tab(self):
        rel = self.current_file()
        if rel:
            self.book.forget(self.tabs.pop(rel)[0])

    def show_coverage(self):
        c = self.canvas
        lines = ["Self-check: " + ("FAILED" if self.facts["problems"] else "PASSED")]
        lines += ["  - " + p for p in self.facts["problems"]]
        lines += ["", "Hand edits that no longer match the code: %d" % len(self.stale)]
        lines += ["  - %s %s" % (s["what"], s["id"]) for s in self.stale]
        lines += ["", "Not understood (never drawn): %d" % len(self.facts["unknown"])]
        lines += ["  %s:%s  %s" % (u["file"], u["line"], u["reason"]) for u in self.facts["unknown"][:400]]
        c.create_text(20, 20, text="\n".join(lines), anchor="nw", font=("monospace", 9))
        c.configure(scrollregion=c.bbox("all"))

    def take_snapshot(self):
        script = os.environ.get("DOCMAP_SNAPSHOT_TK")
        if script:
            exec(script, {"app": self})
        self.root.update()
        self.root.after(800, self._snap)

    def _snap(self):
        frame = self.root.wm_frame()
        subprocess.run(["import", "-window", str(int(frame, 16) if frame.startswith("0x") else frame), self.snapshot])
        print("snapshot written:", self.snapshot)
        self.root.destroy()


def run(project, build, reason, snapshot=None):
    app = Fallback(project, build, reason, snapshot)
    app.root.mainloop()
    return 0
