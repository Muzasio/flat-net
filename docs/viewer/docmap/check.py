"""Self-checks: every fact must point at something real. Failures are shown, never hidden."""
import io
import os
import re
import tokenize

class ConfigError(Exception):
    """docmap.json cannot be used. Shown to the user as one clear line, never as a traceback."""


DEF = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)")
CLASS = re.compile(r"^\s*class\s+(\w+)")


def code_only(src_lines):
    """Source lines with comments and string contents blanked, using Python's tokenizer (not the parser)."""
    out = [list(l) for l in src_lines]
    try:
        for tok in tokenize.generate_tokens(io.StringIO("\n".join(src_lines) + "\n").readline):
            if tok.type in (tokenize.STRING, tokenize.COMMENT) or tok.type == getattr(tokenize, "FSTRING_MIDDLE", -1):
                (r1, c1), (r2, c2) = tok.start, tok.end
                for r in range(r1, r2 + 1):
                    a = c1 if r == r1 else 0
                    b = c2 if r == r2 else len(out[r - 1])
                    for c in range(a, min(b, len(out[r - 1]))):
                        out[r - 1][c] = " "
    except (tokenize.TokenError, IndentationError, IndexError):
        return [l.split("#", 1)[0] for l in src_lines]
    return ["".join(l) for l in out]


# Comments, strings and characters of Rust/C/C++/C#, read left to right in one pass, so a "//" inside a
# string or a '"' character cannot fool it. Raw strings: Rust r#"..."#, C++ R"x(...)x", C# @"...".
NOISE = re.compile(r'//[^\n]*|/\*.*?\*/|(?<!\w)r(#*)".*?"\1|(?<!\w)R"([^(\s]*)\(.*?\)\2"|@"(?:[^"]|"")*"'
                   r'|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\\n])\'', re.S)
EFFECTS = {"reads", "writes", "lists", "creates", "deletes", "moves", "changes", "checks", "runs_command"}


def check(facts, root):
    problems = []
    lines_of = {}

    def lines(rel):
        if rel not in lines_of:
            with open(os.path.join(root, rel)) as f:
                lines_of[rel] = f.read().splitlines()
        return lines_of[rel]

    ids = {n["id"] for n in facts["nodes"]}
    for n in facts["nodes"]:
        if not n.get("file") or not n.get("line"):
            continue
        src = lines(n["file"])
        if n["kind"] in ("module", "doc"):
            continue  # whole-file nodes (the file can be empty)
        if n["line"] > len(src):
            problems.append("%s: line %d is past the end of %s" % (n["id"], n["line"], n["file"]))
            continue
        text = src[n["line"] - 1]
        if n["id"].startswith("ts:"):
            expect = n["name"] if n["kind"] in ("function", "method", "class", "namespace", "constant") else None
        else:
            expect = {"function": "def " + n["name"], "method": "def " + n["name"], "class": "class " + n["name"],
                      "constant": n["name"], "heading": n["name"]}.get(n["kind"])
        if expect and expect not in text:
            problems.append("%s: line %d of %s does not contain '%s'" % (n["id"], n["line"], n["file"], expect))

    for e in facts["edges"]:
        for end in ("from", "to"):
            if e[end] not in ids:
                problems.append("edge %s -> %s (line %s): '%s' is not a known node" % (e["from"], e["to"], e["line"], e[end]))

    # Second, independent method: count def/class lines with a plain pattern and compare with the parser.
    for rel in facts["python_files"]:
        by_regex = sorted(m.group(1) for l in lines(rel) for m in [DEF.match(l) or CLASS.match(l)] if m)
        by_parser = sorted(n["name"] for n in facts["nodes"] if n.get("file") == rel
                           and n["kind"] in ("function", "method", "class"))
        if by_regex != by_parser:
            problems.append("%s: parser found %d functions/classes, line pattern found %d (difference: %s)" % (
                rel, len(by_parser), len(by_regex), sorted(set(by_regex) ^ set(by_parser))))

    # Second method for calls: plain text search for "self.name(" and "name(" on each line, compared with
    # the parser's call links. Comments are cut off; text inside strings can still cause a reported difference.
    for rel in facts["python_files"]:
        mod = "py:" + rel
        by_id = {n["id"]: n for n in facts["nodes"]}
        names = {}
        for n in facts["nodes"]:
            if n.get("file") == rel and n["kind"] in ("function", "method", "class"):
                names.setdefault(n["name"], []).append(n)
        parsed = {(e["line"], e["to"]) for e in facts["edges"] if e["file"] == rel
                  and e["kind"] in ("calls", "calls_later", "instantiates") and e["to"].startswith(mod + ":")}
        found = set()
        either = []  # (line, ids): a self.name( call where name is a method of several classes
        for no, l in enumerate(code_only(lines(rel)), 1):
            code = l
            if DEF.match(code):  # def f(self) -> None: self.g()   the body can be on the same line
                rest = re.match(r"^\s*(?:async\s+)?def\s+\w+\s*\(.*\)\s*(?:->\s*.+?)?:\s*(\S.*)$", code)
                if not rest:
                    continue
                code = rest.group(1)
            elif CLASS.match(code):
                continue
            for name, defs in names.items():
                for m in re.finditer(r"(self\.|\b)%s\s*\(" % re.escape(name), code):
                    if m.start() > 0 and code[m.start() - 1] == "." and not m.group(1):
                        continue  # other_object.name( is not this function
                    cands = []
                    for d in defs:
                        outer = by_id.get(d["parent"])
                        if outer and outer["kind"] in ("function", "method") and not (outer["line"] <= no <= outer["end_line"]):
                            continue  # a nested function's name only counts inside the function that holds it
                        if bool(m.group(1)) == (d["kind"] == "method"):
                            cands.append(d)
                    if m.group(1) and len(cands) > 1:  # self.name(: the method of the class this line is in
                        inside = [d for d in cands if by_id[d["parent"]]["line"] <= no <= by_id[d["parent"]]["end_line"]]
                        cands = inside or cands
                    if len(cands) > 1:  # inherited: any one of them is right; the parser must pick one of these
                        either.append((no, {d["id"] for d in cands}))
                    else:
                        found.update((no, d["id"]) for d in cands)
        for no, ids in either:
            picked = {(no, i) for i in ids} & parsed
            if picked:
                parsed -= picked
            else:
                problems.append("%s line %d: call to one of %s found by text search only" % (rel, no, sorted(ids)))
        for line, nid in sorted(found ^ parsed):
            side = "text search only" if (line, nid) in found else "parser only"
            problems.append("%s line %d: call to %s found by %s" % (rel, line, nid.split(":", 1)[1], side))

    # Second method for other languages: plain patterns on the text (comments and strings removed first).
    second = {"rust": (r"\bfn\s+(\w+)", ("function", "method")),
              "c_sharp": (r"\b(?:class|struct|interface|enum|record)\s+(\w+)", ("class",)),
              "c": (r"\b(?:struct|union)\s+(\w+)\s*\{", ("class",)),
              "cpp": (r"\b(?:struct|class|union)\s+(\w+)\s*(?:final\s*)?(?::[^{;]*)?\{", ("class",))}
    langs = {n["file"]: n.get("lang") for n in facts["nodes"] if n["kind"] == "module" and n["id"].startswith("ts:")}
    for rel in facts.get("ts_files", []):
        pattern, kinds = second[langs[rel]]
        text = NOISE.sub(lambda m: " " if m.group(0).startswith("/") else '""', "\n".join(lines(rel)))
        by_text = re.findall(pattern, text)
        for u in facts["unknown"]:  # fn inside a macro: listed as not read, so the parser cannot see it
            if u["file"] == rel and u.get("macro_fn") in by_text:
                by_text.remove(u["macro_fn"])
        by_text = sorted(by_text)
        by_parser = sorted(n["name"] for n in facts["nodes"] if n.get("file") == rel and n["kind"] in kinds)
        if by_text != by_parser:
            problems.append("%s: parser found %s, text pattern found %s" % (rel, by_parser, by_text))

    # Second method for files and commands in other languages: the library name must be on the edge's line,
    # and the path or command word must be written inside the call's lines.
    ts_set = set(facts.get("ts_files", []))
    by_id = {n["id"]: n for n in facts["nodes"]}
    for e in facts["edges"]:
        if e["file"] not in ts_set or e["kind"] not in EFFECTS or e["to"] not in by_id:
            continue
        src = lines(e["file"])
        word = re.split(r"::|\.", e.get("name", ""))[-1]
        target = e["args"][0] if e["kind"] == "runs_command" else by_id[e["to"]]["name"]
        if e["line"] > len(src) or word not in src[e["line"] - 1]:
            problems.append("%s line %d: '%s' is not on that line" % (e["file"], e["line"], word))
        elif "value_name" in e and not re.search(r"\b%s\b" % re.escape(e["value_name"]),
                                                 "\n".join(src[e["line"] - 1:e.get("end_line", e["line"])])):
            problems.append("%s line %d: variable '%s' is not used in that call" % (e["file"], e["line"], e["value_name"]))
        elif "value_line" in e:  # the string is in a variable or constant defined elsewhere
            vsrc = lines(e["value_file"])
            if e["value_line"] > len(vsrc) or target not in vsrc[e["value_line"] - 1]:
                problems.append("%s line %d: '%s' is not written on %s line %d" % (
                    e["file"], e["line"], target, e["value_file"], e["value_line"]))
        elif target not in "\n".join(src[e["line"] - 1:e.get("end_line", e["line"])]):
            problems.append("%s line %d: '%s' is not written in that call" % (e["file"], e["line"], target))

    for err in facts["link_errors"]:
        problems.append("%s line %d: %s" % (err["file"], err["line"], err["problem"]))
    return problems


def coverage_report(facts, problems, stale=()):
    count = lambda items, key: sorted(((sum(1 for i in items if i[key] == v)), v) for v in {i[key] for i in items})
    out = ["# Coverage report", "", "Generated by docmap. Do not edit by hand.", ""]
    out += ["## Self-check", ""]
    out += ["**PASSED.** Every node points to a real line, every link and heading exists, and both counting methods agree."] \
        if not problems else ["**FAILED: %d problem(s).** The diagram may be wrong where these point." % len(problems), ""] \
        + ["- " + p for p in problems]
    out += ["", "## Hand edits that no longer match (%d)" % len(stale), ""]
    out += ["- %s `%s`%s" % (s["what"], s["id"], " in view " + s["view"] if s.get("view") else "") for s in stale] \
        or ["None. Every hand edit points at something that exists."]
    if facts.get("skipped"):
        out += ["", "## Files NOT read (%d)" % len(facts["skipped"]), "",
                "These need the tree-sitter parsers. Run: `python3 docmap.py setup --install parsers`", ""]
        out += ["- `%s`" % f for f in facts["skipped"]]
    if facts.get("build_dirs"):
        out += ["", "## Build output folders skipped (%d)" % len(facts["build_dirs"]), "",
                "Made by the compiler (next to Cargo.toml / a .csproj, or holding CMakeCache.txt). Not your code.", ""]
        out += ["- `%s`" % d for d in facts["build_dirs"]]
    out += ["", "## What was found", "", "| Kind | Count |", "|---|---|"]
    out += ["| %s | %d |" % (k, c) for c, k in count(facts["nodes"], "kind")]
    out += ["", "| Link kind | Certainty | Count |", "|---|---|---|"]
    pairs = {}
    for e in facts["edges"]:
        pairs[(e["kind"], e["certainty"])] = pairs.get((e["kind"], e["certainty"]), 0) + 1
    out += ["| %s | %s | %d |" % (k, c, n) for (k, c), n in sorted(pairs.items())]
    out += ["", "## Not understood (never drawn)", "",
            "These calls or paths could not be proven. They are **not** in the diagram. Check them in the code.", ""]
    for c, reason in count(facts["unknown"], "reason")[::-1]:
        out.append("### %s (%d)" % (reason, c))
        out += ["- `%s` line %s, in `%s`: `%s`" % (u["file"], u["line"], u["in"].split(":", 1)[-1], u["code"])
                for u in facts["unknown"] if u["reason"] == reason]
        out.append("")
    out += ["## Doc terms with more than one match (not linked)", ""]
    out += ["- `%s` (%s line %d): %s" % (a["text"], a["file"], a["line"], ", ".join(a["candidates"]))
            for a in facts["ambiguous"]] or ["None."]
    out += ["", "## Doc terms that match nothing in the code (%d)" % len(facts["unlinked"]), "",
            "Usually commands, values or outside names. Listed so nothing is silently dropped.", ""]
    out += ["- `%s` (%s line %d)" % (u["text"], u["file"], u["line"]) for u in facts["unlinked"]]
    return "\n".join(out) + "\n"


def stale_edits(layout, facts):
    """Hand edits (layout.json) that point at things no longer in the code or docs. Shown in red, never dropped silently."""
    ids = {n["id"] for n in facts["nodes"]}
    out = []
    for view, pos in (layout.get("positions") or {}).items():
        out += [{"what": "position", "view": view, "id": i} for i in pos if i not in ids]
    for view, groups in (layout.get("groups") or {}).items():
        for g in groups:
            out += [{"what": "group member", "view": view, "group": g.get("name"), "id": i}
                    for i in g.get("members", []) if i not in ids]
    for what in ("labels", "notes"):
        out += [{"what": what[:-1], "id": i} for i in (layout.get(what) or {}) if i not in ids]
    for l in layout.get("links") or []:
        out += [{"what": "manual link", "id": l[end], "link": [l["from"], l["to"]]}
                for end in ("from", "to") if l[end] not in ids]
    return out


def load_layout(path):
    import json
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
