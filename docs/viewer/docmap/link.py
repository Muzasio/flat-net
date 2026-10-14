"""Join doc terms and links to code facts. Exact matches only; anything else is reported, never guessed."""
import builtins
import os
import shutil

PY_BUILTINS = set(dir(builtins))


def keys_for(node):
    """The exact spellings a doc may use for a node."""
    k, name = node["kind"], node["name"]
    if k in ("function", "method"):
        cls = node["id"].rsplit(":", 1)[1].split("@")[0].split(".")[0] if k == "method" else None
        out = {name + "()"}
        if cls:
            out |= {cls + sep + name + end for sep in (".", "::") for end in ("", "()")}
        return out
    if k in ("class", "constant", "command", "external_module", "namespace"):
        return {name}
    if k in ("module", "doc", "file", "folder"):
        if k in ("file", "folder") and not node.get("exact", True):
            return set()
        return {name, os.path.basename(name)} if name else set()
    return set()


def link(nodes, terms, links, project_files, root):
    by_id = {n["id"]: n for n in nodes}
    for rel in project_files:  # files that exist on disk, so docs can point at them
        fid = "file:" + rel
        if fid not in by_id:
            by_id[fid] = {"id": fid, "kind": "file", "name": rel, "file": None, "line": None, "end_line": None,
                          "parent": None, "exact": True}
            nodes.append(by_id[fid])
        by_id[fid]["on_disk"] = True
    index = {}
    for n in nodes:
        for key in keys_for(n):
            index.setdefault(key, set()).add(n["id"])
    # a module file and its "file:" twin are the same thing: prefer the module
    for key, ids in index.items():
        mods = {i for i in ids if i.startswith(("py:", "doc:"))}
        twins = {"file:" + by_id[i]["name"] for i in mods}
        index[key] = ids - twins if mods else ids

    # Bare function names (no "()") are weaker: a doc may mean a system command with the same name.
    bare = {}
    for n in nodes:
        if n["kind"] in ("function", "method"):
            bare.setdefault(n["name"], set()).add(n["id"])

    edges, ambiguous, unlinked, errors = [], [], [], []
    for t in terms:
        ids, certainty = index.get(t["text"], set()), "certain"
        if not ids and t["text"] in bare:
            ids, certainty = set(bare[t["text"]]), "resolved"
            clash = [w for w, hit in (("command run by this project", "cmd:" + t["text"] in by_id),
                                      ("system command", shutil.which(t["text"])),
                                      ("Python built-in", t["text"] in PY_BUILTINS)) if hit]
            if clash:
                ambiguous.append(dict(t, candidates=sorted(ids) + ["(%s %s)" % (c, t["text"]) for c in clash]))
                continue
        if len(ids) == 1:
            edges.append({"from": t["section"], "to": next(iter(ids)), "kind": "documents", "certainty": certainty,
                          "file": t["file"], "line": t["line"], "term": t["text"]})
        elif ids:
            ambiguous.append(dict(t, candidates=sorted(ids)))
        else:
            unlinked.append(t)

    anchors = {(n["file"], n.get("anchor")) for n in nodes if n["kind"] == "heading"}
    for l in links:
        target = l["target"]
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, anchor = target.partition("#")
        rel = os.path.normpath(os.path.join(os.path.dirname(l["file"]), path)) if path else l["file"]
        if not os.path.exists(os.path.join(root, rel)):
            errors.append(dict(l, problem="link target file does not exist: " + rel))
            continue
        if os.path.isdir(os.path.join(root, rel)):
            dest = "file:" + rel
            if dest not in by_id:
                by_id[dest] = {"id": dest, "kind": "folder", "name": rel, "file": None, "line": None,
                               "end_line": None, "parent": None}
                nodes.append(by_id[dest])
        else:
            dest = "doc:" + rel if rel.endswith(".md") else "file:" + rel
        if anchor:
            if (rel, anchor) not in anchors:
                errors.append(dict(l, problem="heading #%s not found in %s" % (anchor, rel)))
                continue
            dest = "doc:%s#%s" % (rel, anchor)
        edges.append({"from": l["section"], "to": dest, "kind": "links", "certainty": "certain",
                      "file": l["file"], "line": l["line"]})
    return edges, ambiguous, unlinked, errors
