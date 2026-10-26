"""Turn docs and source files into HTML for the viewer tabs: line numbers, clickable terms, call chips.

Raw HTML inside Markdown is shown as text, never run, so a doc cannot load anything by itself.
"""
import html
import posixpath
import re
import urllib.parse

from scan.docs_scan import CODE_SPAN, FENCE, HEADING, slug

LIST = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}")
IMG = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
BOLD = re.compile(r"\*\*(.+?)\*\*")
ITALIC = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])")
SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
PROJECT_URL = "docmap://project/"  # images inside the project are served from here (viewer.py, images only)


def table_cells(row):
    """Cells of a table row, GitHub rules: "\\|" is a "|" inside a cell, also inside `code`."""
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    cells, cur, i = [], [], 0
    while i < len(row):
        if row[i] == "\\" and row[i + 1:i + 2] == "|":
            cur.append("|")
            i += 2
            continue
        if row[i] == "|":
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(row[i])
        i += 1
    return cells + ["".join(cur).strip()]


def image_src(src, rel):
    """Where an image in doc `rel` loads from, or None if it may not load (outside the project, unknown scheme)."""
    if src.startswith(("http://", "https://", "data:")):
        return src
    if SCHEME.match(src) or src.startswith("/"):
        return None
    path = posixpath.normpath(posixpath.join(posixpath.dirname(rel), src.split("#")[0].split("?")[0]))
    if path == ".." or path.startswith("../"):
        return None
    return PROJECT_URL + urllib.parse.quote(path)


class Linker(object):
    """Answers 'what does this term at this line point to?' from the facts, so the page never guesses."""

    def __init__(self, facts):
        self.term_at = {}
        for e in facts["edges"]:
            if e["kind"] == "documents":
                self.term_at[(e["file"], e["line"], e["term"])] = (e["to"], e["certainty"])
        self.ambiguous = {(a["file"], a["line"], a["text"]): a["candidates"] for a in facts["ambiguous"]}
        self.from_line = {}
        nodes = {n["id"]: n for n in facts["nodes"]}
        for e in facts["edges"]:
            if e["kind"] in ("calls", "calls_later", "references", "instantiates", "runs_command", "reads", "writes",
                             "lists", "creates", "deletes", "moves", "changes", "checks") and e["to"] in nodes:
                self.from_line.setdefault((e["file"], e["line"]), []).append((e, nodes[e["to"]]))
        self.defs = {(n["file"], n["line"]): n for n in facts["nodes"]
                     if n["kind"] in ("function", "method", "class", "namespace", "constant")}


def inline(text, rel, line, linker, internet):
    """One line of Markdown inline syntax to HTML. Code spans are cut out first so nothing inside them changes."""
    spans = []

    def keep(m):
        term = m.group(2).strip()
        key = (rel, line, term)
        esc = html.escape(term)
        if key in linker.term_at:
            nid, certainty = linker.term_at[key]
            out = '<code class="term %s" data-node="%s">%s</code>' % (certainty, html.escape(nid), esc)
        elif key in linker.ambiguous:
            tip = "More than one match, not linked: " + ", ".join(linker.ambiguous[key])
            out = '<code class="term ambiguous" title="%s">%s</code>' % (html.escape(tip), esc)
        else:
            out = "<code>%s</code>" % esc
        spans.append(out)
        return "\x00%d\x00" % (len(spans) - 1)

    text = CODE_SPAN.sub(keep, text)
    text = html.escape(text, quote=False)

    def img(m):
        src = html.unescape(m.group(2))
        if src.startswith(("http://", "https://")) and not internet:
            return '<span class="blocked">[online image hidden: internet is off]</span>'
        url = image_src(src, rel)
        if url is None:
            return '<span class="blocked">[image not shown: outside the project]</span>'
        return '<img alt="%s" src="%s">' % (html.escape(html.unescape(m.group(1))), html.escape(url))

    def link(m):
        target = html.unescape(m.group(2))
        if target.startswith(("http://", "https://")):
            return '<a class="web" href="%s">%s</a>' % (html.escape(target), m.group(1))
        return '<a class="doclink" data-target="%s" data-from="%s">%s</a>' % (
            html.escape(target), html.escape(rel), m.group(1))

    text = IMG.sub(img, text)
    text = LINK.sub(link, text)
    text = BOLD.sub(r"<b>\1</b>", text)
    text = ITALIC.sub(r"<i>\1</i>", text)
    return re.sub("\x00(\\d+)\x00", lambda m: spans[int(m.group(1))], text)


def block(line_no, inner, cls=""):
    return '<div class="blk %s" id="L%d"><span class="no">%d</span><div class="body">%s</div></div>' % (
        cls, line_no, line_no, inner)


def markdown(source, rel, linker, internet):
    lines = source.splitlines()
    out, i, seen = [], 0, {}
    while i < len(lines):
        text, no = lines[i], i + 1
        if FENCE.match(text):
            j = i + 1
            while j < len(lines) and not FENCE.match(lines[j]):
                j += 1
            code = "\n".join(lines[i + 1:j])
            out.append(block(no, "<pre>%s</pre>" % html.escape(code), "code"))
            i = j + 1
            continue
        m = HEADING.match(text)
        if m:
            base = slug(m.group(2))
            anchor = base if base not in seen else "%s-%d" % (base, seen[base])
            seen[base] = seen.get(base, 0) + 1
            level = len(m.group(1))
            out.append(block(no, '<h%d id="h-%s" data-node="doc:%s#%s">%s</h%d>' % (
                level, html.escape(anchor), html.escape(rel), html.escape(anchor),
                inline(m.group(2), rel, no, linker, internet), level), "heading"))
            i += 1
            continue
        if text.strip().startswith("|") and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1]):
            rows, j = [], i
            while j < len(lines) and lines[j].strip().startswith("|"):
                if j != i + 1:
                    cells = table_cells(lines[j])
                    tag = "th" if j == i else "td"
                    rows.append("<tr>%s</tr>" % "".join("<%s>%s</%s>" % (tag, inline(c, rel, j + 1, linker, internet), tag)
                                                          for c in cells))
                j += 1
            out.append(block(no, "<table>%s</table>" % "".join(rows)))
            i = j
            continue
        lm = LIST.match(text)
        if lm:
            depth = len(lm.group(1).replace("\t", "    ")) // 2
            out.append(block(no, '<div class="li" style="margin-left:%dem">%s %s</div>' % (
                depth * 1.2, "&bull;" if lm.group(2) in "-*+" else html.escape(lm.group(2)),
                inline(lm.group(3), rel, no, linker, internet))))
            i += 1
            continue
        if text.startswith(">"):
            out.append(block(no, "<blockquote>%s</blockquote>" % inline(text.lstrip("> "), rel, no, linker, internet)))
        elif re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", text):
            out.append(block(no, "<hr>"))
        elif text.strip():
            out.append(block(no, "<p>%s</p>" % inline(text, rel, no, linker, internet)))
        i += 1
    return '<div class="doc">%s</div>' % "".join(out)


def code(source, rel, linker):
    rows = []
    for no, text in enumerate(source.splitlines(), 1):
        esc = html.escape(text)
        d = linker.defs.get((rel, no))
        cls = ' class="def"' if d else ""
        node = ' data-node="%s"' % html.escape(d["id"]) if d else ""
        chips = "".join(
            '<span class="chip %s %s" data-node="%s" title="%s (%s)">&rarr; %s</span>' % (
                e["certainty"], e["kind"], html.escape(n["id"]), e["kind"], e["certainty"], html.escape(n["name"]))
            for e, n in linker.from_line.get((rel, no), []))
        rows.append('<div class="ln" id="L%d"%s><span class="no">%d</span><code%s>%s</code>%s</div>' % (
            no, node, no, cls, esc or " ", '<span class="chips">%s</span>' % chips if chips else ""))
    return '<div class="src">%s</div>' % "".join(rows)


def text_file(source):
    return '<div class="src">%s</div>' % "".join(
        '<div class="ln" id="L%d"><span class="no">%d</span><code>%s</code></div>' % (no, no, html.escape(t) or " ")
        for no, t in enumerate(source.splitlines(), 1))
