"""Facts from Rust, C, C++ and C# using tree-sitter (a syntax parser).

tree-sitter knows the shape of the code, not the types. So:
  certain  - definitions, imports/includes (written in the code)
  resolved - calls proven by a simple rule: self/this calls inside the same type, Type::f / Type.f with a
             project type, a module path matching a project file, or a name defined exactly once in the project
  unknown  - everything else (method calls on variables, library calls, names defined more than once)
Known library calls that touch files or run commands (table in ts_effects.py) become reads/writes/... and
runs_command links, marked resolved, only when the path or command is a plain string in the code.
Two passes: first all definitions in the whole project, then calls, so calls across files can be resolved.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# One folder per Python version: the parsers are compiled for the Python that installed them.
DEPS = os.path.join(os.path.dirname(HERE), ".deps", "py%d.%d" % sys.version_info[:2])
if os.path.isdir(DEPS) and DEPS not in sys.path:
    sys.path.insert(0, DEPS)
try:
    from . import ts_effects as fx
except ImportError:  # run as a plain script
    import ts_effects as fx

EXT = {".rs": "rust", ".c": "c", ".h": "c?", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
       ".hh": "cpp", ".hxx": "cpp", ".cs": "c_sharp"}
MODULES = {"rust": "tree_sitter_rust", "c": "tree_sitter_c", "cpp": "tree_sitter_cpp", "c_sharp": "tree_sitter_c_sharp"}
TYPES = {"struct_item", "enum_item", "trait_item", "union_item", "struct_specifier", "class_specifier",
         "union_specifier", "class_declaration", "struct_declaration", "interface_declaration", "enum_declaration",
         "record_declaration"}
NAMESPACES = {"namespace_definition", "namespace_declaration", "file_scoped_namespace_declaration"}
FUNCS = {"function_item", "function_signature_item", "function_definition", "method_declaration",
         "constructor_declaration", "local_function_statement"}
CALLS = {"call_expression", "invocation_expression"}


def available():
    """True when the parsers load and really work on this Python (an older tree-sitter has another API)."""
    import importlib
    if os.path.isdir(DEPS) and DEPS not in sys.path:
        sys.path.insert(0, DEPS)
        importlib.invalidate_caches()
    try:
        import tree_sitter as ts
        for mod in MODULES.values():
            ts.Parser(ts.Language(importlib.import_module(mod).language()))
        return True
    except Exception:
        return False


def languages_for(files):
    """Which grammar each file uses. .h is C++ when the project has C++ files, else C."""
    has_cpp = any(EXT.get(os.path.splitext(f)[1]) == "cpp" for f in files)
    out = {}
    for f in files:
        lang = EXT.get(os.path.splitext(f)[1])
        if lang == "c?":
            lang = "cpp" if has_cpp else "c"
        if lang:
            out[f] = lang
    return out


class Project(object):
    def __init__(self, root, files):
        import importlib
        import tree_sitter as ts
        self.root = root
        self.langs = languages_for(files)
        self.parsers = {}
        for lang in set(self.langs.values()):
            mod = importlib.import_module(MODULES[lang])
            self.parsers[lang] = ts.Parser(ts.Language(mod.language()))
        self.nodes, self.edges, self.unknown, self.missing = [], [], [], []
        self.trees = {}
        self.defs = {}        # short name ("helper", "App.run") -> [ids]
        self.types = {}       # type name -> [ids]
        self.members = {}     # (type name, member) -> [ids]
        self.by_id = {}
        self.names_cache, self.headers_cache = {}, {}
        self.fn_node = {}      # function id -> its tree node
        self.calls_made = []   # (file, call node, caller id, callee id, argument shift) for wrapper functions
        self.wrappers = {}     # function id -> [(what, kind, parameter number, library name)]
        self.cache = {}

    # ---------- helpers ----------
    def text(self, rel, n):
        return self.trees[rel][1][n.start_byte:n.end_byte].decode("utf-8", "replace")

    def line_of(self, rel, n):
        return n.start_point[0] + 1

    def name_line(self, n):
        """Line of a definition's name: the return type or [Attributes] can be on lines above it."""
        name = n.child_by_field_name("name")
        if name is None:
            name = n.child_by_field_name("declarator")
            while name is not None and name.type != "function_declarator":
                name = name.child_by_field_name("declarator")
        return (name if name is not None else n).start_point[0] + 1

    def add_node(self, nid, kind, name, rel, n, parent, **extra):
        if nid in self.by_id:
            nid = "%s@%d" % (nid, n.start_point[0] + 1)  # overloads: same name, different line
        node = {"id": nid, "kind": kind, "name": name, "file": rel, "line": self.name_line(n),
                "end_line": n.end_point[0] + 1, "parent": parent}
        node.update(extra)
        self.nodes.append(node)
        self.by_id[nid] = node
        return nid

    def edge(self, src, dst, kind, certainty, rel, line, **extra):
        e = {"from": src, "to": dst, "kind": kind, "certainty": certainty, "file": rel, "line": line}
        e.update(extra)
        self.edges.append(e)

    def ext(self, name):
        nid = "mod:" + name
        if nid not in self.by_id:
            self.by_id[nid] = {"id": nid, "kind": "external_module", "name": name, "file": None, "line": None,
                               "end_line": None, "parent": None}
            self.nodes.append(self.by_id[nid])
        return nid

    def unknown_call(self, rel, n, where, reason):
        line = n.start_point[0] + 1
        code = self.trees[rel][1].splitlines()[line - 1].decode("utf-8", "replace").strip()
        self.unknown.append({"in": where, "file": rel, "line": line, "code": code, "reason": reason})

    # ---------- pass 1: definitions ----------
    def scan(self):
        for rel, lang in sorted(self.langs.items()):
            with open(os.path.join(self.root, rel), "rb") as f:
                src = f.read()
            self.trees[rel] = (self.parsers[lang].parse(src), src, lang)
            mod_id = "ts:" + rel
            self.by_id[mod_id] = {"id": mod_id, "kind": "module", "name": rel, "file": rel, "line": 1,
                                  "end_line": src.count(b"\n") + 1, "parent": None, "lang": lang}
            self.nodes.append(self.by_id[mod_id])
            self.define(rel, self.trees[rel][0].root_node, mod_id, None)
            if lang == "rust":
                self.macro_fns(rel)
        self.attach_out_of_class()
        for rel in sorted(self.trees):
            self.uses(rel, self.trees[rel][0].root_node, "ts:" + rel, None)
        self.wrapper_calls()
        return self

    def macro_fns(self, rel):
        """fn written inside a macro (proptest! { fn ... }, macro_rules!): tree-sitter does not read macros,
        so each one is listed as not understood instead of being silently missing."""
        stack = [self.trees[rel][0].root_node]
        while stack:
            n = stack.pop()
            if n.type not in ("macro_invocation", "macro_definition"):
                stack.extend(n.children)
                continue
            mac = self.text(rel, n).split("!", 1)[0].strip()
            tokens, inner = [], [n]
            while inner:  # the macro's tokens in order; text inside strings is not a token
                t = inner.pop()
                if t.child_count == 0:
                    tokens.append(t)
                elif "string" not in t.type:
                    inner.extend(reversed(t.children))
            for a, b in zip(tokens, tokens[1:]):
                if a.type == "fn" and b.type == "identifier":
                    line = a.start_point[0] + 1
                    code = self.trees[rel][1].splitlines()[line - 1].decode("utf-8", "replace").strip()
                    self.unknown.append({"in": "ts:" + rel, "file": rel, "line": line, "code": code,
                                         "macro_fn": self.text(rel, b),
                                         "reason": "fn inside a macro (%s!): the parser does not read macros" % mac})

    def func_name(self, rel, n):
        """(owner type or None, name) of a function definition."""
        name = n.child_by_field_name("name")
        if name is not None:
            return None, self.text(rel, name)
        d = n.child_by_field_name("declarator")
        while d is not None and d.type not in ("function_declarator",):
            d = d.child_by_field_name("declarator")
        if d is None:
            return None, None
        inner = d.child_by_field_name("declarator")
        if inner is None:
            return None, None
        if inner.type == "qualified_identifier":  # C++: void Widget::draw()
            scope, nm = inner.child_by_field_name("scope"), inner.child_by_field_name("name")
            return (self.text(rel, scope) if scope is not None else None), (self.text(rel, nm) if nm is not None else None)
        return None, self.text(rel, inner)

    def define(self, rel, n, parent, owner):
        for ch in n.children:
            t = ch.type
            if t in TYPES and ch.child_by_field_name("body") is not None:
                name = self.text(rel, ch.child_by_field_name("name")) if ch.child_by_field_name("name") is not None else None
                if not name:
                    continue
                nid = self.add_node("ts:%s:%s" % (rel, name), "class", name, rel, ch, parent, type_kind=t.split("_")[0])
                self.types.setdefault(name, []).append(nid)
                self.edge(parent, nid, "contains", "certain", rel, ch.start_point[0] + 1)
                self.define(rel, ch.child_by_field_name("body"), nid, name)
            elif (t in NAMESPACES or t == "mod_item" and ch.child_by_field_name("body") is not None) \
                    and ch.child_by_field_name("name") is not None:  # mod_item: Rust "mod tests { ... }"
                name = self.text(rel, ch.child_by_field_name("name"))
                nid = self.add_node("ts:%s:%s" % (rel, name), "namespace", name, rel, ch, parent)
                self.edge(parent, nid, "contains", "certain", rel, ch.start_point[0] + 1)
                self.define(rel, ch.child_by_field_name("body") or ch, nid, owner)
            elif t == "impl_item":  # Rust: methods belong to the type after "impl ... for"
                ty = ch.child_by_field_name("type")
                name = self.text(rel, ty).split("<")[0] if ty is not None else None
                body = ch.child_by_field_name("body")
                if body is not None and name:
                    self.define(rel, body, parent, name)
            elif t in FUNCS:
                scope, name = self.func_name(rel, ch)
                if not name:
                    continue
                own = scope or owner
                short = "%s.%s" % (own, name) if own else name
                kind = "method" if own else "function"
                nid = self.add_node("ts:%s:%s" % (rel, short), kind, name, rel, ch, parent, owner=own,
                                    body=ch.child_by_field_name("body") is not None)
                self.defs.setdefault(short, []).append(nid)
                self.fn_node[nid] = ch
                if own:
                    self.members.setdefault((own, name), []).append(nid)
                else:
                    self.defs.setdefault(name, [])
                self.edge(parent, nid, "contains", "certain", rel, ch.start_point[0] + 1)
            elif t in ("declaration_list", "field_declaration_list", "linkage_specification", "preproc_ifdef",
                       "preproc_if", "preproc_else", "template_declaration",
                       "field_declaration", "declaration", "type_definition"):  # struct X {...} inside these
                self.define(rel, ch, parent, owner)
            elif t in ("const_item", "static_item") and ch.child_by_field_name("name") is not None:
                name = self.text(rel, ch.child_by_field_name("name"))
                nid = self.add_node("ts:%s:%s" % (rel, name), "constant", name, rel, ch, parent)
                self.edge(parent, nid, "contains", "certain", rel, ch.start_point[0] + 1)

    def attach_out_of_class(self):
        """C++ 'void Widget::draw()' in a .cpp file: its parent is class Widget if that class is in the project once."""
        for node in self.nodes:
            if node["kind"] == "method" and node.get("owner") and self.by_id[node["parent"]]["kind"] != "class":
                owners = self.types.get(node["owner"], [])
                if len(owners) == 1:
                    node["parent"] = owners[0]
                    for e in self.edges:
                        if e["to"] == node["id"] and e["kind"] == "contains":
                            e["from"] = owners[0]

    # ---------- pass 2: imports and calls ----------
    def uses(self, rel, n, where, owner):
        for ch in n.children:
            t = ch.type
            here, own = where, owner
            if t in FUNCS:
                scope, name = self.func_name(rel, ch)
                o = scope or owner
                short = "%s.%s" % (o, name) if o else name
                cands = [i for i in self.defs.get(short, []) if self.by_id[i]["file"] == rel
                         and self.by_id[i]["line"] == self.name_line(ch)]
                here, own = (cands[0] if cands else where), o
            elif t in TYPES and ch.child_by_field_name("name") is not None:
                own = self.text(rel, ch.child_by_field_name("name"))
            elif t == "impl_item" and ch.child_by_field_name("type") is not None:
                own = self.text(rel, ch.child_by_field_name("type")).split("<")[0]
            if t == "use_declaration":
                arg = ch.child_by_field_name("argument")
                first = re.split(r"::|\{", self.text(rel, arg))[0].strip() if arg is not None else ""
                if first and first not in ("crate", "self", "super"):
                    self.edge("ts:" + rel, self.ext(first), "imports", "certain", rel, ch.start_point[0] + 1, name=self.text(rel, arg))
            elif t == "using_directive" and ch.named_children:
                name = self.text(rel, ch.named_children[-1])  # using X; using A = X; using static X;
                self.edge("ts:" + rel, self.ext(name), "imports", "certain", rel, ch.start_point[0] + 1, name=name)
            elif t == "preproc_include":
                self.include(rel, ch)
            elif t in CALLS:
                self.call(rel, ch, here, own)
            elif t == "object_creation_expression":
                ty = ch.child_by_field_name("type")
                name = self.text(rel, ty) if ty is not None else ""
                if len(self.types.get(name, [])) == 1:
                    self.edge(here, self.types[name][0], "instantiates", "resolved", rel, ch.start_point[0] + 1)
                elif not self.types.get(name):
                    self.new_effect(rel, ch, name, ch.child_by_field_name("arguments"), here, fx.CS_NEW)
            elif t == "declaration" and self.trees[rel][2] == "cpp" and ch.child_by_field_name("type") is not None:
                ty, d = self.text(rel, ch.child_by_field_name("type")), ch.child_by_field_name("declarator")
                if d is not None and d.type == "init_declarator" and not self.types.get(ty):
                    self.new_effect(rel, ch, ty, d.child_by_field_name("value"), here, fx.CPP_STREAMS)
                elif d is not None and d.type == "function_declarator" and not self.types.get(ty):
                    # std::ifstream in(path); is parsed like a function declaration. It is a variable only when
                    # path is a variable or constant (C++ reads it as a function when path is a type).
                    ps = d.child_by_field_name("parameters")
                    ps = ps.named_children if ps is not None else []
                    names = [p.child_by_field_name("type") for p in ps if p.child_by_field_name("declarator") is None]
                    if ps and len(names) == len(ps) and self.is_variable(rel, names[0], here):
                        self.new_effect(rel, ch, ty, names, here, fx.CPP_STREAMS)
            self.uses(rel, ch, here, own)

    def include(self, rel, n):
        path = n.child_by_field_name("path")
        if path is None:
            return
        line = n.start_point[0] + 1
        if path.type == "system_lib_string":
            name = self.text(rel, path).strip("<>")
            self.edge("ts:" + rel, self.ext(name), "imports", "certain", rel, line, name=name)
            return
        name = self.text(rel, path).strip('"')
        target = os.path.normpath(os.path.join(os.path.dirname(rel), name))
        if "ts:" + target in self.by_id:
            self.edge("ts:" + rel, "ts:" + target, "includes", "certain", rel, line)
        else:
            self.unknown_call(rel, n, "ts:" + rel, "include not found in project: " + name)

    def one(self, ids):
        return ids[0] if len(ids) == 1 else None

    def call(self, rel, n, where, owner):
        f = n.child_by_field_name("function")
        if f is None:
            f = n.children[0] if n.children else None
        if f is None:
            return
        line = n.start_point[0] + 1
        lang = self.trees[rel][2]
        target, reason = None, None
        if f.type == "identifier":
            name = self.text(rel, f)
            if owner and self.one(self.members.get((owner, name), [])):
                target = self.members[(owner, name)][0]  # bare call inside a type to its own member
            else:
                same_file = [i for i in self.defs.get(name, []) if self.by_id[i]["file"] == rel and self.by_id[i]["kind"] == "function"]
                anywhere = [i for i in self.defs.get(name, []) if self.by_id[i]["kind"] == "function"]
                target = self.one(same_file) or (self.one(anywhere) if lang != "c_sharp" else None)
                if target is None:
                    reason = ("name '%s' is defined %d times" % (name, len(anywhere))) if len(anywhere) > 1 \
                        else "call to '%s', not defined in the project (library?)" % name
        elif f.type in ("field_expression", "member_access_expression"):
            recv = f.child_by_field_name("value") or f.child_by_field_name("argument") or f.child_by_field_name("expression")
            fld = f.child_by_field_name("field") or f.child_by_field_name("name")
            name = self.text(rel, fld) if fld is not None else "?"
            rtext = self.text(rel, recv) if recv is not None else ""
            if rtext in ("self", "this") and owner and self.one(self.members.get((owner, name), [])):
                target = self.members[(owner, name)][0]
            elif f.type == "member_access_expression" and self.one(self.types.get(rtext, [])) \
                    and self.one(self.members.get((rtext, name), [])):
                target = self.members[(rtext, name)][0]  # C#: Helper.Run()
            else:
                reason = "method call on a value of unknown type (%s.%s)" % (rtext[:30], name)
        elif f.type in ("scoped_identifier", "qualified_identifier"):
            scope_n = f.child_by_field_name("path") or f.child_by_field_name("scope")
            name_n = f.child_by_field_name("name")
            scope = self.text(rel, scope_n) if scope_n is not None else ""
            name = self.text(rel, name_n) if name_n is not None else "?"
            if self.one(self.types.get(scope, [])) and self.one(self.members.get((scope, name), [])):
                target = self.members[(scope, name)][0]  # App::new()
            else:
                mod_files = [r for r in self.trees if os.path.splitext(os.path.basename(r))[0] == scope.split("::")[-1]
                             or r.endswith("/%s/mod.rs" % scope.split("::")[-1])]
                cands = [i for r in mod_files for i in self.defs.get(name, [])
                         if self.by_id[i]["file"] == r and self.by_id[i]["kind"] == "function"]
                target = self.one(cands) if lang == "rust" else None
                if target is None:
                    reason = "call to %s::%s, not found in the project (library?)" % (scope, name)
        else:
            reason = "call on an expression"
        if target:
            self.edge(where, target, "calls", "resolved", rel, line)
            shift = 1 if f.type == "scoped_identifier" and self.self_param(target) else 0  # App::save(&app, "x")
            self.calls_made.append((rel, n, where, target, shift))
        elif not self.effect(rel, n, f, where):
            self.unknown_call(rel, n, where, reason)


    # ---------- files and commands (known library calls) ----------
    def names(self, rel):
        """(alias -> full name, open prefixes) from use / using / namespace-alias lines anywhere in the file."""
        if rel in self.names_cache:
            return self.names_cache[rel]
        alias, opens = {}, []
        lang = self.trees[rel][2]

        def rust_use(n, prefix):
            t = n.type
            if t in ("identifier", "scoped_identifier", "crate", "super"):
                full = prefix + self.text(rel, n)
                alias[full.split("::")[-1]] = full
            elif t == "self" and prefix:
                alias[prefix[:-2].split("::")[-1]] = prefix[:-2]
            elif t == "use_as_clause":
                path, al = n.child_by_field_name("path"), n.child_by_field_name("alias")
                if path is not None and al is not None:
                    alias[self.text(rel, al)] = prefix + self.text(rel, path)
            elif t == "use_wildcard":
                opens.append(prefix + self.text(rel, n).rstrip("*").rstrip(":"))
            elif t == "scoped_use_list":
                path = n.child_by_field_name("path")
                sub = prefix + (self.text(rel, path) + "::" if path is not None else "")
                lst = n.child_by_field_name("list")
                for c in (lst.named_children if lst is not None else []):
                    rust_use(c, sub)
            elif t == "use_list":
                for c in n.named_children:
                    rust_use(c, prefix)

        stack = [self.trees[rel][0].root_node]
        while stack:
            n = stack.pop()
            stack.extend(n.children)
            t = n.type
            if t == "use_declaration" and n.child_by_field_name("argument") is not None:
                rust_use(n.child_by_field_name("argument"), "")
            elif t == "namespace_alias_definition" and n.child_by_field_name("name") is not None:
                alias[self.text(rel, n.child_by_field_name("name"))] = self.text(rel, n.named_children[-1])
            elif t == "using_declaration" and lang == "cpp":
                target = self.text(rel, n.named_children[-1]) if n.named_children else ""
                if any(c.type == "namespace" for c in n.children):
                    opens.append(target)  # using namespace std;
                else:
                    alias[target.split("::")[-1]] = target  # using std::ifstream;
            elif t == "using_directive" and not any(c.type == "static" for c in n.children):
                name = n.child_by_field_name("name")
                target = self.text(rel, n.named_children[-1]) if n.named_children else ""
                if name is not None:
                    alias[self.text(rel, name)] = target  # using IO = System.IO;
                else:
                    opens.append(target)
        self.names_cache[rel] = (alias, opens)
        return alias, opens

    def full_names(self, rel, text):
        """Every full name a written name can stand for, given the file's use/using lines."""
        sep = "." if self.trees[rel][2] == "c_sharp" else "::"
        text = re.sub(r"\s+", "", text)
        alias, opens = self.names(rel)
        first, _, rest = text.partition(sep)
        out = [text]
        if first in alias:
            out.append(alias[first] + (sep + rest if rest else ""))
        out += [p + sep + text for p in opens]
        return out

    def headers(self, rel, seen=None):
        """Headers included by this file, also through project headers it includes."""
        if rel in self.headers_cache:
            return self.headers_cache[rel]
        seen = seen or set()
        seen.add(rel)
        out = set()
        stack = [self.trees[rel][0].root_node]
        while stack:
            n = stack.pop()
            stack.extend(n.children)
            path = n.child_by_field_name("path") if n.type == "preproc_include" else None
            if path is None:
                continue
            name = self.text(rel, path).strip('<>"')
            out.add(name)
            target = os.path.normpath(os.path.join(os.path.dirname(rel), name))
            if target in self.trees and target not in seen:
                out |= self.headers(target, seen)
        self.headers_cache[rel] = out
        return out

    def plain(self, rel, n):
        """Text of a plain string literal; None for anything else (escapes, formatting, variables)."""
        if n is not None and n.type == "argument" and n.named_children:
            n = n.named_children[-1]  # C#: argument wraps the value
        if n is None:
            return None
        if n.type == "string_literal" and all(c.type in ('"', "string_content", "string_literal_content") for c in n.children):
            return "".join(self.text(rel, c) for c in n.children if c.type != '"')
        if n.type == "raw_string_literal" and self.trees[rel][2] == "rust":
            return "".join(self.text(rel, c) for c in n.children if c.type == "string_content")
        if n.type == "verbatim_string_literal":
            return self.text(rel, n)[2:-1].replace('""', '"')
        return None

    def arg_list(self, args):
        return [c for c in (args.named_children if args is not None else []) if "comment" not in c.type]

    def file_node(self, text):
        root = os.path.abspath(self.root)
        if os.path.isabs(text) and text.startswith(root + os.sep):
            text = os.path.relpath(text, root)
        nid = "file:" + text
        if nid not in self.by_id:
            folder = os.path.isdir(os.path.join(root, text))
            self.by_id[nid] = {"id": nid, "kind": "folder" if folder else "file", "name": text, "file": None,
                               "line": None, "end_line": None, "parent": None, "exact": True}
            self.nodes.append(self.by_id[nid])
        return nid

    def cmd(self, rel, n, where, words, **extra):
        nid = "cmd:" + words[0]
        if nid not in self.by_id:
            self.by_id[nid] = {"id": nid, "kind": "command", "name": words[0], "file": None, "line": None,
                               "end_line": None, "parent": None}
            self.nodes.append(self.by_id[nid])
        self.edge(where, nid, "runs_command", "resolved", rel, n.start_point[0] + 1, args=words, **extra)

    def lookup(self, rel, f):
        """(full name, spec) when the called name is a known library call, else (None, None)."""
        lang = self.trees[rel][2]
        text = self.text(rel, f)
        if f.type == "identifier" and self.defs.get(text):
            return None, None  # the project defines this name itself
        for full in self.full_names(rel, text):
            if lang == "rust" and full in fx.RUST:
                return full, fx.RUST[full]
            if lang == "c_sharp" and full in fx.CS:
                return full, fx.CS[full]
            if lang == "cpp" and full in fx.CPP:
                return full, fx.CPP[full]
            if lang == "cpp" and full.startswith("std::") and full[5:] in fx.C:
                return full, fx.C[full[5:]][0]
        if lang in ("c", "cpp") and f.type == "identifier" and text in fx.C and fx.C[text][1] & self.headers(rel):
            return text, fx.C[text][0]
        return None, None

    def effect(self, rel, n, f, where):
        """Record a known library call. True when the call was understood (recorded or listed with a reason)."""
        full, spec = self.lookup(rel, f)
        if spec is None:
            return False
        args = self.arg_list(n.child_by_field_name("arguments"))
        self.apply(rel, n, where, full, spec, args)
        return True

    def new_effect(self, rel, n, type_text, args, where, table):
        """new StreamReader("x") in C#, std::ifstream in("x") in C++."""
        for full in self.full_names(rel, type_text):
            if full in table:
                vals = args if isinstance(args, list) else \
                    [args] if args is not None and args.type == "string_literal" else self.arg_list(args)
                return self.apply(rel, n, where, full, [(table[full], 0)], vals)

    def apply(self, rel, n, where, full, spec, args):
        """Links for a known library call. The path or command is a plain string, a variable or constant that
        holds one for sure (see value), or a parameter of the function it is in: then that function is a
        wrapper and the links are made where the wrapper is called (wrapper_calls)."""
        if spec in ("fopen", "open"):
            if len(args) < 2:
                return self.unknown_call(rel, n, where, "mode for %s not given" % full)
            if spec == "fopen":
                mode = self.value(rel, args[1], where)
                kind = None if not isinstance(mode, tuple) else ("writes" if any(c in mode[0] for c in "wa+") else "reads")
            else:
                flags = self.text(rel, args[1])
                kind = "writes" if any(w in flags for w in fx.WRITE_FLAGS) else ("reads" if "O_RDONLY" in flags else None)
            if kind is None:
                return self.unknown_call(rel, n, where, "file mode for %s not known" % full)
            items = [("file", kind, 0)]
        elif spec in ("shell", "exec"):
            items = [(spec, None, 0)]
        else:
            items = [("file", kind, idx) for kind, idx in spec]
        for what, kind, idx in items:
            v = self.value(rel, args[idx], where) if idx < len(args) else None
            if isinstance(v, int):
                self.wrappers.setdefault(where, []).append((what, kind, v, full))
            elif v is None:
                self.unknown_call(rel, n, where, "%s for %s not known (not a plain string, or a variable that can change)"
                                  % ("path" if what == "file" else "command", full))
            else:
                extra = {"chained": self.chained_args(rel, n, args)} if what == "exec" else {}
                self.record(rel, n, where, what, kind, v, full, **extra)

    def record(self, rel, n, where, what, kind, v, name, chained=(), **extra):
        """One file or command link. v = (text, file, line) of the string; its place is saved when it is
        written outside the call (a variable or constant), so the self-check can find it."""
        text, vfile, vline = v[:3]
        line, end = n.start_point[0] + 1, n.end_point[0] + 1
        extra.update(name=name, end_line=end)
        if vfile != rel or not line <= vline <= end:
            extra.update(value_file=vfile, value_line=vline)
        if len(v) > 3:
            extra.update(value_name=v[3])
        if what == "file":
            return self.edge(where, self.file_node(text), kind, "resolved", rel, line, **extra)
        if what == "exec":
            words = [text] + list(chained)
        elif fx.SHELL_SPECIAL & set(text):
            self.unknown_call(rel, n, where, "shell line for %s has | ; & $ or quotes: only the first word is shown" % name)
            words = re.findall(r"^\s*([\w./-]+)(?=\s|$)", text)
        else:
            words = text.split()
        if words:
            self.cmd(rel, n, where, words, **extra)

    def wrapper_calls(self):
        """Calls to a wrapper function (one that hands its parameter to a file or command call)."""
        for rel, n, where, target, shift in self.calls_made:
            if not self.wrappers.get(target):
                continue
            args = self.arg_list(n.child_by_field_name("arguments"))
            wname = self.by_id[target]["name"]
            for what, kind, idx, full in self.wrappers[target]:
                v = self.value(rel, args[idx + shift], where) if idx + shift < len(args) else None
                if isinstance(v, tuple):
                    self.record(rel, n, where, what, kind, v, wname, via=wname, library=full)
                else:  # a parameter again (wrapper of a wrapper) is not followed
                    self.unknown_call(rel, n, where, "%s passed to %s not known" % ("path" if what == "file" else "command", wname))

    # ---------- what a name holds ----------
    def value(self, rel, n, where):
        """What an argument holds for sure: (text, file, line) of a plain string; an int i when it is
        parameter i of the function `where` and never changed there; None when not known."""
        if n is not None and n.type == "argument" and n.named_children:
            n = n.named_children[-1]
        if n is not None and n.type == "reference_expression" and not any(c.type == "mutable_specifier" for c in n.children):
            n = n.child_by_field_name("value")  # Rust &x: a read-only borrow holds the same value
        s = self.plain(rel, n)
        if s is not None:
            return s, rel, n.start_point[0] + 1
        if n is None or n.type not in ("identifier", "type_identifier"):
            return None
        name = self.text(rel, n)
        fn = self.fn_node.get(where)
        found = None
        if fn is not None:
            params = self.params(rel, fn)
            local = self.bindings(rel, fn).get(name)
            if local is not None and name in params:
                return None  # shadows a parameter: which one is meant depends on the order
            if local is not None:
                found = self.single(rel, local, self.changed(rel, fn, name))
            elif name in params:
                return None if self.changed(rel, fn, name) else params.index(name)
        if fn is None or (self.bindings(rel, fn).get(name) is None and name not in self.params(rel, fn)):
            glob = self.globals(rel).get(name)
            found = self.single(rel, glob, name in self.changed_anywhere()) if glob else None
        return found + (name,) if found else None  # the name is kept for the self-check

    def is_variable(self, rel, n, where):
        name = self.text(rel, n)
        fn = self.fn_node.get(where)
        return bool(fn is not None and (name in self.bindings(rel, fn) or name in self.params(rel, fn))
                    or name in self.globals(rel))

    def single(self, rel, defs, changed):
        """Known only when there is exactly one definition, it is a plain string, and nothing changes it."""
        if len(defs) == 1 and defs[0][0] is not None and not changed:
            return defs[0]
        return None

    def bindings(self, rel, scope, top=False):
        """name -> [(plain string or None, file, line)] for every variable/constant defined in scope
        (nested functions are left out; at the top level all function bodies are left out)."""
        key = ("bind", rel, scope.start_byte, scope.end_byte)
        if key in self.cache:
            return self.cache[key]
        out = {}
        lang = self.trees[rel][2]

        def add(name_n, val_n, ok=True):
            if name_n is None:
                return
            text = self.plain(rel, val_n) if ok and val_n is not None else None
            line = (val_n if val_n is not None else name_n).start_point[0] + 1
            out.setdefault(self.text(rel, name_n), []).append((text, rel, line) if text is not None else (None, rel, line))

        stack = list(scope.children)
        while stack:
            n = stack.pop()
            t = n.type
            if t in FUNCS:
                continue
            stack.extend(n.children)
            if t == "let_declaration":
                pat = n.child_by_field_name("pattern")
                if pat is not None and pat.type == "identifier":
                    add(pat, n.child_by_field_name("value"))
            elif t in ("const_item", "static_item"):
                add(n.child_by_field_name("name"), n.child_by_field_name("value"),
                    not any(c.type == "mutable_specifier" for c in n.children))
            elif t == "preproc_def" and n.child_by_field_name("name") is not None:
                val = n.child_by_field_name("value")
                m = re.match(r'^\s*"([^"\\]*)"\s*$', self.text(rel, val)) if val is not None else None
                out.setdefault(self.text(rel, n.child_by_field_name("name")), []).append(
                    (m.group(1), rel, n.start_point[0] + 1) if m else (None, rel, n.start_point[0] + 1))
            elif t == "declaration" and lang in ("c", "cpp"):
                ty = self.text(rel, n.child_by_field_name("type")) if n.child_by_field_name("type") is not None else ""
                const = any(c.type == "type_qualifier" and self.text(rel, c) in ("const", "constexpr") for c in n.children)
                for d in n.children_by_field_name("declarator"):
                    val = d.child_by_field_name("value") if d.type == "init_declarator" else None
                    inner = d.child_by_field_name("declarator") if d.type == "init_declarator" else d
                    mutable = inner is not None and inner.type == "array_declarator" or ("string" in ty and not const)
                    add(self.ident(inner), val, not mutable)
            elif t == "variable_declarator" and lang == "c_sharp":
                name_n = n.child_by_field_name("name")
                vals = [c for c in n.named_children if c != name_n]
                field = n.parent is not None and n.parent.parent is not None and n.parent.parent.type == "field_declaration"
                fixed = not field or any(c.type == "modifier" and self.text(rel, c) in ("const", "readonly")
                                         for c in n.parent.parent.children)
                add(name_n, vals[-1] if vals else None, fixed)
        self.cache[key] = out
        return out

    def ident(self, n):
        """The name inside a C/C++ declarator (*p, &p, p[], p)."""
        while n is not None and n.type != "identifier":
            n = n.child_by_field_name("declarator") or next((c for c in n.named_children if "declarator" in c.type
                                                              or c.type == "identifier"), None)
        return n

    def globals(self, rel):
        """Top-level names of this file, plus (C/C++) of project headers it includes."""
        key = ("glob", rel)
        if key not in self.cache:
            out = {}
            files = [rel] + [r for r in sorted(self.trees) if r != rel and self.trees[rel][2] in ("c", "cpp")
                             and r in self.project_headers(rel)]
            for r in files:
                for name, defs in self.bindings(r, self.trees[r][0].root_node, True).items():
                    out.setdefault(name, []).extend(defs)
            self.cache[key] = out
        return self.cache[key]

    def project_headers(self, rel, seen=None):
        seen = seen if seen is not None else set()
        stack = [self.trees[rel][0].root_node]
        while stack:
            n = stack.pop()
            stack.extend(n.children)
            path = n.child_by_field_name("path") if n.type == "preproc_include" else None
            if path is not None:
                target = os.path.normpath(os.path.join(os.path.dirname(rel), self.text(rel, path).strip('<>"')))
                if target in self.trees and target not in seen:
                    seen.add(target)
                    self.project_headers(target, seen)
        return seen

    def params(self, rel, fn):
        """Parameter names of a function, in order (Rust self left out)."""
        lang = self.trees[rel][2]
        if lang in ("c", "cpp"):
            d = fn.child_by_field_name("declarator")
            while d is not None and d.type != "function_declarator":
                d = d.child_by_field_name("declarator")
            plist = d.child_by_field_name("parameters") if d is not None else None
        else:
            plist = fn.child_by_field_name("parameters")
        out = []
        for p in (plist.named_children if plist is not None else []):
            if p.type == "self_parameter" or "comment" in p.type:
                continue
            if lang == "rust":
                pat = p.child_by_field_name("pattern")
                name = pat if pat is not None and pat.type == "identifier" else None
            elif lang == "c_sharp":
                name = p.child_by_field_name("name")
            else:
                name = self.ident(p.child_by_field_name("declarator"))
            out.append(self.text(rel, name) if name is not None else None)
        return out

    def self_param(self, fid):
        fn = self.fn_node.get(fid)
        plist = fn.child_by_field_name("parameters") if fn is not None else None
        return plist is not None and any(c.type == "self_parameter" for c in plist.named_children)

    def changed(self, rel, scope, name):
        """True when the name is assigned, or handed out to be changed (&x, &mut x, ref x, out x), in scope."""
        key = ("chg", rel, scope.start_byte, scope.end_byte)
        if key not in self.cache:
            out, stack = set(), [scope]
            while stack:
                n = stack.pop()
                stack.extend(n.children)
                if n.type in ("assignment_expression", "compound_assignment_expr"):
                    left = n.child_by_field_name("left")
                    if left is not None and left.type == "identifier":
                        out.add(self.text(rel, left))
                elif n.type in ("update_expression", "postfix_unary_expression", "prefix_unary_expression"):
                    out.update(self.text(rel, c) for c in n.named_children if c.type == "identifier")
            text = self.text(rel, scope)
            out.update(re.findall(r"&\s*(?:mut\s+)?(\w+)" if self.trees[rel][2] != "c_sharp" else r"\b(?:ref|out)\s+(\w+)", text))
            if self.trees[rel][2] == "rust":
                out -= set(re.findall(r"&\s*(\w+)", text)) - set(re.findall(r"&\s*mut\s+(\w+)", text))
            self.cache[key] = out
        return name in self.cache[key]

    def changed_anywhere(self):
        if "chg_all" not in self.cache:
            self.cache["chg_all"] = set()
            for r in self.trees:
                self.changed(r, self.trees[r][0].root_node, "")
                self.cache["chg_all"] |= self.cache[("chg", r, self.trees[r][0].root_node.start_byte,
                                                     self.trees[r][0].root_node.end_byte)]
        return self.cache["chg_all"]

    def chained_args(self, rel, n, args):
        """Leading plain-string arguments: Rust Command::new("ls").arg("-l"), C# Process.Start("ls", "-l -a")."""
        lang = self.trees[rel][2]
        if lang == "c_sharp":
            rest = self.plain(rel, args[1]) if len(args) > 1 else None
            return rest.split() if rest and not fx.SHELL_SPECIAL & set(rest) else []
        words, cur = [], n
        while lang == "rust" and cur.parent is not None and cur.parent.type == "field_expression" \
                and cur.parent.parent is not None and cur.parent.parent.type == "call_expression":
            field = cur.parent.child_by_field_name("field")
            a = self.arg_list(cur.parent.parent.child_by_field_name("arguments"))
            value = self.plain(rel, a[0]) if field is not None and self.text(rel, field) == "arg" and a else None
            if value is None:
                break
            words.append(value)
            cur = cur.parent.parent
        return words


def scan_tree_sitter(root, files):
    return Project(root, files).scan()
