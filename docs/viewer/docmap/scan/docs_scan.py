"""Facts from Markdown docs: headings, `code terms` and links, each with its line number."""
import os
import re

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
CODE_SPAN = re.compile(r"(`+)(.+?)\1")
LINK = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)\s]+)\)")


def slug(text):
    """GitHub-style heading anchor: lower case, drop punctuation, spaces to dashes."""
    text = re.sub(r"`|\*\*|__", "", text.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def term_text(raw, line):
    """The term inside a code span. In a table row "\\|" stands for "|" (GitHub rule), so it is unescaped there."""
    raw = raw.strip()
    return raw.replace("\\|", "|") if line.lstrip().startswith("|") else raw


def scan_markdown(root, rel):
    with open(os.path.join(root, rel)) as f:
        lines = f.read().splitlines()
    doc_id = "doc:" + rel
    nodes = [{"id": doc_id, "kind": "doc", "name": rel, "file": rel, "line": 1, "end_line": len(lines), "parent": None}]
    edges, terms, links = [], [], []
    current, in_fence, seen = doc_id, False, {}
    for no, text in enumerate(lines, 1):
        if FENCE.match(text):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING.match(text)
        if m:
            base = slug(m.group(2))
            anchor = base if base not in seen else "%s-%d" % (base, seen[base])
            seen[base] = seen.get(base, 0) + 1
            current = "%s#%s" % (doc_id, anchor)
            nodes.append({"id": current, "kind": "heading", "name": m.group(2), "file": rel, "line": no,
                          "end_line": None, "parent": doc_id, "level": len(m.group(1)), "anchor": anchor})
            edges.append({"from": doc_id, "to": current, "kind": "contains", "certainty": "certain",
                          "file": rel, "line": no})
        for cm in CODE_SPAN.finditer(text):
            terms.append({"text": term_text(cm.group(2), text), "file": rel, "line": no, "section": current})
        for lm in LINK.finditer(re.sub(CODE_SPAN, "", text)):
            links.append({"text": lm.group(1), "target": lm.group(2), "file": rel, "line": no, "section": current})
    # end_line of each heading = line before the next heading of any level
    heads = [n for n in nodes if n["kind"] == "heading"]
    for a, b in zip(heads, heads[1:] + [None]):
        a["end_line"] = (b["line"] - 1) if b else len(lines)
    return nodes, edges, terms, links
