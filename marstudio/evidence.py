"""Local print exports, comparison sheets and independently verifiable evidence bundles."""
from __future__ import annotations

import io
from collections import Counter
from pathlib import Path
from xml.sax.saxutils import escape

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter

from .integrity import DISCLAIMER, canonical, digest, require, verify_ledger, verify_project
from .project import archive_bytes, atomic_write, utc
from .render import (FACE, FONTS, decode_png, png_bytes, preview, print_pdf, print_png, raster, svg_export)

USCO_AI = "https://www.copyright.gov/ai/"
USCO_REG = "https://www.copyright.gov/ai/ai_policy_guidance.pdf"
BUNDLE_README = """MAR Studio · local evidence bundle
Run: python verify_bundle.py evidence_bundle.zip
Use a verified copy of the verifier to check untrusted bundles. It does not extract ZIP members.
Optionally pass --expected-head with a history hash previously held by an independent party.
The manifest excludes its own entry and the outer ZIP. Keep the ZIP's SHA-256 separately if needed.
The report describes the checkpoint immediately before its own artifact was recorded.
The project includes subsequent report/export entries. A source filename is not proof of origin.
The archive contains original source bytes and plaintext personal notes/history. Share selectively.
Self-declared identity and system-clock readings are unverified. This is not a legal certificate.
"""


def comparison(project, source=None, seq=None):
    state = project.state_at(seq) if seq else project.state
    before = project.state_at(1)
    images = []
    if source and source in project.sources and project.state["assets"][source]["media"] == "png":
        images.append(("LOCKED SOURCE · separate", decode_png(project.sources[source], source)))
    for title, document, touched in [
        ("FIRST SAVED STATE", before, None),
        ("SELECTED REVISION" if seq else "CURRENT DESIGN", state, None),
        ("GUI-TOUCHED LAYERS · pink", state, {key for e in project.events[:seq]
                                            if e["origin"] == "manual_gui" for key in e["objects"]}),
    ]:
        images.append((title, decode_png(preview(document, project.sources, 610, highlight=touched,
                                                 only=touched if touched is not None else None))))
    img = raster((len(images)*377+24, 640))
    with QPainter(img) as p:
        p.fillRect(img.rect(),QColor("#112333"))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        f = QFont(FACE); f.setPixelSize(14); f.setBold(True); p.setFont(f)
        for i,(title,image) in enumerate(images):
            x = i*377+17
            p.setPen(QColor("#d9e8e6"))
            p.drawText(QPointF(x,31),title)
            p.fillRect(QRectF(x,48,365,574),QColor("#203849"))
            ratio = min(345/image.width(),554/image.height())
            w,h = image.width()*ratio,image.height()*ratio
            p.drawImage(QRectF(x+(365-w)/2,48+(574-h)/2,w,h),image)
    return png_bytes(img)


def verified_status(project):
    files = project.members(preview(project.state, project.sources, 480))
    files["manifest.json"] = canonical({k: digest(v) for k, v in files.items()})
    state, events, snaps, anchor = verify_project(files)
    verify_ledger(files["audit.sqlite"], events)
    return {"status": "verified", "anchor": anchor, "events": len(events), "snapshots": len(snaps),
            "sources": len(state["assets"]), "exports": len(state["exports"]),
            "limits": "Identity and device time are unverified. Whole-history rewriting needs an external anchor to detect."}


def report_pdf(project, status, *, worksheet=False, example=False):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image, KeepTogether, LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer

    for name, file in [("MAR-Reg","DejaVuSans.ttf"),("MAR-Bold","DejaVuSans-Bold.ttf")]:
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name,str(FONTS/file)))
    pdfmetrics.registerFontFamily("MAR-Reg", normal="MAR-Reg", bold="MAR-Bold", italic="MAR-Reg", boldItalic="MAR-Bold")
    navy, slate, teal, frost = (colors.HexColor(c) for c in ("#142639","#476173","#156f62","#e9f4f0"))
    title = ParagraphStyle("Title", fontName="MAR-Bold",fontSize=25,leading=30,textColor=navy,spaceAfter=12)
    h2 = ParagraphStyle("Subhead",fontName="MAR-Bold",fontSize=14,leading=19,textColor=navy,spaceBefore=14,spaceAfter=6)
    body = ParagraphStyle("Body",fontName="MAR-Reg",fontSize=9,leading=14,textColor=navy,spaceAfter=7,splitLongWords=True)
    small = ParagraphStyle("Small",parent=body,fontSize=7.5,leading=11,textColor=slate,spaceAfter=0)
    badge = ParagraphStyle("Badge",parent=small,fontName="MAR-Bold",fontSize=9,leading=13,textColor=teal,spaceAfter=9)
    table_head = ParagraphStyle("Table Head",parent=small,textColor=colors.white,fontName="MAR-Bold")
    out = io.BytesIO()
    doc = SimpleDocTemplate(out,pagesize=(612,792),rightMargin=43,leftMargin=43,topMargin=46,bottomMargin=47,
                            title="MAR Authorship & Design Provenance Record", author="", creator="MAR Studio",
                            subject="Local design documentation; not a legal certificate")
    w = doc.width
    story = []
    state, events = project.state, project.events
    meta = state["meta"]
    def p(value, style=body):
        return Paragraph(escape(str(value)).replace("\n","<br/>"),style)
    def text(value, style=body):
        story.append(p(value,style))
    def section(label):
        text(label,h2)
    def value(label,key):
        text(label,badge)
        text(meta[key] or "Not provided")
    def grid(headers, rows, widths, style=small):
        cells = [[p(x,table_head) for x in headers],
                 *[[p(x,style) if isinstance(x,str) else x for x in row] for row in rows]]
        story.append(LongTable(cells,colWidths=widths,repeatRows=1,splitInRow=1,hAlign="LEFT",style=[
            ("BACKGROUND",(0,0),(-1,0),navy),("ROWBACKGROUNDS",(0,1),(-1,-1),[frost,colors.white]),
            ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
            ("VALIGN",(0,0),(-1,-1),"TOP"),("LINEBELOW",(0,0),(-1,0),.3,teal)]))
    def pic(data, maxw, maxh):
        decoded = decode_png(data)
        ratio = min(maxw/decoded.width(),maxh/decoded.height())
        return Image(io.BytesIO(data),width=decoded.width()*ratio,height=decoded.height()*ratio)
    def bottom(c, d):
        c.setStrokeColor(frost); c.line(43,41,569,41)
        c.setFillColor(slate); c.setFont("MAR-Reg",8)
        c.drawString(43,27,"MAR STUDIO  /  LOCAL DESIGN RECORD")
        c.drawRightString(569,27,str(d.page))

    text("EXAMPLE / SOFTWARE-GENERATED FIXTURE" if example else
         ("TEMPLATE / NO AUTHORSHIP DETERMINATION" if state["template"] else "DESIGN RECORD / LOCAL EVIDENCE"),badge)
    text("MAR Authorship & Design Provenance Record",title)
    text(meta["title"] or "Untitled",h2)
    text(DISCLAIMER + " This record does not establish copyrightability, registration or legal approval.")
    grid(("Field","Recorded information"),[
        ("Author / owner credit", meta["credit"] or "Not provided"),
        ("Operator (self-declared)", meta["actor"] or "Not provided"),
        ("Scope of claim", meta["claim"] or "Not provided"),
        ("Report time (device clock, UTC)", utc()),
        ("Build and schema", f"Studio {project.environment['studio']} · schema {state['schema']} · {project.environment['os']} {project.environment['os_release']} · Python {project.environment['python']}"),
        ("Internal hash verification", status["status"] + f" · {len(events)} events · {len(project.snapshots)} distinct snapshots"),
        ("Pre-report checkpoint SHA-256", status["anchor"]["head"]),
    ], [151,w-151])
    section("Creative decisions entered by the operator")
    text(meta["decisions"] or "No statement supplied. The application has generated no first-person account.")
    section("Disclosure and evidentiary limits")
    text("Event counts describe application activity. They do not prove creativity, identity, authorship, original creation independently of a source, or time. Local evidence can be rewritten by someone with sufficient access. The author credit is self-declared; the device clock was not independently checked.")
    text(meta["gaps"] or "No additional limitations entered by the operator.")
    if example:
        text("All fixture content and test activity were generated by software for demonstration. No user edit or claim of authorship is represented.",badge)

    story.append(PageBreak())
    section("Canvas progression")
    text("First blank canvas, an intermediate state if available, and the current design. Pink outlines mark surviving layers touched by recorded GUI commands; they may include reused or unknown material.")
    def artwork(s):
        return s["paper"],s["objects"],s["order"]
    blank, current = artwork(project.state_at(1)),artwork(state)
    intermediate = None
    from .integrity import parse
    for e in reversed(events[1:-1]):
        if e["snapshot"] and artwork(parse(project.snapshots[e["snapshot"]])) not in (blank,current):
            intermediate = e["seq"]
            break
    keys = [1] + ([intermediate] if intermediate else []) + [len(events)]
    widths = [w/len(keys)]*len(keys)
    images = [pic(preview(project.state_at(seq),project.sources,620,
                          highlight=project.manual_objects() if seq == len(events) else None),
                  widths[i]-14,230) for i,seq in enumerate(keys)]
    labels = ["01 · Blank"] + (["02 · Checkpoint"] if len(keys)==3 else []) + ["Current"]
    grid(labels,[images,[f"Event {i:04d}" for i in keys]],widths)
    section("Source inventory")
    if not state["assets"]:
        text("No references imported.")
    for sha, asset in state["assets"].items():
        story.append(KeepTogether([p(asset["name"],badge),p(f"{asset['type']} · {asset['media'].upper()} · {asset['size']:,} bytes"
                                      + (f" · {asset['width']} × {asset['height']} px" if asset["width"] else ""))]))
        if asset["media"] == "png":
            story.append(pic(project.sources[sha],180,100))
        text(f"SHA-256: {sha}  |  Import observed: {asset['imported']}",small)
        for label,key in [("Credit","credit"),("License","license"),("Source notes","notes")]:
            text(f"{label}: {asset[key] or 'not provided'}",small)
        story.append(Spacer(1,10))

    story.append(PageBreak())
    section("AI use and other material")
    for label,key in [("Human-authored text, as declared","human_text"),("Composition and modifications, as declared","composition"),
                      ("AI-generated material and its reuse","ai_use"),("Third-party and licensed elements","third_party"),
                      ("Unknown authorship or source","unknown"),("Text / research sources and verbatim vs reconstructed notes","references"),
                      ("Visual licenses or credits","licenses"),("Fonts and permissions","font_notes"),("Drafts and other records","drafts")]:
        value(label,key)
    if worksheet:
        section("Optional registration drafting worksheet")
        text("This worksheet is an operator-entered draft. No form was filed, and the software does not determine what should be registered.")
        value("Confirmed human-created material (operator entry)","worksheet_created")
        value("AI-generated and other material to exclude (operator entry)","worksheet_excluded")
    section("Official U.S. Copyright Office resources")
    for label,url in [("Copyright and Artificial Intelligence (including Part 2)",USCO_AI),
                      ("Registration Guidance for Works Containing AI-Generated Material",USCO_REG)]:
        story.append(Paragraph(f'<link href="{url}" color="#156f62">{label}</link><br/>{url}',body))

    story.append(PageBreak())
    section("Event and custody index")
    dist = Counter(e["origin"] for e in events)
    text("Events by recorded origin: " + "; ".join(f"{k}: {v}" for k,v in sorted(dist.items())) + ". Full logs: history/audit.jsonl and history/audit.sqlite in the evidence bundle.")
    manual = [e for e in events if e["origin"]=="manual_gui"]
    sample = {e["seq"]: e for e in [*events[:2], *manual[:3], *events[-3:]]}
    grid(("Seq.","Action and origin","Observed UTC / SHA-256"),[
        (str(i),f"{e['action']}\n{e['origin']}",f"{e['utc']}\n{e['hash']}") for i,e in sorted(sample.items())
    ], [37,153,w-190])
    story.append(Spacer(1,10))
    rows = [(f"Source · {a['name']}",a["imported"],sha) for sha,a in state["assets"].items()]
    seen = set()
    for e in events:
        sha = e["snapshot"]
        if sha and sha not in seen:
            seen.add(sha)
            rows.append((f"State · event {e['seq']}",e["utc"],sha))
    rows.extend((f"Export · {a['name']}\n{a['settings']}",a["utc"],a["sha256"]) for a in state["exports"].values())
    grid(("Item","Observed UTC","SHA-256"),rows,[164,119,w-283])
    text("This PDF and the outer bundle are identified separately; they cannot each contain their own digest.",small)

    doc.build(story,onFirstPage=bottom,onLaterPages=bottom)
    return out.getvalue()


def export_file(project, path, kind, *, dpi=300, source=None, worksheet=False, example=False):
    path = Path(path)
    require(kind in ("svg","pdf","png","comparison","report"),"Unknown export type")
    info = f"{kind}; {project.state['paper']['w']} × {project.state['paper']['h']} mm"
    if kind=="png":
        info += f"; {dpi} dpi"
    if kind=="comparison":
        info += f"; separate source {source or 'none'}"
    if kind=="report":
        info += f"; worksheet {'included' if worksheet else 'omitted'}"
    project.checkpoint("Capture export checkpoint",details={"settings":info})
    s = project.state; sources = project.sources
    if kind=="report":
        data = report_pdf(project,verified_status(project),worksheet=worksheet,example=example)
    elif kind=="svg":
        data = svg_export(s,sources)
    elif kind=="pdf":
        data = print_pdf(s,sources)
    elif kind=="png":
        data = print_png(s,sources,dpi)
    else:
        data = comparison(project,source)
    atomic_write(path,data)
    return project.archive_artifact(path,data,info)


def bundle(project, path, *, worksheet=False, example=False):
    project.checkpoint("Capture evidence report checkpoint",details={"worksheet":worksheet,"example":example})
    report = report_pdf(project,verified_status(project),worksheet=worksheet,example=example)
    project.archive_artifact("report.pdf",report,"Authorship and design provenance report; embedded in evidence bundle")
    project.checkpoint("Assemble evidence bundle",details={"worksheet":worksheet})
    status = verified_status(project)
    project_files = project.members(preview(project.state,project.sources,600))
    dedicated = {}
    for seq, info in enumerate(project.state["exports"].values(),1):
        safe = "".join(c for c in info["name"] if c not in '\\/:<>"|?*').strip(" .") or "artifact"
        dedicated[f"exports/{seq:04d}-{safe[:120]}"] = project.artifacts[info["sha256"]]
    members = {
        "project.marproj":archive_bytes(project_files), "report.pdf":report,
        "README.txt":BUNDLE_README.encode("utf-8"),
        "verify_bundle.py":Path(__file__).with_name("integrity.py").read_bytes()
        if Path(__file__).with_name("integrity.py").is_file() else _embedded_verifier(),
        "history/audit.jsonl":project_files["audit.jsonl"],"history/audit.sqlite":project_files["audit.sqlite"],
        "verification.json":canonical(status),"anchor.json":canonical(project.anchor()),
        **{k:v for k,v in project_files.items() if k.startswith(("assets/","snapshots/"))}, **dedicated,
    }
    data = archive_bytes(members)
    atomic_write(path,data)
    return digest(data), report


def _embedded_verifier():
    import sys
    return (Path(sys._MEIPASS)/"marstudio"/"integrity.py").read_bytes()

