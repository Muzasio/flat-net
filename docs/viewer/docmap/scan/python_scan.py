"""Facts from Python source, using Python's own parser (ast).

Rule: never guess. A fact is either
  certain  - written directly in the code (def, import, self.method() call), or
  resolved - proven through one simple, checkable step (a callback reference, a wrapper function), or
  unknown  - listed in the coverage report with file and line, and never drawn.
"""
import ast
import builtins
import io
import os
import re
import sys
import tokenize

BUILTINS = set(dir(builtins))
SUBPROCESS_FUNCS = {"run", "Popen", "call", "check_call", "check_output"}
# Library calls that act on a path given as the first argument: name -> edge kind.
PATH_OPS = {"os.listdir": "lists", "os.scandir": "lists", "os.walk": "lists",
            "os.makedirs": "creates", "os.mkdir": "creates", "tempfile.mkdtemp": "creates",
            "os.remove": "deletes", "os.unlink": "deletes", "os.rmdir": "deletes", "shutil.rmtree": "deletes",
            "os.rename": "moves", "os.replace": "moves", "shutil.move": "moves",
            "os.chmod": "changes", "os.path.exists": "checks", "os.path.isdir": "checks",
            "os.path.isfile": "checks", "os.readlink": "reads"}


def const_num(node):
    """A number written in the code (ast.Num before Python 3.8), else None."""
    value = node.value if isinstance(node, ast.Constant) else getattr(node, "n", None) \
        if type(node).__name__ == "Num" else None
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    legacy = getattr(ast, "Str", None)  # Python < 3.8
    if legacy is not None and isinstance(node, legacy):
        return node.s
    return None


def const_set(node):
    """All string values an expression can have, if they are all constants (e.g. "rb" if x else "r")."""
    s = const_str(node)
    if s is not None:
        return {s}
    if isinstance(node, ast.IfExp):
        a, b = const_set(node.body), const_set(node.orelse)
        return a | b if a is not None and b is not None else None
    return None


def dotted(node):
    """'os.path.join' for an Attribute chain on a plain name, else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return ".".join([node.id] + parts[::-1])
    return None


def local_defs(body):
    """def/class statements in a function body, also inside its try / if / for / while / with / match blocks,
    but not inside other functions or classes (those have their own names)."""
    out, todo = [], list(body)
    while todo:
        st = todo.pop(0)
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(st)
            continue
        todo[0:0] = [c for c in ast.iter_child_nodes(st)
                     if isinstance(c, (ast.stmt, ast.excepthandler)) or type(c).__name__ == "match_case"]
    return out


class PathValue(object):
    """A path that is fully known (exact) or known only in parts (pattern, '*' marks unknown parts)."""

    def __init__(self, text, exact):
        self.text, self.exact = text, exact


def const_prefix(items):
    """Leading constant strings of an argument list: what is known for sure about a command line."""
    out = []
    for item in items:
        s = const_str(item)
        if s is None:
            break
        out.append(s)
    return out


def command_list(node):
    """Known leading words of a command list, also through ["a", ...] + other."""
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        node = node.left
    if isinstance(node, (ast.List, ast.Tuple)):
        return const_prefix(node.elts)
    return []


class ModuleScanner(object):
    def __init__(self, root, rel):
        self.root, self.rel = root, rel
        self.path = os.path.join(root, rel)
        with open(self.path) as f:
            self.source = f.read()
        self.lines = self.source.splitlines()
        self.tree = ast.parse(self.source, filename=rel)
        self.mod_id = "py:" + rel
        self.nodes, self.edges, self.unknown, self.stats = [], [], [], {"builtin_calls": 0}
        self.aliases = {}      # local name -> full module/symbol name
        self.env = {}          # module constant name -> PathValue
        self.funcs = {}        # module function name -> id
        self.classes = {}      # class name -> info
        self.file_wrappers = {}  # function name -> (param index, set of modes)
        self.cmd_wrappers = {}   # function name -> param index or "*"
        self.nested = {}         # function id -> {nested function or class name: id}
        self.local_classes = set()  # ids of classes defined inside a function

    # ---------- helpers ----------
    def end_line(self, node):
        """Last line of a statement. Python 3.8+ records it. Before that: the last line where code inside
        starts, carried to the end of that logical line (multi-line strings, brackets) using the tokenizer."""
        end = getattr(node, "end_lineno", None)
        if end is not None:
            return end
        if not hasattr(self, "logical_end"):
            self.logical_end, start = {}, None
            for tok in tokenize.generate_tokens(io.StringIO("\n".join(self.lines) + "\n").readline):
                if start is None and tok.type not in (tokenize.NL, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT):
                    start = tok.start[0]
                if tok.type == tokenize.NEWLINE and start is not None:
                    for line in range(start, tok.end[0] + 1):
                        self.logical_end[line] = tok.end[0]
                    start = None
        last = max(getattr(n, "lineno", 0) for n in ast.walk(node))
        return self.logical_end.get(last, last)

    def def_line(self, node):
        """Line of 'def name' / 'class name'. Before Python 3.8 a decorated one reports its first @ line."""
        if sys.version_info >= (3, 8) or not node.decorator_list:
            return node.lineno
        word = "class" if isinstance(node, ast.ClassDef) else "def"
        pattern = re.compile(r"^\s*(?:async\s+)?%s\s+%s\b" % (word, re.escape(node.name)))
        for line in range(node.lineno, len(self.lines) + 1):
            if pattern.match(self.lines[line - 1]):
                return line
        return node.lineno

    def add_node(self, nid, kind, name, line=None, end=None, parent=None, **extra):
        n = {"id": nid, "kind": kind, "name": name, "file": self.rel if line else None,
             "line": line, "end_line": end, "parent": parent}
        n.update(extra)
        self.nodes.append(n)

    def add_edge(self, src, dst, kind, certainty, line, **extra):
        e = {"from": src, "to": dst, "kind": kind, "certainty": certainty, "file": self.rel, "line": line}
        e.update(extra)
        self.edges.append(e)

    def add_unknown(self, where, node, reason):
        line = getattr(node, "lineno", None)
        code = self.lines[line - 1].strip() if line else ""
        self.unknown.append({"in": where, "file": self.rel, "line": line, "code": code, "reason": reason})

    def ext(self, name, line):
        """Node for an outside module; returns its id."""
        top = name.split(".")[0]
        nid = "mod:" + top
        if not any(n["id"] == nid for n in self.nodes):
            self.add_node(nid, "external_module", top)
        return nid

    # ---------- path values ----------
    def path_value(self, node):
        s = const_str(node)
        if s is not None:
            return PathValue(s, True)
        if isinstance(node, ast.Name):
            if node.id == "__file__":
                return PathValue(os.path.abspath(self.path), True)
            return self.env.get(node.id)
        if isinstance(node, ast.Call):
            name = self.full_name(node.func)
            args = [self.path_value(a) for a in node.args]
            if name in ("os.path.abspath", "os.path.expanduser", "os.path.realpath") and len(args) == 1 and args[0]:
                return args[0]
            if name == "os.path.dirname" and len(args) == 1 and args[0]:
                return PathValue(os.path.dirname(args[0].text), args[0].exact)
            if name == "os.path.join" and args and any(args):
                return PathValue(os.path.join(*[a.text if a else "*" for a in args]), all(a and a.exact for a in args))
            return None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self.path_value(node.left), self.path_value(node.right)
            if left is None and right is None:
                return None
            return PathValue((left.text if left else "*") + (right.text if right else "*"),
                             bool(left and right and left.exact and right.exact))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            fmt = const_str(node.left)
            if fmt is None:
                return None
            for spec in ("%s", "%d", "%i"):
                fmt = fmt.replace(spec, "*")
            return PathValue(fmt, False)
        if isinstance(node, ast.JoinedStr):
            text = "".join(const_str(v) if const_str(v) is not None else "*" for v in node.values)
            return PathValue(text, False)
        return None

    def file_id(self, pv):
        text, root = pv.text, os.path.abspath(self.root)
        if os.path.isabs(text) and text.startswith(root + os.sep):
            text = os.path.relpath(text, root)
        nid = "file:" + text
        if not any(n["id"] == nid for n in self.nodes):
            folder = pv.exact and os.path.isdir(os.path.join(root, text))
            self.add_node(nid, "folder" if folder else "file", text, exact=pv.exact)
        return nid

    def full_name(self, func):
        """Full dotted name of a call target through imports, e.g. messagebox.showerror -> tkinter.messagebox.showerror."""
        d = dotted(func)
        if not d:
            return None
        head, _, rest = d.partition(".")
        if head in self.aliases:
            return self.aliases[head] + ("." + rest if rest else "")
        return None

    # ---------- passes ----------
    def scan(self):
        self.add_node(self.mod_id, "module", self.rel, 1, len(self.lines))
        body = self.tree.body
        for st in body:
            if isinstance(st, ast.Import):
                for a in st.names:
                    self.aliases[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
                    self.add_edge(self.mod_id, self.ext(a.name, st.lineno), "imports", "certain", st.lineno, name=a.name)
            elif isinstance(st, ast.ImportFrom) and st.module:
                for a in st.names:
                    self.aliases[a.asname or a.name] = st.module + "." + a.name
                    self.add_edge(self.mod_id, self.ext(st.module, st.lineno), "imports", "certain", st.lineno,
                                  name=st.module + "." + a.name)
        for st in body:
            if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
                pv = self.path_value(st.value)
                if pv:
                    self.env[st.targets[0].id] = pv
                self.add_node(self.mod_id + ":" + st.targets[0].id, "constant", st.targets[0].id, st.lineno,
                              self.end_line(st), parent=self.mod_id)
                self.add_edge(self.mod_id, self.mod_id + ":" + st.targets[0].id, "contains", "certain", st.lineno)
            elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.funcs[st.name] = self.mod_id + ":" + st.name
            elif isinstance(st, ast.ClassDef):
                self.classes[st.name] = self.class_info(st)
        for st in body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.find_wrappers(st)
        for st in body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.function(st, self.mod_id, None)
            elif isinstance(st, ast.ClassDef):
                self.class_def(st)
            elif not isinstance(st, (ast.Import, ast.ImportFrom)):
                self.walk(st, self.mod_id, None, later=False)  # module-level code
        return self

    def class_info(self, cls, cid=None):
        """cid: id for a class inside a class or function (Outer.Inner); top-level classes use module:Name."""
        cid = cid or "%s:%s" % (self.mod_id, cls.name)
        methods = {}
        for st in cls.body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods[st.name] = "%s.%s" % (cid, st.name)
        attrs = set()
        for n in ast.walk(cls):
            if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store) and isinstance(n.value, ast.Name) \
                    and n.value.id == "self":
                attrs.add(n.attr)
        bases = [self.full_name(b) or (dotted(b) if dotted(b) in self.classes else None) for b in cls.bases]
        return {"id": cid, "methods": methods, "attrs": attrs, "bases": bases,
                "name": cls.name}

    def find_wrappers(self, fn):
        params = [a.arg for a in fn.args.args]
        vararg = fn.args.vararg.arg if fn.args.vararg else None
        for n in ast.walk(fn):
            if not isinstance(n, ast.Call) or not n.args:
                continue
            first = n.args[0]
            if isinstance(n.func, ast.Name) and n.func.id == "open" and isinstance(first, ast.Name) \
                    and first.id in params:
                modes = const_set(n.args[1]) if len(n.args) > 1 else {"r"}
                self.file_wrappers[fn.name] = (params.index(first.id), modes)
            name = self.full_name(n.func) or ""
            if name.startswith("subprocess.") and name.split(".")[-1] in SUBPROCESS_FUNCS and isinstance(first, ast.Name):
                if first.id == vararg:
                    self.cmd_wrappers[fn.name] = "*"
                elif first.id in params:
                    self.cmd_wrappers[fn.name] = params.index(first.id)

    def class_def(self, cls, info=None, parent=None):
        info = info or self.classes[cls.name]
        parent = parent or self.mod_id
        self.add_node(info["id"], "class", cls.name, self.def_line(cls), self.end_line(cls), parent=parent)
        self.add_edge(parent, info["id"], "contains", "certain", self.def_line(cls))
        for b, bnode in zip(info["bases"], cls.bases):
            if b and b in self.classes:
                self.add_edge(info["id"], self.classes[b]["id"], "inherits", "certain", cls.lineno)
            elif b:
                self.add_edge(info["id"], self.ext(b, cls.lineno), "inherits", "certain", cls.lineno, name=b)
            else:
                self.add_unknown(info["id"], bnode, "base class not resolved")
        for st in cls.body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.function(st, info["id"], info)
            elif isinstance(st, ast.ClassDef):  # class inside a class
                self.class_def(st, self.class_info(st, info["id"] + "." + st.name), info["id"])

    def function(self, fn, parent, cls, nested=False):
        if nested:  # def inside a function: id under its parent; keeps the class unless it has its own "self"
            nid, kind = parent + "." + fn.name, "function"
            if "self" in [a.arg for a in fn.args.args]:
                cls = None
        else:
            nid = cls["methods"][fn.name] if cls else self.funcs.get(fn.name, parent + ":" + fn.name)
            kind = "method" if cls else "function"
        self.add_node(nid, kind, fn.name, self.def_line(fn), self.end_line(fn), parent=parent,
                      params=[a.arg for a in fn.args.args],
                      doc=(ast.get_docstring(fn) or "").split("\n")[0])
        self.add_edge(parent, nid, "contains", "certain", self.def_line(fn))
        # names of functions defined inside this one, so "worker" in self.after(10, worker) resolves.
        # Also inside its try / if / for / with blocks: those are still this function's local names.
        self.nested[nid] = {st.name: nid + "." + st.name for st in local_defs(fn.body)}
        for st in fn.body:
            self.walk(st, nid, cls, later=False)

    # ---------- body walk ----------
    def local_func(self, where, name):
        """A function defined inside 'where' or inside any function around it (Python's closure lookup)."""
        scope = where
        while scope in self.nested:
            if name in self.nested[scope]:
                return self.nested[scope][name]
            if "." not in scope.rsplit(":", 1)[-1] or scope.rsplit(".", 1)[0] not in self.nested:
                return None
            scope = scope.rsplit(".", 1)[0]
        return None

    def walk(self, node, where, cls, later, ctx=None):
        """ctx: when this node is passed straight into another call (a callback), which call and which argument."""
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return self.function(node, where, cls, nested=True)
        if isinstance(node, ast.ClassDef):  # class inside a function
            info = self.class_info(node, where + "." + node.name)
            self.local_classes.add(info["id"])
            return self.class_def(node, info, where)
        if isinstance(node, ast.Lambda):
            later = True
        extra = ctx or {}
        if isinstance(node, ast.Call):
            first = len(self.edges)
            self.call(node, where, cls, later)
            if ctx:
                for e in self.edges[first:]:
                    if e["kind"] == "calls_later":
                        e.update(ctx)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and cls \
                and isinstance(node.value, ast.Name) and node.value.id == "self" and node.attr in cls["methods"]:
            # self.method used as a value (callback), not called here
            self.add_edge(where, cls["methods"][node.attr], "references", "resolved", node.lineno, **extra)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and self.local_func(where, node.id):
            self.add_edge(where, self.local_func(where, node.id), "references", "resolved", node.lineno, **extra)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in self.funcs:
            self.add_edge(where, self.funcs[node.id], "references", "resolved", node.lineno, **extra)
        for child in ast.iter_child_nodes(node):
            if isinstance(node, ast.Call):
                if child is node.func and isinstance(child, (ast.Attribute, ast.Name)):
                    if isinstance(child, ast.Attribute):
                        self.walk(child.value, where, cls, later)  # receiver may hold calls
                    continue
                self.walk(child, where, cls, later, self.arg_context(node, child))
            elif isinstance(node, (ast.keyword, ast.Lambda)):
                self.walk(child, where, cls, later, ctx)  # a keyword or lambda only wraps the value
            else:
                self.walk(child, where, cls, later)

    @staticmethod
    def arg_context(call, child):
        """Which call a value is handed to: {"callback_of": "after", "delay": 200} or {"callback_of": "Button", "kw": "command"}."""
        f = call.func
        name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
        if not name:
            return None
        ctx = {"callback_of": name}
        if isinstance(child, ast.keyword):
            ctx["kw"] = child.arg
        first = call.args[0] if call.args else None
        if first is not None and first is not child and const_num(first) is not None:
            ctx["delay"] = const_num(first)
        return ctx

    def call(self, node, where, cls, later):
        kind = "calls_later" if later else "calls"
        func, line = node.func, node.lineno
        if isinstance(func, ast.Name):
            name = func.id
            if self.local_func(where, name) in self.local_classes and not later:
                self.add_edge(where, self.local_func(where, name), "instantiates", "certain", line)
            elif self.local_func(where, name):
                self.add_edge(where, self.local_func(where, name), kind, "resolved" if later else "certain", line)
            elif name in self.funcs:
                certainty = "resolved" if later else "certain"
                self.add_edge(where, self.funcs[name], kind, certainty, line)
                self.wrapper_effects(name, node, where)
            elif name in self.classes:
                self.add_edge(where, self.classes[name]["id"], "instantiates", "certain", line)
            elif name in self.aliases:
                self.add_edge(where, self.ext(self.aliases[name], line), kind, "certain", line, name=self.aliases[name])
                self.library_effects(self.aliases[name], node, where)
            elif name in BUILTINS:
                self.stats["builtin_calls"] += 1
                if name == "open" and node.args:
                    self.open_effect(node.args[0], const_set(node.args[1]) if len(node.args) > 1 else {"r"}, where, line)
            else:
                self.add_unknown(where, node, "call to local name '%s'" % name)
            return
        if isinstance(func, ast.Attribute):
            base = func.value
            if cls and isinstance(base, ast.Name) and base.id == "self":
                if func.attr in cls["methods"]:
                    self.add_edge(where, cls["methods"][func.attr], kind, "resolved" if later else "certain", line)
                elif func.attr in cls["attrs"]:
                    self.add_unknown(where, node, "call on instance attribute self.%s (type not known)" % func.attr)
                else:
                    ext_bases = [b for b in cls["bases"] if b and b not in self.classes]
                    if len(ext_bases) == 1:
                        self.add_edge(where, self.ext(ext_bases[0], line), kind, "resolved", line,
                                      name="%s.%s (inherited)" % (ext_bases[0], func.attr))
                    else:
                        self.add_unknown(where, node, "method self.%s not found in class or one outside base" % func.attr)
                return
            full = self.full_name(func)
            if full:
                self.add_edge(where, self.ext(full, line), kind, "certain", line, name=full)
                self.library_effects(full, node, where)
                return
            if const_str(base) is not None:
                self.stats["builtin_calls"] += 1  # method on a string literal, e.g. ", ".join
                return
            self.add_unknown(where, node, "method call on a value of unknown type (.%s)" % func.attr)
            return
        self.add_unknown(where, node, "call on an expression")

    def library_effects(self, full, node, where):
        if full.startswith("subprocess.") and full.split(".")[-1] in SUBPROCESS_FUNCS and node.args:
            words = command_list(node.args[0])
            if words:
                self.cmd_edge(words, where, node.lineno, "certain")
            elif not (isinstance(node.args[0], ast.Name)):
                self.add_unknown(where, node, "command not readable")
        if full in PATH_OPS and node.args:
            pv = self.path_value(node.args[0])
            if pv is None:
                self.add_unknown(where, node.args[0], "path for %s not known" % full)
            else:
                self.add_edge(where, self.file_id(pv), PATH_OPS[full], "certain" if pv.exact else "resolved",
                              node.lineno, name=full)

    def wrapper_effects(self, name, node, where):
        if name in self.file_wrappers:
            idx, modes = self.file_wrappers[name]
            if idx < len(node.args):
                self.open_effect(node.args[idx], modes, where, node.lineno, via=name)
        if name in self.cmd_wrappers:
            spec = self.cmd_wrappers[name]
            if spec == "*":
                words = const_prefix(node.args)
            else:
                words = command_list(node.args[spec]) if spec < len(node.args) else []
            if words:
                self.cmd_edge(words, where, node.lineno, "resolved", via=name)
            else:
                self.add_unknown(where, node, "command passed to %s not readable" % name)

    def cmd_edge(self, words, where, line, certainty, **extra):
        nid = "cmd:" + words[0]
        if not any(n["id"] == nid for n in self.nodes):
            self.add_node(nid, "command", words[0])
        self.add_edge(where, nid, "runs_command", certainty, line, args=words, **extra)

    def open_effect(self, path_node, modes, where, line, via=None):
        if via is None and isinstance(path_node, ast.Name) and where.rsplit(":", 1)[-1] in self.file_wrappers:
            return  # inside a wrapper like read(path): the real paths are recorded where it is called
        pv = self.path_value(path_node)
        if pv is None:
            return self.add_unknown(where, path_node, "file path not known")
        if modes is None:
            return self.add_unknown(where, path_node, "file mode not known")
        write = any(c in m for m in modes for c in "wax+")
        extra = {"via": via} if via else {}
        self.add_edge(where, self.file_id(pv), "writes" if write else "reads",
                      "resolved" if via or not pv.exact else "certain", line, **extra)


def scan_python(root, rel):
    return ModuleScanner(root, rel).scan()
