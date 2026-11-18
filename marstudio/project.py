"""Mutable editor commands over an append-only, independently verifiable project."""
from __future__ import annotations

import copy
import io
import os
import platform
import sqlite3
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .integrity import (GENESIS, MAX_EVENTS, MAX_FILE, MAX_JSON, MAX_TOTAL, META_FIELDS, ORIGINS,
                        SCHEMA, TOPICS, Invalid, apply, canonical, digest, filename, patches,
                        png_dimensions, read_zip, require, safe_member, validate_state, verify_ledger,
                        verify_project)

DESIGN_ACTION = "undoable"
SQL = """
CREATE TABLE events (seq INTEGER PRIMARY KEY, body BLOB NOT NULL);
CREATE TRIGGER append_sequence BEFORE INSERT ON events
WHEN NEW.seq <> COALESCE((SELECT MAX(seq) FROM events), 0) + 1
BEGIN SELECT RAISE(ABORT, 'nonsequential history'); END;
CREATE TRIGGER no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""


def uid():
    return uuid.uuid4().hex


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def make_state(topic, title, template=False):
    assert topic in TOPICS
    meta = dict.fromkeys(META_FIELDS, "")
    meta["title"] = title
    return {"project": uid(), "schema": SCHEMA, "topic": topic, "template": template,
            "paper": {"w": 210, "h": 297, "background": "#ffffffff"}, "objects": {}, "order": [],
            "assets": {}, "exports": {}, "meta": meta, "guides": [], "scaffold": ""}


def layer(kind, x=18, y=24, w=62, h=24, **values):
    text_kind = kind in ("text", "label", "callout")
    entry = {"kind": kind, "name": kind.title(), "parent": None, "visible": True, "locked": False,
             "x": round(x, 4), "y": round(y, 4), "w": round(max(w, 1), 4), "h": round(max(h, 1), 4),
             "angle": 0, "fill": "#00000000" if kind == "text" else "#e9f4f1ff",
             "stroke": "#27776cff", "ink": "#172738ff", "border": 0 if kind == "text" else .32,
             "opacity": 1, "size": 15, "line_height": 1.15, "padding": 3, "radius": 2,
             "text": "", "spans": [], "points": [], "alt": "", "source": "unknown",
             "asset": None, "crop": [0, 0, 1, 1], "edge": "Proposed link",
             "label": "", "start": None, "end": None}
    if kind in ("arrow", "connector", "path", "polygon"):
        entry["points"] = [[0, 0], [1, 1]] if kind != "polygon" else [[.5, 0], [1, 1], [0, 1]]
    if kind == "chart":
        entry["points"] = [[0, .42], [.25, .67], [.5, .54], [.75, .88]]
    if text_kind:
        entry["h"] = max(entry["h"], 14)
    entry.update(values)
    return entry


def families(state):
    result = {}
    for key in state["order"]:
        result.setdefault(state["objects"][key]["parent"], []).append(key)
    return result


def descendants(state, keys, leaf=False):
    families_ = families(state)
    found = set()
    def descend(key):
        if key in found or key not in state["objects"]:
            return
        if not leaf or state["objects"][key]["kind"] != "group":
            found.add(key)
        for child in families_.get(key, []):
            descend(child)
    for key in keys:
        descend(key)
    return [k for k in state["order"] if k in found]


def ancestry(state, key):
    while key is not None:
        yield key
        key = state["objects"][key]["parent"]


def is_locked(state, key):
    return any(state["objects"][k]["locked"] for k in ancestry(state, key))


def front_layers(state, include_hidden=False):
    child_map = families(state)
    def walk(parent, shown, opacity):
        for key in child_map.get(parent, []):
            obj = state["objects"][key]
            visible = shown and obj["visible"]
            alpha = opacity * obj["opacity"]
            if obj["kind"] == "group":
                yield from walk(key, visible, alpha)
            elif visible or include_hidden:
                yield key, obj, alpha
    return walk(None, True, 1)


def atomic_write(path, data, *, before_replace=None):
    path = Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.stem[:36]}-",
                                     suffix=".pending", delete=False) as tmp:
        pending = Path(tmp.name)
        try:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        except BaseException:
            tmp.close()
            pending.unlink(missing_ok=True)
            raise
    try:
        if before_replace:
            before_replace()
        os.replace(pending, path)
        if os.name == "posix":
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        pending.unlink(missing_ok=True)


def archive_bytes(files):
    require(len(files) < 22000 and all(safe_member(n) and len(b) <= MAX_FILE for n,b in files.items())
            and sum(map(len, files.values())) <= MAX_TOTAL, "Output exceeds the archive limit")
    full = dict(files)
    full["manifest.json"] = canonical({k: digest(v) for k, v in files.items()})
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as z:
        for name, data in sorted(full.items()):
            z.writestr(name, data)
    require(out.tell() <= MAX_TOTAL, "Output exceeds the compressed archive limit")
    return out.getvalue()


class Project:
    def __init__(self, topic="general", title="Untitled design", template=False, *, _open=None):
        self.session = uid()
        self.environment = {"studio": __version__, "schema": SCHEMA, "os": platform.system(),
                            "os_release": platform.release(), "python": platform.python_version()}
        self.state = None
        self.events = []
        self.snapshots = {}
        self.sources = {}
        self.artifacts = {}
        self.undo_stack, self.redo_stack = [], []
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(SQL)
        self._jsonl_size = 0
        if _open:
            state, events, snaps, _anchor, files = _open
            self.state, self.events, self.snapshots = state, events, snaps
            self.sources = {k: files[f"assets/{k}.{s['media']}"] for k, s in state["assets"].items()}
            self.artifacts = {s["sha256"]: files[f"artifacts/{s['sha256']}"] for s in state["exports"].values()}
            data = files["audit.jsonl"]
            self._jsonl_size = len(data)
            with self.db:
                self.db.executemany("INSERT INTO events VALUES (?, ?)", ((e["seq"], canonical(e)) for e in events))
            for e in events:
                self._history(e)
        else:
            self._append(make_state(topic, title, template), "Create blank project",
                         "noncreative_tool", snapshot=True)

    def _history(self, event):
        action = event["action"]
        details = event["details"]
        if details.get(DESIGN_ACTION) is True:
            self.undo_stack.append(event)
            self.redo_stack.clear()
        elif action == "Undo":
            require(self.undo_stack and self.undo_stack[-1]["seq"] == details.get("target"),
                    "Invalid undo history")
            self.redo_stack.append(self.undo_stack.pop())
        elif action == "Redo":
            require(self.redo_stack and self.redo_stack[-1]["seq"] == details.get("target"),
                    "Invalid redo history")
            self.undo_stack.append(self.redo_stack.pop())

    def _append(self, after, action, origin, details=None, snapshot=False):
        require(origin in ORIGINS and len(self.events) < MAX_EVENTS, "Invalid origin or history limit reached")
        validate_state(after)
        seq = len(self.events) + 1
        patch = patches(self.state, after)
        source_refs, object_ids = set(), set()
        for key, sub, before, value in patch:
            if key == "objects" and sub:
                object_ids.add(sub)
                source_refs.update(data["asset"] for data in (before, value) if data and data["asset"])
            if key == "assets" and sub:
                source_refs.add(sub)
        if snapshot:
            source_refs.update(after["assets"])
        observed = datetime.now().astimezone()
        snap = canonical(after) if snapshot else None
        payload = {"project": after["project"], "session": self.session, "seq": seq,
                   "utc": observed.astimezone(timezone.utc).isoformat(timespec="milliseconds"),
                   "offset_minutes": int(observed.utcoffset().total_seconds() / 60),
                   "action": action, "origin": origin, "actor": after["meta"]["actor"],
                   "objects": sorted(object_ids), "assets": sorted(source_refs), "patch": patch,
                   "details": details or {}, "snapshot": digest(snap) if snap else None,
                   "environment": self.environment,
                   "previous": self.events[-1]["hash"] if self.events else GENESIS}
        event = {**payload, "hash": digest(canonical(payload))}
        raw = canonical(event)
        require(0 < len(raw) < MAX_JSON and self._jsonl_size + len(raw) + 1 <= MAX_FILE,
                "Event log storage limit exceeded")
        with self.db:
            self.db.execute("INSERT INTO events VALUES (?, ?)", (seq, raw))
        self._jsonl_size += len(raw) + 1
        if snap:
            self.snapshots[event["snapshot"]] = snap
        self.events.append(event)
        self.state = after
        self._history(event)
        return event

    def edit(self, action, change, origin, *, undoable=True, details=None):
        state = copy.deepcopy(self.state)
        change(state)
        if state == self.state:
            return None
        return self._append(state, action, origin, {**(details or {}), DESIGN_ACTION: undoable},
                            snapshot=(len(self.events) + 1) % 12 == 0)

    def undo(self):
        if self.undo_stack:
            target = self.undo_stack[-1]
            return self._append(apply(self.state, target["patch"], reverse=True), "Undo",
                                "noncreative_tool", {"target": target["seq"]})

    def redo(self):
        if self.redo_stack:
            target = self.redo_stack[-1]
            return self._append(apply(self.state, target["patch"]), "Redo",
                                "noncreative_tool", {"target": target["seq"]})

    def checkpoint(self, reason, *, details=None):
        return self._append(copy.deepcopy(self.state), reason, "noncreative_tool", details, snapshot=True)

    def import_file(self, path, source="unknown", credit="", license_="", notes=""):
        path = Path(path)
        suffix = path.suffix.lower().removeprefix(".")
        require(suffix in ("png", "md", "txt", "pdf"), "Supported source types: PNG, MD, TXT and PDF. SVG is not imported.")
        require(filename(path.name), "Unsupported source filename")
        with path.open("rb") as file:
            data = file.read(48 * 1024**2 + 1)
        require(0 < len(data) <= 48 * 1024**2, "Source size must be 1 byte to 48 MiB")
        key = digest(data)
        if key in self.sources:
            return key
        dims = png_dimensions(data) if suffix == "png" else (0, 0)
        if suffix == "png":
            from .render import decode_png
            decode_png(data, cache_key=key)
        if suffix == "pdf":
            require(data.startswith(b"%PDF-"), "Expected a PDF document")
        info = {"name": path.name, "size": len(data), "width": dims[0], "height": dims[1],
                "media": suffix, "type": source, "imported": utc(), "credit": credit,
                "license": license_, "notes": notes}
        self.edit("Import locked source", lambda s: s["assets"].update({key: info}),
                  "automated_import", undoable=False, details={"selected_file": path.name, "classification": source})
        self.sources[key] = data
        return key

    def archive_artifact(self, path, data, settings):
        name = Path(path).name
        require(filename(name), "Unsupported export filename")
        key = digest(data)
        info = {"sha256": key, "name": name, "size": len(data), "settings": settings, "utc": utc()}
        self.edit("Record artifact bytes", lambda s: s["exports"].update({uid(): info}),
                  "noncreative_tool", undoable=False)
        self.artifacts[key] = data
        return key

    def retain_edited_pngs(self, source_hash, images, *, additional_sources=()):
        """Keep a photo operation's output files available even after a layer undo."""
        source = self.state["assets"][source_hash]
        lineage = list(dict.fromkeys([source_hash, *additional_sources]))
        declarations = {self.state["assets"][key]["type"] for key in lineage}
        output_type = source["type"] if len(declarations) == 1 else "mixed"
        keys = []
        new_sources, new_metadata = {}, {}
        for data in images:
            key = digest(data)
            keys.append(key)
            if key in self.sources:
                require(self.state["assets"][key]["type"] == output_type,
                        "These pixels match an existing asset with a different source declaration.")
                continue
            width, height = png_dimensions(data)
            require(len(data) <= MAX_FILE, "The edited PNG exceeds the project asset limit.")
            new_sources[key] = data
            new_metadata[key] = dict(source, name=f"edited-{key[:12]}.png", size=len(data),
                                     width=width, height=height, imported=utc(),
                                     type=output_type, notes=f"Edited pixels derived from SHA-256 {source_hash}")
            if len(lineage) > 1:
                new_metadata[key].update(credit="", license="",
                                         notes="Composite of PNG assets listed in the edit history.")
        if new_sources:
            require(len(self.events) < MAX_EVENTS - 1, "Project history has no room for another photo edit.")
            total = sum(map(len, self.sources.values())) + sum(map(len, self.artifacts.values()))
            require(total + sum(map(len, new_sources.values())) <= MAX_TOTAL - 16 * 1024**2,
                    "The project has no room for another copy of the edited pixels.")
            self.edit("Preserve edited PNG", lambda s: s["assets"].update(new_metadata),
                      "noncreative_tool", undoable=False,
                      details={"from_asset": source_hash, "from_assets": lineage, "to_assets": list(new_sources)})
            self.sources.update(new_sources)
        return keys

    def anchor(self):
        return {"count": len(self.events), "head": self.events[-1]["hash"],
                "project": self.state["project"], "state": digest(canonical(self.state))}

    def members(self, preview):
        files = {"project.json": canonical(self.state), "anchor.json": canonical(self.anchor()),
                 "preview.png": preview, "audit.sqlite": self.db.serialize(),
                 "audit.jsonl": b"".join(body + b"\n" for (body,) in
                                         self.db.execute("SELECT body FROM events ORDER BY seq"))}
        for key, s in self.state["assets"].items():
            files[f"assets/{key}.{s['media']}"] = self.sources[key]
        for key, data in self.snapshots.items():
            files[f"snapshots/{key}.json"] = data
        for key, data in self.artifacts.items():
            files[f"artifacts/{key}"] = data
        return files

    def save(self, path, preview, **kwargs):
        atomic_write(path, archive_bytes(self.members(preview)), **kwargs)

    @classmethod
    def load(cls, path, *, check_sqlite=True):
        files = read_zip(path)
        state, events, snaps, anchor = verify_project(files)
        if check_sqlite:
            verify_ledger(files["audit.sqlite"], events)
        return cls(_open=(state, events, snaps, anchor, files))

    def state_at(self, seq):
        require(type(seq) is int and 1 <= seq <= len(self.events), "No such event")
        from .integrity import parse
        for i in range(seq - 1, -1, -1):
            key = self.events[i]["snapshot"]
            if key:
                result = parse(self.snapshots[key])
                for e in self.events[i+1:seq]:
                    result = apply(result, e["patch"])
                return result
        raise Invalid("Missing genesis checkpoint")

    def manual_objects(self):
        return {k for e in self.events if e["origin"] == "manual_gui" for k in e["objects"]}

