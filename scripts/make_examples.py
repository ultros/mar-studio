"""Generate two blank templates and a clearly labeled automated evidence fixture."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QApplication

from marstudio.briefs import TITLES
from marstudio.evidence import bundle, export_file
from marstudio.project import Project, atomic_write, layer, uid
from marstudio.render import FACE, png_bytes, preview, raster, register_fonts


def seed(p, *objects):
    def set_layers(s):
        for o in objects:
            k = uid()
            s["objects"][k] = o
            s["order"].append(k)
    p.edit("Build software demonstration fixture",set_layers,"noncreative_tool",undoable=False,
           details={"author":"automated test fixture; no user input or authorship represented"})


def main():
    app = QApplication([])
    register_fonts()
    templates = ROOT/"examples"; templates.mkdir(exist_ok=True)
    for topic, name in [("architecture","MAR-Architecture-Blank.marproj"),
                        ("hypothesis","Why-I-Propose-This-System-Blank.marproj")]:
        p = Project(topic,TITLES[topic],template=True)
        p.save(templates/name,preview(p.state,p.sources))
        p.db.close()

    output = ROOT/"output"
    (output/"pdf").mkdir(exist_ok=True,parents=True)
    p = Project("general","Example: Vector Workflow Fixture",template=True)
    p.edit("Set example fixture paper and disclosure",lambda s:(s["paper"].update(w=210,h=225,background="#f7fbfaff"),
        s["meta"].update(ai_use="This software-generated test fixture includes no claim about real designs or original source images.",
                         gaps="The requested MAR originals were not provided for this example. The imported image is a QA fixture.")),
        "noncreative_tool",undoable=False)
    with tempfile.TemporaryDirectory() as t:
        work = Path(t)
        fixture = raster((440,220))
        with QPainter(fixture) as paint:
            fixture.fill(QColor("#d4eae5"))
            paint.setPen(QColor("#184d48"))
            font = QFont(FACE); font.setPixelSize(23); font.setBold(True); paint.setFont(font)
            paint.drawText(QPointF(35,90),"Synthetic source image")
            font.setPixelSize(15); font.setBold(False); paint.setFont(font)
            paint.drawText(QPointF(35,132),"Generated for import QA. Not an original.")
        source=work/"Synthetic-QA-Reference.png"; source.write_bytes(png_bytes(fixture))
        sha = p.import_file(source,notes="Programmatically generated fixture; not one of the supplied MAR images.")
        seed(p,layer("panel",0,0,210,63,fill="#132b3dff",border=0,radius=0,source="template"),
             layer("text",14,9,180,13,text="MAR STUDIO   /   FORMAT CHECK",size=11,ink="#8de2ccff",
                   spans=[[0,29,True,False]],source="template"),
             layer("text",14,23,180,22,text="Vector workflow fixture",size=30,ink="#ffffffff",
                   spans=[[0,23,True,False]],source="template"),
             layer("text",14,43,178,15,text="Software-generated demonstration • no operator authorship",size=12,
                   ink="#ccede8ff",source="template"))
        p.checkpoint("Save fixture intermediate state")
        seed(p,layer("text",14,72,177,23,text="01   /   Editable shapes, typography, and links",size=16,
                   spans=[[0,2,True,False],[11,26,True,False]],source="template"),
             layer("panel",17,95,72,43,text="ITALIA\nLabel → review",size=16,spans=[[0,6,True,False]],
                   fill="#e3f3f0ff",source="template"),
             layer("panel",121,95,72,43,text="SHQIPËRIA\nReview → decision",size=16,spans=[[0,9,True,False]],
                   fill="#e3f3f0ff",source="template"),
             layer("arrow",90,115,30,1,points=[[.08,.5],[.91,.5]],stroke="#156f62ff",border=1,source="template"),
             layer("callout",17,151,176,47,text="02   /   Print test\nAccented labels: Italia • Shqipëria.\nIllustrative figures: €98,000–180,000.",
                   size=14,spans=[[0,19,True,False],[66,87,False,True]],fill="#ffffffff",
                   radius=3,source="template"),
             layer("text",14,202,182,15,text="Fixture artwork only. Locked source excluded from this page.",size=10,
                   source="template"))
        for kind,name in [("svg","fixture.svg"),("png","fixture-300dpi.png"),("pdf","fixture-poster.pdf"),
                          ("comparison","fixture-comparison.png")]:
            export_file(p,work/name,kind,source=sha)
        sha, report = bundle(p,output/"example-evidence-bundle.zip",worksheet=True,example=True)
        atomic_write(output/"pdf"/"example-authorship-record.pdf",report)
        print(f"Example bundle SHA-256: {sha}")
        print(f"Example history head: {p.anchor()['head']}")


if __name__=="__main__":
    main()

