"""Optional online lookup. The rest of docmap never imports this unless the user asks for a lookup.

Rules:
- Runs only when the Internet switch is on (checked here before every request).
- Results are saved only when the user picks them, into <docs>/viewer/research/, with source link, date and license.
- Saved research is "external": it never changes facts or diagrams.
Limits respected (checked 2026-10-13):
- GitHub search without login: 10 requests per minute (docs.github.com/en/rest/search/search).
- Stack Exchange without key: 300 requests per day per IP; obey the "backoff" field (seconds).
- Wikimedia: a User-Agent naming the tool and a way to contact its operator (User-Agent policy).
- Same question to the same source is answered from a local cache for 1 hour.
"""
import gzip
import html
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request

LIMITS = {"github": (10, 60), "stackoverflow": (300, 86400), "wikipedia": (60, 60), "mslearn": (30, 60)}
CACHE_SECONDS = 3600
# Lookups run on background threads. .usage.json is read, changed and written back, so only one at a time:
# otherwise two lookups would each save their own copy and the other's counts and cache would be lost.
_USAGE_LOCK = threading.Lock()


class Lookup(object):
    def __init__(self, out_dir, contact=""):
        self.dir = os.path.join(out_dir, "research")
        self.state_path = os.path.join(self.dir, ".usage.json")
        agent = "docmap/1.0 (personal documentation viewer; %s)" % (contact or "contact not set")
        self.headers = {"User-Agent": agent, "Accept": "application/json"}

    # ---------- bookkeeping ----------
    def _state(self):
        try:
            with open(self.state_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"calls": {}, "cache": {}, "backoff": {}}

    def _save_state(self, st):
        os.makedirs(self.dir, exist_ok=True)
        with open(self.state_path, "w") as f:
            json.dump(st, f)

    def _get(self, source, url, extra_headers=None):
        with _USAGE_LOCK:
            return self._get_locked(source, url, extra_headers)

    def _get_locked(self, source, url, extra_headers):
        st, now = self._state(), time.time()
        key = source + " " + url
        hit = st["cache"].get(key)
        if hit and now - hit["t"] < CACHE_SECONDS:
            return hit["data"]
        if now < st["backoff"].get(source, 0):
            raise RuntimeError("%s asked us to wait %d more seconds" % (source, st["backoff"][source] - now))
        count, window = LIMITS[source]
        recent = [t for t in st["calls"].get(source, []) if now - t < window]
        if len(recent) >= count:
            raise RuntimeError("%s limit reached (%d per %d s). Try again later." % (source, count, window))
        req = urllib.request.Request(url, headers=dict(self.headers, **(extra_headers or {})))
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
        data = json.loads(raw.decode("utf-8"))
        st["calls"][source] = recent + [now]
        if isinstance(data, dict) and data.get("backoff"):
            st["backoff"][source] = now + int(data["backoff"])
        st["cache"] = {k: v for k, v in st["cache"].items() if now - v["t"] < CACHE_SECONDS}
        st["cache"][key] = {"t": now, "data": data}
        self._save_state(st)
        return data

    # ---------- sources ----------
    def wikipedia(self, q):
        d = self._get("wikipedia", "https://en.wikipedia.org/w/rest.php/v1/search/page?" + urllib.parse.urlencode({"q": q, "limit": 5}))
        return [{"title": p["title"], "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(p["key"]),
                 "summary": html.unescape(re.sub(r"<[^>]+>", "", p.get("excerpt") or p.get("description") or "")),
                 "license": "CC BY-SA 4.0 (Wikipedia)"} for p in d.get("pages", [])]

    def stackoverflow(self, q):
        d = self._get("stackoverflow", "https://api.stackexchange.com/2.3/search/advanced?" + urllib.parse.urlencode(
            {"order": "desc", "sort": "relevance", "q": q, "site": "stackoverflow", "pagesize": 5}))
        return [{"title": html.unescape(i["title"]), "url": i["link"],
                 "summary": "score %s, %s answer(s)%s" % (i.get("score"), i.get("answer_count"),
                                                         ", answered" if i.get("is_answered") else ""),
                 "license": "CC BY-SA (Stack Overflow)"} for i in d.get("items", [])]

    def github(self, q):
        d = self._get("github", "https://api.github.com/search/repositories?" + urllib.parse.urlencode({"q": q, "per_page": 5}),
                      {"Accept": "application/vnd.github+json"})
        return [{"title": i["full_name"], "url": i["html_url"],
                 "summary": "%s (stars: %s)" % (i.get("description") or "", i.get("stargazers_count")),
                 "license": "see the repository's own license"} for i in d.get("items", [])]

    def mslearn(self, q):
        d = self._get("mslearn", "https://learn.microsoft.com/api/search?" + urllib.parse.urlencode(
            {"search": q, "locale": "en-us", "$top": 5}))
        return [{"title": i["title"], "url": i["url"], "summary": i.get("description", ""),
                 "license": "Microsoft Learn terms of use"} for i in d.get("results", [])]

    @staticmethod
    def official(q):
        """Links to official docs search pages. No request is made here; opening a link needs the internet."""
        enc = urllib.parse.quote(q)
        out = [{"title": "Python docs search: " + q, "url": "https://docs.python.org/3/search.html?q=" + enc},
               {"title": "Rust std docs search: " + q, "url": "https://doc.rust-lang.org/std/?search=" + enc},
               {"title": "cppreference search: " + q, "url": "https://en.cppreference.com/mwiki/index.php?search=" + enc}]
        mod = q.split(".")[0]
        if mod in getattr(sys, "stdlib_module_names", ()):
            out.insert(0, {"title": "Python module " + mod, "url": "https://docs.python.org/3/library/%s.html" % mod})
        for o in out:
            o.update(summary="official documentation link", license="see the site")
        return out

    def search(self, q, internet_on):
        if not internet_on:
            raise RuntimeError("Internet is off. Turn it on to look things up.")
        results, errors = {}, {}
        for name in ("wikipedia", "stackoverflow", "github", "mslearn"):
            try:
                results[name] = getattr(self, name)(q)
            except Exception as e:  # one source failing must not hide the others
                errors[name] = str(e)
        results["official"] = self.official(q)
        return {"query": q, "results": results, "errors": errors, "time": time.strftime("%Y-%m-%d %H:%M")}

    # ---------- saved research ----------
    def save(self, term, node_id, picked):
        os.makedirs(self.dir, exist_ok=True)
        slug = re.sub(r"[^\w.-]+", "-", term).strip("-")[:60] or "term"
        path = os.path.join(self.dir, "%s-%s.json" % (slug, time.strftime("%Y%m%d-%H%M%S")))
        record = {"term": term, "node": node_id, "saved": time.strftime("%Y-%m-%d %H:%M"), "external": True,
                  "items": picked}
        with open(path, "w") as f:
            json.dump(record, f, indent=1)
        return os.path.basename(path)

    def saved(self):
        out = []
        if os.path.isdir(self.dir):
            for name in sorted(os.listdir(self.dir)):
                if name.endswith(".json") and not name.startswith("."):
                    try:
                        with open(os.path.join(self.dir, name)) as f:
                            out.append(dict(json.load(f), file=name))
                    except (OSError, ValueError):
                        continue
        return out
