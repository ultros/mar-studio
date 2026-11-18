"""Standard-library project schema and independent, read-only verification."""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import re
import stat
import struct
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

SCHEMA = 1
GENESIS = "0" * 64
MAX_FILE = 128 * 1024**2
MAX_TOTAL = 512 * 1024**2
MAX_JSON = 24 * 1024**2
MAX_EVENTS = 20000
MAX_OBJECTS = 3500
MAX_PIXELS = 75_000_000
ORIGINS = ("manual_gui", "noncreative_tool", "automated_import", "AI_generated", "unknown")
SOURCES = ("unknown", "user-declared human", "AI-generated", "third-party", "mixed", "template")
KINDS = ("text", "panel", "circle", "polygon", "path", "arrow", "connector", "label",
         "chart", "grid", "callout", "icon", "placeholder", "image", "group")
TOPICS = ("general", "architecture", "hypothesis")
EDGE_TYPES = ("Proposed link", "Shipment", "Money transfer", "Speculation")
META_FIELDS = ("title", "actor", "credit", "claim", "decisions", "human_text", "composition",
               "ai_use", "third_party", "unknown", "references", "licenses", "font_notes",
               "drafts", "gaps", "worksheet_created", "worksheet_excluded")
STATE_FIELDS = ("schema", "project", "topic", "paper", "objects", "order", "meta", "assets",
                "exports", "guides", "scaffold", "template")
MAPS = ("objects", "assets", "exports", "meta")
OBJECT_FIELDS = ("kind", "name", "parent", "visible", "locked", "x", "y", "w", "h", "angle",
                 "fill", "stroke", "ink", "border", "opacity", "size", "line_height", "padding",
                 "radius", "text", "spans", "points", "alt", "source", "asset", "crop", "edge",
                 "label", "start", "end")
EVENT_FIELDS = ("project", "session", "seq", "utc", "offset_minutes", "action", "origin", "actor",
                "objects", "assets", "patch", "details", "snapshot", "previous", "hash",
                "environment")
KIT = {
    "project.marproj", "report.pdf", "README.txt", "verify_bundle.py", "history/audit.jsonl",
    "history/audit.sqlite", "verification.json", "anchor.json",
}
GUIDANCE = {
    "architecture": ("Bilateral swimlane", "Annotated network", "Vertical process"),
    "hypothesis": ("Branched evidence graph", "Annotated timeline", "Vertical editorial"),
}
DISCLAIMER = ("Supporting evidence only. Copyright does not protect ideas, methods or facts. "
              "Accurately disclose AI-created material in any registration.")
SCENARIO_NOTE = "ILLUSTRATIVE HYPOTHESIS • UNPROVEN • NOT A VERIFIED CASE"
IMAGE_SIG = b"\x89PNG\r\n\x1a\n"


class Invalid(ValueError):
    """Bad input, archive, or history."""


def require(ok, reason):
    if not ok:
        raise Invalid(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def hash_ok(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise Invalid("Cannot encode canonical JSON") from exc


def _pairs(items):
    out = {}
    for key, val in items:
        require(key not in out, "Duplicate JSON key")
        out[key] = val
    return out


def _constant(_):
    raise Invalid("Non-finite JSON value")


def parse(data):
    require(len(data) <= MAX_JSON, "JSON size limit exceeded")
    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        stack = [(result, 0)]
        count = 0
        while stack:
            item, depth = stack.pop()
            count += 1
            require(depth < 48 and count < 1_500_000, "JSON complexity limit exceeded")
            if type(item) is dict:
                stack.extend((i, depth + 1) for i in item)
                stack.extend((i, depth + 1) for i in item.values())
            elif type(item) is list:
                stack.extend((i, depth + 1) for i in item)
            elif type(item) is str:
                require(not any(0xD800 <= ord(c) <= 0xDFFF or ord(c) < 32 and c not in "\n\r\t"
                                or (ord(c) & 0xFFFF) in (0xFFFE, 0xFFFF) for c in item),
                        "Invalid Unicode in JSON")
            elif type(item) is float:
                require(math.isfinite(item), "Non-finite JSON value")
        return result
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, Invalid):
            raise
        raise Invalid(f"Invalid JSON: {exc}") from exc


def exact(value, fields, label):
    require(type(value) is dict and set(value) == set(fields), f"Invalid {label} fields")


def string(value, n=16000):
    return (isinstance(value, str) and len(value) <= n and
            not any(0xD800 <= ord(c) <= 0xDFFF or ord(c) < 32 and c not in "\r\n\t"
                    or (ord(c) & 0xFFFF) in (0xFFFE, 0xFFFF) for c in value))


def numeric(value, low, high):
    return type(value) in (float, int) and low <= value <= high


def filename(value):
    return string(value, 255) and bool(value) and not any(c in value for c in "/\\:\n\r\t") and value not in (".", "..")


def date_ok(value):
    if not string(value, 40):
        return False
    try:
        return datetime.fromisoformat(value).utcoffset() is not None
    except ValueError:
        return False


def color(value):
    return isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{8}", value) is not None


def png_dimensions(data):
    require(len(data) >= 33 and data[:16] == IMAGE_SIG + b"\0\0\0\rIHDR", "Expected a PNG source")
    w, h = struct.unpack(">II", data[16:24])
    require(0 < w <= 20000 and 0 < h <= 20000 and w*h <= MAX_PIXELS, "PNG dimensions exceed safety limit")
    return w, h


def migrate_state(value):
    require(type(value) is dict and type(value.get("schema")) is int, "Missing integer schema version")
    # Future adapters must be recorded as tool transitions. Never rewrite old hashed events.
    require(value["schema"] == SCHEMA, f"Unsupported project schema {value['schema']}")
    return value


def validate_object(obj):
    exact(obj, OBJECT_FIELDS, "layer")
    require(obj["kind"] in KINDS and obj["source"] in SOURCES and obj["edge"] in EDGE_TYPES,
            "Unknown layer type, source or edge classification")
    require(string(obj["name"], 160) and string(obj["alt"], 4000) and string(obj["label"], 160),
            "Invalid layer labels")
    require(obj["parent"] is None or identifier(obj["parent"]), "Invalid group ID")
    require(type(obj["visible"]) is bool and type(obj["locked"]) is bool, "Invalid layer flags")
    for k, lo, hi in [("x", -20000, 20000), ("y", -20000, 20000), ("w", 1, 20000),
                      ("h", 1, 20000), ("angle", -360, 360), ("border", 0, 100),
                      ("opacity", 0, 1), ("size", 4, 360), ("line_height", .7, 4),
                      ("padding", 0, 300), ("radius", 0, 300)]:
        require(numeric(obj[k], lo, hi), f"Invalid layer {k}")
    require(all(color(obj[k]) for k in ("fill", "stroke", "ink")), "Invalid RGBA color")
    require(string(obj["text"]), "Invalid text")
    require(type(obj["spans"]) is list and len(obj["spans"]) <= 4000, "Too many style spans")
    pos = 0
    for span in obj["spans"]:
        require(type(span) is list and len(span) == 4 and all(type(i) is int for i in span[:2])
                and pos <= span[0] < span[1] <= len(obj["text"])
                and all(type(b) is bool for b in span[2:]), "Invalid text style span")
        pos = span[1]
    pts = obj["points"]
    require(type(pts) is list and len(pts) <= 4096 and all(
        type(p) is list and len(p) == 2 and all(numeric(v, 0, 1) for v in p) for p in pts),
        "Invalid vector points")
    require(obj["asset"] is None or hash_ok(obj["asset"]), "Invalid image asset hash")
    require((obj["kind"] == "image") == (obj["asset"] is not None), "Invalid placed image")
    c = obj["crop"]
    require(type(c) is list and len(c) == 4 and all(numeric(v, 0, 1) for v in c) and
            c[2] > 0 and c[3] > 0 and c[0] + c[2] <= 1.000001 and c[1] + c[3] <= 1.000001,
            "Invalid image crop")
    require(all(obj[k] is None or obj["kind"] == "connector" and identifier(obj[k])
                for k in ("start", "end")), "Invalid connector anchor")


def validate_state(s):
    migrate_state(s)
    exact(s, STATE_FIELDS, "project")
    require(identifier(s["project"]) and s["topic"] in TOPICS and type(s["template"]) is bool,
            "Invalid project identity or topic")
    p = s["paper"]
    exact(p, ("w", "h", "background"), "paper")
    require(numeric(p["w"], 38, 1200) and numeric(p["h"], 38, 1200) and color(p["background"]),
            "Paper must be 38–1200 mm per side")
    exact(s["meta"], META_FIELDS, "metadata")
    require(all(string(v, 8000) for v in s["meta"].values()), "Invalid project metadata")
    require(s["scaffold"] in ("", *GUIDANCE.get(s["topic"], ())), "Invalid scaffold")
    require(type(s["guides"]) is list and len(s["guides"]) <= 100 and all(
        type(g) is list and len(g) == 2 and g[0] in ("x", "y") and numeric(g[1], 0, 1200)
        for g in s["guides"]), "Invalid guides")
    objects, order, assets, exports = (s[k] for k in ("objects", "order", "assets", "exports"))
    require(type(objects) is dict and len(objects) <= MAX_OBJECTS and type(order) is list
            and len(order) == len(objects) and all(identifier(k) for k in order)
            and set(order) == set(objects), "Invalid layer order")
    require(type(assets) is dict and len(assets) <= 250, "Invalid source inventory")
    require(type(exports) is dict and len(exports) <= 400, "Invalid export inventory")
    for key, asset in assets.items():
        exact(asset, ("name", "size", "width", "height", "type", "imported", "media", "license",
                      "credit", "notes"), "asset")
        require(hash_ok(key) and filename(asset["name"]) and asset["type"] in SOURCES
                and date_ok(asset["imported"]) and type(asset["size"]) is int
                and 1 <= asset["size"] <= MAX_FILE
                and all(string(asset[k], 4000) for k in ("license", "credit", "notes")), "Invalid asset metadata")
        require(asset["media"] in ("png", "md", "txt", "pdf"), "Invalid asset format")
        w, h = asset["width"], asset["height"]
        require(type(w) is int and type(h) is int and
                (0 < w <= 20000 and 0 < h <= 20000 and w*h <= MAX_PIXELS if asset["media"] == "png"
                 else (w, h) == (0, 0)), "Invalid asset dimensions")
    for key, info in exports.items():
        exact(info, ("sha256", "name", "size", "settings", "utc"), "export")
        require(identifier(key) and hash_ok(info["sha256"]) and filename(info["name"])
                and date_ok(info["utc"]) and type(info["size"]) is int
                and 0 < info["size"] <= MAX_FILE and string(info["settings"], 4000),
                "Invalid artifact metadata")
    for key, obj in objects.items():
        validate_object(obj)
        if obj["asset"] is not None:
            require(obj["asset"] in assets and assets[obj["asset"]]["media"] == "png"
                    and obj["source"] == assets[obj["asset"]]["type"], "Image source missing or relabeled")
        seen, parent = {key}, obj["parent"]
        while parent is not None:
            require(parent in objects and parent not in seen and len(seen) < 24
                    and objects[parent].get("kind") == "group", "Missing, cyclic or too deeply nested group")
            seen.add(parent)
            parent = objects[parent].get("parent")
        for endpoint in (obj["start"], obj["end"]):
            if endpoint is not None:
                require(endpoint in objects and objects[endpoint]["kind"] not in ("group", "connector"),
                        "Invalid linked endpoint")
    return s


def patches(before, after):
    if before is None:
        return [[key, None, None, after[key]] for key in STATE_FIELDS]
    out = []
    for key in STATE_FIELDS:
        a, b = before[key], after[key]
        if a == b:
            continue
        if key in MAPS:
            out.extend([key, sub, a.get(sub), b.get(sub)] for sub in sorted(a.keys() | b.keys())
                       if a.get(sub) != b.get(sub))
        else:
            out.append([key, None, a, b])
    return out


def apply(state, patch, reverse=False):
    out = {} if state is None else copy.deepcopy(state)
    require(type(patch) is list and len(patch) <= MAX_OBJECTS * 2 + 30, "Invalid history patch")
    seen = set()
    for entry in patch:
        require(type(entry) is list and len(entry) == 4, "Invalid patch entry")
        key, sub, a, b = entry
        require(key in STATE_FIELDS and (sub is None or key in MAPS and string(sub, 64)),
                "Invalid patch address")
        require((key, sub) not in seen, "Duplicate patch address")
        seen.add((key, sub))
        if reverse:
            a, b = b, a
        target, field = (out, key) if sub is None else (out[key], sub)
        require(target.get(field) == a, f"History before-state mismatch at {key}")
        if b is None:
            target.pop(field, None)
        else:
            target[field] = copy.deepcopy(b)
    return out


def validate_events(events, snapshot_bytes, expected, anchor, keep=False):
    require(type(events) is list and 0 < len(events) <= MAX_EVENTS, "Invalid history length")
    state, prev, states, used_snaps = None, GENESIS, [], set()
    for seq, event in enumerate(events, 1):
        exact(event, EVENT_FIELDS, f"event {seq}")
        require(type(event["seq"]) is int and event["seq"] == seq and event["previous"] == prev
                and identifier(event["session"]) and event["project"] == expected["project"]
                and event["origin"] in ORIGINS and date_ok(event["utc"])
                and datetime.fromisoformat(event["utc"]).utcoffset().total_seconds() == 0
                and type(event["offset_minutes"]) is int and -840 <= event["offset_minutes"] <= 840
                and string(event["action"], 120) and string(event["actor"], 8000)
                and type(event["details"]) is dict and type(event["environment"]) is dict
                and all(type(event[k]) is list for k in ("objects", "assets")),
                f"Invalid event sequence, identity or metadata at {seq}")
        require(all(identifier(k) for k in event["objects"]) and all(hash_ok(k) for k in event["assets"]),
                f"Invalid event object or asset references at {seq}")
        payload = {k: v for k, v in event.items() if k != "hash"}
        require(event["hash"] == digest(canonical(payload)), f"Event hash mismatch at {seq}")
        prev = event["hash"]
        try:
            state = validate_state(apply(state, event["patch"]))
        except (KeyError, TypeError, AttributeError, Invalid) as exc:
            raise Invalid(f"Event {seq}: {exc}") from exc
        require(state["project"] == expected["project"] and state["topic"] == expected["topic"]
                and state["template"] == expected["template"] and all(h in expected["assets"] for h in event["assets"]),
                f"History changed project identity or points to missing assets at {seq}")
        if seq == 1:
            require(event["action"] == "Create blank project" and not state["objects"] and
                    event["snapshot"] is not None, "Missing blank genesis snapshot")
        if event["snapshot"] is not None:
            snap = event["snapshot"]
            require(hash_ok(snap) and snapshot_bytes.get(snap) == canonical(state)
                    and digest(snapshot_bytes[snap]) == snap, f"Snapshot missing or changed at event {seq}")
            used_snaps.add(snap)
        if keep:
            states.append(state)
    require(state == expected, "Saved canvas differs from replayed history")
    require(used_snaps == set(snapshot_bytes), "Unexpected snapshot")
    require(anchor == {"count": len(events), "head": prev, "project": expected["project"],
                       "state": digest(canonical(expected))}, "Saved history anchor mismatch")
    return states


def safe_member(name):
    if not isinstance(name, str) or not name or len(name) > 260 or any(
            ord(c) < 32 or c in '\\:<' + '>"|?*' for c in name):
        return False
    components = name.split("/")
    device = re.compile(r"(?i)^(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(\.|$)")
    return all(p not in ("", ".", "..") and not p.endswith((" ", ".")) and not device.search(p)
               for p in components)


def read_zip(source):
    """No extraction. Validate names, entry types, case collisions and budgets before decompression."""
    seen = set()
    try:
        if isinstance(source, (str, Path)):
            require(Path(source).stat().st_size <= MAX_TOTAL, "Archive size limit exceeded")
        try_source = io.BytesIO(source) if isinstance(source, bytes) else source
        with zipfile.ZipFile(try_source) as archive:
            infos = archive.infolist()
            require(0 < len(infos) <= 22000 and sum(i.file_size for i in infos) <= MAX_TOTAL,
                    "Archive member or expanded-size limit exceeded")
            for i in infos:
                name = i.filename
                folded = name.casefold()
                require(i.orig_filename == name and safe_member(name) and folded not in seen,
                        f"Unsafe or duplicate ZIP member: {name!r}")
                seen.add(folded)
                typ = stat.S_IFMT(i.external_attr >> 16)
                require(not i.is_dir() and typ in (0, stat.S_IFREG) and not (i.flag_bits & 1)
                        and 0 <= i.file_size <= MAX_FILE
                        and i.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                        f"Unsupported archive entry: {name!r}")
            for name in seen:
                require(not any(str(p) in seen for p in PurePosixPath(name).parents),
                        "Archive file/directory collision")
            return {i.filename: archive.read(i) for i in infos}
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as exc:
        raise Invalid(f"Invalid archive: {exc}") from exc


def check_manifest(files):
    require("manifest.json" in files, "Missing SHA-256 manifest")
    manifest = parse(files["manifest.json"])
    errors = []
    require(type(manifest) is dict and "manifest.json" not in manifest, "Invalid manifest")
    for k in sorted(set(files) - {"manifest.json"} - set(manifest)):
        errors.append(f"Unexpected member: {k}")
    for k, sha in manifest.items():
        if k not in files:
            errors.append(f"Missing member: {k}")
        elif not hash_ok(sha) or digest(files[k]) != sha:
            errors.append(f"Digest mismatch: {k}")
    require(not errors, "; ".join(errors))


def verify_project(files):
    check_manifest(files)
    try:
        state = validate_state(parse(files["project.json"]))
        anchor = parse(files["anchor.json"])
        raw_events = files["audit.jsonl"].splitlines()
        require(len(raw_events) <= MAX_EVENTS, "Too many events")
        events = [parse(line) for line in raw_events]
        require(all(canonical(event) == line for event, line in zip(events, raw_events)),
                "Noncanonical event encoding")
        snapshot_bytes = {k[10:-5]: v for k, v in files.items() if k.startswith("snapshots/") and k.endswith(".json")}
        validate_events(events, snapshot_bytes, state, anchor)
        required = {"project.json", "anchor.json", "audit.jsonl", "audit.sqlite", "preview.png", "manifest.json"}
        png_dimensions(files["preview.png"])
        required.update(f"snapshots/{k}.json" for k in snapshot_bytes)
        for key, asset in state["assets"].items():
            name = f"assets/{key}.{asset['media']}"
            required.add(name)
            data = files[name]
            require(digest(data) == key and len(data) == asset["size"], f"Source changed: {name}")
            if asset["media"] == "png":
                require(png_dimensions(data) == (asset["width"], asset["height"]), f"Source dimensions changed: {name}")
        for info in state["exports"].values():
            name = f"artifacts/{info['sha256']}"
            required.add(name)
            data = files[name]
            require(digest(data) == info["sha256"] and len(data) == info["size"], f"Artifact changed: {name}")
        require(required == set(files), "Unexpected or missing project members: " + ", ".join(sorted(required ^ set(files))))
        # The app never executes SQL from an imported file. Compare with an independently regenerated
        # ledger where SQLite uses the same database format; always compare logical rows on verification CLI.
        require(files["audit.sqlite"].startswith(b"SQLite format 3\0"), "Invalid SQLite ledger header")
        return state, events, snapshot_bytes, anchor
    except (KeyError, TypeError, AttributeError) as exc:
        raise Invalid(f"Missing or malformed project entry: {exc}") from exc


def verify_ledger(raw, events):
    import sqlite3
    import tempfile
    try:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "audit.sqlite"
            path.write_bytes(raw)
            db = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
            try:
                db.execute("PRAGMA trusted_schema=OFF")
                db.execute("PRAGMA query_only=ON")
                require(db.execute("SELECT type FROM sqlite_schema WHERE name = 'events'").fetchall()
                        == [("table",)], "SQLite events table is absent")
                callbacks = iter(range(100000))
                db.set_progress_handler(lambda: next(callbacks, None) is None, 100)
                rows = db.execute("SELECT seq, body FROM events ORDER BY seq LIMIT ?", (MAX_EVENTS + 1,)).fetchall()
            finally:
                db.close()
        require(rows == [(e["seq"], canonical(e)) for e in events], "SQLite ledger differs from verified JSONL")
    except sqlite3.Error as exc:
        raise Invalid(f"Invalid SQLite ledger: {exc}") from exc


def verify_path(path, expected_head=None):
    items = read_zip(path)
    if "project.marproj" in items:
        check_manifest(items)
        require(KIT <= set(items), "Evidence bundle is missing required content")
        project = read_zip(items["project.marproj"])
        state, events, snaps, anchor = verify_project(project)
        require(items["history/audit.jsonl"] == project["audit.jsonl"] and
                items["history/audit.sqlite"] == project["audit.sqlite"], "Exported ledger differs from project")
        require(parse(items["anchor.json"]) == anchor, "Bundle anchor differs from project")
        require(parse(items["verification.json"]).get("anchor") == anchor, "Verification report anchor differs")
        for name, data in project.items():
            if name.startswith(("assets/", "snapshots/")):
                require(items.get(name) == data, f"Missing or changed bundle copy: {name}")
        delivered = {digest(v) for k, v in items.items() if k.startswith("exports/")}
        require(delivered == {v["sha256"] for v in state["exports"].values()}, "Bundle export list differs from project")
    else:
        project = items
        state, events, snaps, anchor = verify_project(project)
    verify_ledger(project["audit.sqlite"], events)
    if expected_head is not None:
        require(expected_head == anchor["head"], "History head differs from independently kept value")
    return {"status": "verified", "anchor": anchor, "sources": len(state["assets"]),
            "snapshots": len(snaps), "exports": len(state["exports"]),
            "limits": "Internal consistency only; hashes cannot authenticate identity, clock, or a rewritten entire archive."}


def main():
    cli = argparse.ArgumentParser(description="Verify a local MAR project or evidence bundle without extracting it.")
    cli.add_argument("archive", type=Path)
    cli.add_argument("--expected-head", help="Hash previously retained through a trusted independent channel")
    args = cli.parse_args()
    try:
        print(json.dumps(verify_path(args.archive, args.expected_head), indent=2))
    except (Invalid, OSError, TypeError) as exc:
        print(f"VERIFICATION FAILED: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

