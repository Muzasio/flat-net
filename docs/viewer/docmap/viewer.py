"""docmap window: WebKitGTK page, no browser around it.

Internet switch (default off), enforced three ways:
  1. the page's Content-Security-Policy only allows docmap: content,
  2. the network layer gets a dead proxy (127.0.0.1:9) so any http(s) request fails,
  3. every navigation away from docmap: is stopped; web links open only when the switch is on.
"""
import html
import json
import mimetypes
import os
import sys
import threading
import urllib.parse

# WebKitGTK's DMA-BUF renderer crashes on some Wayland setups ("Error 71 (Protocol error)"), seen on the
# development PC. Turning it off is the documented workaround; set DOCMAP_DMABUF=1 to keep it on.
if not os.environ.get("DOCMAP_DMABUF"):
    os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
for _v in ("4.1", "4.0"):
    try:
        gi.require_version("WebKit2", _v)
        break
    except ValueError:
        continue
from gi.repository import Gio, GLib, Gtk, WebKit2  # noqa: E402

import render  # noqa: E402
from check import ConfigError, load_layout, stale_edits  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
DEAD_PROXY = "http://127.0.0.1:9"


class Viewer(object):
    def __init__(self, project, build, snapshot=None):
        self.project, self.build, self.snapshot = project, build, snapshot
        self.settings_path = os.path.join(project["out"], "settings.json")
        self.settings = {"internet": False}
        try:
            with open(self.settings_path) as f:
                self.settings.update(json.load(f))
        except (OSError, ValueError):
            pass
        self.layout_path = os.path.join(project["out"], "layout.json")
        self.facts = build()
        self.linker = render.Linker(self.facts)
        self._lookup = None

        ctx = WebKit2.WebContext.get_default()
        ctx.register_uri_scheme("docmap", self.serve)
        self.apply_internet()
        ucm = WebKit2.UserContentManager()
        ucm.register_script_message_handler("docmap")
        ucm.connect("script-message-received::docmap", self.on_message)
        self.view = WebKit2.WebView.new_with_user_content_manager(ucm)
        s = self.view.get_settings()
        s.set_enable_developer_extras(bool(os.environ.get("DOCMAP_DEBUG")))
        s.set_enable_write_console_messages_to_stdout(True)
        s.set_allow_file_access_from_file_urls(False)
        s.set_allow_universal_access_from_file_urls(False)
        self.view.connect("decide-policy", self.on_policy)
        if snapshot:
            self.view.connect("load-changed", self.on_load_for_snapshot)

        self.win = Gtk.Window(title="docmap: " + os.path.basename(project["root"]))
        self.win.set_default_size(1500, 900)
        self.win.add(self.view)
        self.win.connect("destroy", Gtk.main_quit)
        self.win.show_all()
        self.view.load_uri("docmap://app/index.html")

    # ---- internet switch ----
    def apply_internet(self):
        on = self.settings["internet"]
        mode = WebKit2.NetworkProxyMode.DEFAULT if on else WebKit2.NetworkProxyMode.CUSTOM
        proxy = None if on else WebKit2.NetworkProxySettings.new(DEAD_PROXY, None)
        session = getattr(WebKit2, "NetworkSession", None)
        if session is not None and hasattr(session, "get_default"):  # WebKitGTK 4.1+
            session.get_default().set_proxy_settings(mode, proxy)
        else:  # WebKitGTK 4.0
            WebKit2.WebContext.get_default().get_website_data_manager().set_network_proxy_settings(mode, proxy)

    def csp(self):
        web = " http: https:" if self.settings["internet"] else ""
        return ("default-src docmap:; script-src docmap: 'unsafe-inline'; style-src docmap: 'unsafe-inline'; "
                "img-src docmap: data:%s; connect-src docmap:; frame-src 'none'; object-src 'none'" % web)

    # ---- serving the page ----
    def serve(self, request):
        if request.get_uri().startswith(render.PROJECT_URL):
            return self.serve_project_image(request)
        path = request.get_path().lstrip("/") or "index.html"
        full = os.path.normpath(os.path.join(WEB, path))
        if not full.startswith(WEB + os.sep) or not os.path.isfile(full):
            return request.finish_error(GLib.Error.new_literal(Gio.io_error_quark(), "not found", Gio.IOErrorEnum.NOT_FOUND))
        with open(full, "rb") as f:
            data = f.read()
        if path == "index.html":
            layout = load_layout(self.layout_path)
            boot = json.dumps({"facts": self.facts, "settings": self.settings, "layout": layout,
                               "research": self.lookup().saved(),
                               "stale": stale_edits(layout, self.facts),
                               "project": os.path.basename(self.project["root"])}).replace("</", "<\\/")
            data = data.replace(b"%CSP%", self.csp().encode()).replace(b"%BOOT%", boot.encode())
        mime = mimetypes.guess_type(full)[0] or "application/octet-stream"
        stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data))
        request.finish(stream, len(data), mime)

    def serve_project_image(self, request):
        """An image a doc shows (render.image_src). Only image files inside the project, nothing else."""
        rel = urllib.parse.unquote(request.get_uri()[len(render.PROJECT_URL):])
        full = self.safe_path(rel)
        mime = mimetypes.guess_type(rel)[0] or ""
        if not full or not mime.startswith("image/"):
            return request.finish_error(GLib.Error.new_literal(Gio.io_error_quark(), "not found", Gio.IOErrorEnum.NOT_FOUND))
        with open(full, "rb") as f:
            data = f.read()
        request.finish(Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data)), len(data), mime)

    def on_policy(self, view, decision, kind):
        if kind not in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION, WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
            return False
        uri = decision.get_navigation_action().get_request().get_uri()
        if uri.startswith("docmap:") and kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            return False
        decision.ignore()
        if uri.startswith(("http://", "https://")):
            if self.settings["internet"]:
                self.open_web(uri)
            else:
                self.js("DM.toast(%s)" % json.dumps("Internet is off. Turn it on (top right) to open: " + uri))
        return True

    def open_web(self, uri):
        win = Gtk.Window(title=uri)
        win.set_default_size(1100, 800)
        web = WebKit2.WebView()
        web.load_uri(uri)
        win.add(web)
        win.show_all()

    # ---- messages from the page ----
    def js(self, script):
        if hasattr(self.view, "evaluate_javascript"):
            self.view.evaluate_javascript(script, -1, None, None, None, None, None)
        else:
            self.view.run_javascript(script, None, None, None)

    def on_message(self, ucm, result):
        value = result.get_js_value() if hasattr(result, "get_js_value") else result
        msg = json.loads(value.to_json(0))
        kind = msg.get("type")
        if kind == "open":
            rel = msg["file"]
            self.js("DM.fileLoaded(%s, %s, %s)" % (json.dumps(rel), json.dumps(self.render_file(rel)),
                                                     json.dumps(msg.get("line"))))
        elif kind == "external":
            path = self.safe_path(msg["file"])
            if path:
                Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)
        elif kind == "internet":
            self.settings["internet"] = bool(msg["on"])
            os.makedirs(os.path.dirname(self.settings_path), exist_ok=True)
            with open(self.settings_path, "w") as f:
                json.dump(self.settings, f)
            self.apply_internet()
            self.view.reload()  # new security policy needs a fresh page; open tabs are restored by the page
        elif kind == "layout":
            layout = msg.get("layout")
            if not isinstance(layout, dict):
                return
            os.makedirs(self.project["out"], exist_ok=True)
            tmp = self.layout_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(layout, f, indent=1, sort_keys=True)
            os.replace(tmp, self.layout_path)  # write whole file, then swap: no half-written layout
            self.js("DM.setStale(%s)" % json.dumps(stale_edits(layout, self.facts)))
        elif kind == "lookup":
            query, internet = str(msg.get("query", ""))[:200], self.settings["internet"]

            def work():
                try:
                    result = self.lookup().search(query, internet)
                except Exception as e:
                    result = {"query": query, "error": str(e)}
                GLib.idle_add(lambda: self.js("DM.lookupResult(%s)" % json.dumps(result)) and False)
            threading.Thread(target=work, daemon=True).start()  # network work off the window thread
        elif kind == "saveResearch":
            items = [i for i in msg.get("items", []) if isinstance(i, dict)]
            if items:
                self.lookup().save(str(msg.get("term", "")), msg.get("node"), items)
            self.js("DM.setResearch(%s)" % json.dumps(self.lookup().saved()))
        elif kind == "rescan":
            try:
                self.facts = self.build()
            except ConfigError as e:  # keep showing the last good scan
                return self.js("DM.toast(%s)" % json.dumps("Rescan stopped: " + str(e)))
            self.linker = render.Linker(self.facts)
            self.view.reload()

    def lookup(self):
        if self._lookup is None:
            import lookup
            cfg = {}
            try:
                with open(os.path.join(self.project["out"], "docmap.json")) as f:
                    cfg = json.load(f)
            except (OSError, ValueError):
                pass
            self._lookup = lookup.Lookup(self.project["out"], cfg.get("contact", ""))
        return self._lookup

    def safe_path(self, rel):
        """Only files inside the project may be opened. Symbolic links are followed first, so a link that
        points outside the project is refused too."""
        root = os.path.realpath(self.project["root"])
        path = os.path.realpath(os.path.join(root, rel))
        return path if path.startswith(root + os.sep) and os.path.isfile(path) else None

    def render_file(self, rel):
        path = self.safe_path(rel)
        if not path:
            return '<p class="err">Not a file inside the project: %s</p>' % html.escape(rel)
        try:
            with open(path) as f:
                source = f.read()
        except (OSError, UnicodeDecodeError) as e:
            return '<p class="err">Cannot show this file as text (%s). Use "Open in default app".</p>' % html.escape(str(e))
        if rel.endswith(".md"):
            return render.markdown(source, rel, self.linker, self.settings["internet"])
        if rel in self.facts["python_files"] or rel in self.facts.get("ts_files", []):
            return render.code(source, rel, self.linker)
        return render.text_file(source)

    # ---- test helper: save a picture of the window ----
    def on_load_for_snapshot(self, view, event):
        if event == WebKit2.LoadEvent.FINISHED:
            GLib.timeout_add(2500, self.take_snapshot)

    def take_snapshot(self):
        script = os.environ.get("DOCMAP_SNAPSHOT_JS")
        if script:
            self.js(script)
        GLib.timeout_add(int(os.environ.get("DOCMAP_SNAPSHOT_WAIT", "1200")), self._snap)
        return False

    def _snap(self):
        def done(view, res):
            surface = view.get_snapshot_finish(res)
            surface.write_to_png(self.snapshot)
            print("snapshot written:", self.snapshot)
            Gtk.main_quit()
        self.view.get_snapshot(WebKit2.SnapshotRegion.VISIBLE, WebKit2.SnapshotOptions.NONE, None, done)
        return False


def run(project, build, snapshot=None):
    Viewer(project, build, snapshot)
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit("Start docmap with: python3 docmap.py view <project>")
