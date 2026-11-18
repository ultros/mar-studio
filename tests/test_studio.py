"""Integration tests. Synthetic test inputs are never committed as manual GUI evidence."""
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")

from PIL import Image, ImageDraw
from pypdf import PdfReader
from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QToolBar

from marstudio.evidence import bundle, export_file
from marstudio.integrity import (SCENARIO_NOTE, Invalid, canonical, digest, parse, read_zip, verify_path,
                                 verify_project)
from marstudio.photo import PASTE_TOOL, filtered, levels, open_pixels, paste_pixels
from marstudio.photo_ui import COLOR_TOOL, MOVE_TOOL, PhotoEditor
from marstudio.project import Project, archive_bytes, layer, uid
from marstudio.render import png_bytes, preview, print_pdf, print_png, raster, register_fonts, svg_export
from marstudio.ui import RichDialog, Studio

APP = QApplication.instance() or QApplication([])
APP.setApplicationName("MAR Studio Test")
APP.setStyle("Fusion")
APP.setQuitOnLastWindowClosed(False)
register_fonts()
ROOT = Path(__file__).resolve().parents[1]


def add(project,*items):
    ids = [uid() for _ in items]
    project.edit("Set up automated test fixture",
                 lambda s:(s["objects"].update(zip(ids,items)),s["order"].extend(ids)),
                 "noncreative_tool")
    return ids


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        self.p = Project()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.p.db.close)

    def png(self,name,color):
        f = self.work/name
        image = raster((80,58)); image.fill(QColor(color))
        image.setText("Description","TEST-ONLY-SOURCE-METADATA")
        data = png_bytes(image); f.write_bytes(data); return f,data

    def save(self,p=None,name="test.marproj"):
        p = p or self.p; target=self.work/name
        p.save(target,preview(p.state,p.sources,200)); return target

    def test_two_sources_preserved_reference_not_exported(self):
        first,raw1 = self.png("reference-a.png","#e212e2")
        second,raw2 = self.png("reference-b.png","#0ee555")
        hashes=[self.p.import_file(first,"AI-generated"),self.p.import_file(second,"mixed")]
        self.assertEqual(hashes,[digest(raw1),digest(raw2)])
        self.assertEqual([first.read_bytes(),second.read_bytes()],[raw1,raw2])
        self.assertEqual({e["origin"] for e in self.p.events},{"noncreative_tool","automated_import"})
        self.assertEqual(self.p.state["objects"],{})
        path = self.save()
        opened = Project.load(path)
        self.addCleanup(opened.db.close)
        self.assertEqual(opened.sources,dict(zip(hashes,[raw1,raw2])))
        image = Image.open(io.BytesIO(print_png(opened.state,opened.sources,300)))
        self.assertEqual(image.getpixel((1232,1032)),(255,255,255,255))
        self.assertNotIn(b"data:image/png",svg_export(opened.state,opened.sources))
        self.assertEqual(verify_path(path)["sources"],2)
        add(opened, layer("image",8,8,20,15,asset=hashes[0],source="AI-generated",crop=[.1,.1,.8,.8]))
        import base64
        svg=svg_export(opened.state,opened.sources)
        png64=svg.split(b"data:image/png;base64,",1)[1].split(b'"',1)[0]
        self.assertNotIn(b"TEST-ONLY-SOURCE-METADATA",base64.b64decode(png64))
        with self.assertRaises(Invalid):
            opened.edit("Mislabel reused source", lambda s:next(iter(s["objects"].values())).update(source="user-declared human"),
                        "noncreative_tool")

    def test_history_persists_undo_redo_and_replay(self):
        key,=add(self.p,layer("text",12,23,170,75,text="Review → Shqipëria\n€98,000–180,000",
                              spans=[[0,6,True,False]],source="unknown"))
        frame = deepcopy(self.p.state)
        self.p.edit("Fixture geometry change", lambda s:s["objects"][key].update(x=45.25,ink="#121314ff"),
                    "noncreative_tool")
        moved = deepcopy(self.p.state)
        self.p.checkpoint("Intermediate fixture")
        self.assertEqual(self.p.undo()["details"]["target"],3)
        self.assertEqual(self.p.state,frame)
        loaded=Project.load(self.save()); self.addCleanup(loaded.db.close)
        self.assertNotEqual(loaded.session,self.p.session)
        self.assertEqual(loaded.state,frame)
        self.assertEqual(loaded.redo()["origin"],"noncreative_tool")
        self.assertEqual(loaded.state,moved)
        self.assertEqual(loaded.state_at(3),moved)
        self.assertEqual(loaded.state_at(2),frame)
        self.assertEqual(loaded.state_at(1)["objects"],{})
        self.assertEqual(verify_path(self.save(loaded))["anchor"],loaded.anchor())
        with self.assertRaises(sqlite3.DatabaseError):
            loaded.db.execute("DELETE FROM events WHERE seq=1")

    def test_changed_deleted_reordered_and_truncated_events(self):
        key,=add(self.p,layer("panel"))
        self.p.edit("Change fill fixture",lambda s:s["objects"][key].update(fill="#81EFF0ff"),"noncreative_tool")
        self.p.checkpoint("State after fixture changes")
        base=read_zip(self.save()); base.pop("manifest.json")
        events=base["audit.jsonl"].splitlines()
        changed=parse(events[1]); changed["origin"]="unknown"
        variants=[events[:1]+[canonical(changed)]+events[2:],events[:1]+events[2:],
                  events[:1]+[events[2],events[1]]+events[3:],events[:-1]]
        for n,lines in enumerate(variants):
            with self.subTest(n=n), self.assertRaises(Invalid):
                files=dict(base, **{"audit.jsonl":b"\n".join(lines)+b"\n"})
                verify_project(read_zip(archive_bytes(files)))
        with self.assertRaisesRegex(Invalid,"independently"):
            verify_path(self.save(),expected_head="1"*64)

    def test_source_and_checkpoint_mutation(self):
        f, raw=self.png("ref.png","#006699")
        h=self.p.import_file(f)
        self.p.checkpoint("More than one checkpoint")
        files=read_zip(self.save())
        files.pop("manifest.json")
        with self.assertRaisesRegex(Invalid,"Source changed"):
            bad=dict(files,**{f"assets/{h}.png":raw+b"different"})
            verify_project(read_zip(archive_bytes(bad)))
        with self.assertRaisesRegex(Invalid,"Snapshot missing or changed"):
            k=next(k for k in files if k.startswith("snapshots/"))
            bad=dict(files,**{k:b"{}"})
            verify_project(read_zip(archive_bytes(bad)))

    def test_concurrent_representation_mismatch(self):
        add(self.p,layer("panel"))
        base=read_zip(self.save()); base.pop("manifest.json")
        other=Project()
        self.addCleanup(other.db.close)
        base["audit.sqlite"]=other.db.serialize()
        f=self.work/"mismatch.marproj"; f.write_bytes(archive_bytes(base))
        with self.assertRaisesRegex(Invalid,"ledger differs"):
            Project.load(f)

    def test_crash_before_replace_leaves_previous_commit(self):
        target=self.save(); initial=target.read_bytes()
        prog = (
            "import os,sys; from marstudio.project import atomic_write;"
            "atomic_write(sys.argv[1],b'new bytes',before_replace=lambda:os._exit(73))"
        )
        result=subprocess.run([sys.executable,"-c",prog,str(target)],cwd=ROOT,check=False)
        self.assertEqual(result.returncode,73)
        self.assertEqual(target.read_bytes(),initial)
        self.assertEqual(verify_path(target)["anchor"]["count"],1)
        add(self.p,layer("circle"))
        self.save()
        self.assertEqual(verify_path(target)["anchor"]["count"],2)

    def test_hostile_zip_json_svg_and_fake_png(self):
        for names in [("../outside",),("safe/../../x",),("C:/bad",),("safe\\bad",),
                      ("a","A"),("aux.png",),("duplicate","duplicate"),("file","file/child")]:
            archive=io.BytesIO()
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore",UserWarning)
                with zipfile.ZipFile(archive,"w") as z:
                    for n in names: z.writestr(n.replace('\\','^'),b"x")
            with self.subTest(names=names), self.assertRaises(Invalid):
                read_zip(archive.getvalue().replace(b"safe^bad",b"safe\\bad"))
        for sample in [b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":1e900}',b'["\\udfff"]',b"["*90+b"1"+b"]"*90]:
            with self.subTest(sample=sample), self.assertRaises(Invalid):
                parse(sample)
        hostile=self.work/"not-supported.svg"
        hostile.write_text('<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',encoding="utf-8")
        with self.assertRaisesRegex(Invalid,"SVG is not imported"):
            self.p.import_file(hostile)
        unsupported=self.work/"pretends.png"; unsupported.write_bytes(hostile.read_bytes())
        with self.assertRaisesRegex(Invalid,"Expected a PNG"):
            self.p.import_file(unsupported)

    def test_mar_print_formats_rich_unicode_dimensions_and_notice(self):
        p=Project("hypothesis","Why I Propose This System")
        self.addCleanup(p.db.close)
        text="Italia and Shqipëria → Human review\n€98,000–180,000 · illustrative only"
        add(p,layer("text",15,25,173,55,size=22,text=text,spans=[[0,6,True,False],[11,20,False,True]],
                    alt="Summary <appears safely>"),
            layer("arrow",23,105,110,16,points=[[0,.8],[1,.2]],edge="Money transfer"))
        svg=svg_export(p.state,p.sources)
        root=ET.fromstring(svg)
        ns={"s":"http://www.w3.org/2000/svg"}
        cells=root.findall(".//s:text",ns)
        self.assertTrue(any(c.attrib.get("font-style")=="italic" for c in cells))
        full="".join(c.text or "" for c in cells)
        self.assertIn("Shqipëria",full)
        self.assertIn("€98,000–180,000",full)
        self.assertIn(SCENARIO_NOTE,full)
        self.assertIn("Money transfer · UNPROVEN",full)
        self.assertEqual(root.get("width"),"210mm")
        self.assertNotIn(b"<script",svg)
        pdf=PdfReader(io.BytesIO(print_pdf(p.state,p.sources)))
        page=pdf.pages[0]; extracted=page.extract_text()
        self.assertAlmostEqual(float(page.mediabox.width),595,delta=1)
        self.assertAlmostEqual(float(page.mediabox.height),842,delta=1)
        for marker in ("Shqipëria","€98,000–180,000","UNPROVEN"):
            self.assertIn(marker,extracted)
        for dpi in (300,600):
            image=Image.open(io.BytesIO(print_png(p.state,p.sources,dpi)))
            self.assertEqual(image.size,(round(210*dpi/25.4),round(297*dpi/25.4)))
            self.assertAlmostEqual(image.info["dpi"][0],dpi,delta=.02)

    def test_custom_landscape_print_page(self):
        self.p.edit("Landscape fixture page",lambda s:s["paper"].update(w=297,h=210),"noncreative_tool")
        page=PdfReader(io.BytesIO(print_pdf(self.p.state,{}))).pages[0]
        self.assertAlmostEqual(float(page.mediabox.width),842,delta=1)
        self.assertAlmostEqual(float(page.mediabox.height),595,delta=1)

    def test_photo_levels_and_filters_keep_transparency(self):
        image = Image.new("RGBA", (7, 7), (80, 97, 154, 0))
        image.putpixel((3, 3), (100, 97, 154, 255))
        adjusted = levels(image, 50, 200, 1.0, 100, "Red")
        self.assertEqual(adjusted.getpixel((3, 3)), (85, 97, 154, 255))
        self.assertEqual(adjusted.getpixel((0, 0))[3], 0)
        blurred = filtered(image, "Gaussian blur", 1, 100)
        self.assertGreater(blurred.getpixel((3, 2))[3], 0)
        self.assertLess(blurred.getpixel((3, 3))[3], 255)
        sharpened = filtered(image, "Unsharp mask", 1, 200)
        self.assertGreater(sharpened.getpixel((3, 3))[0], 100)
        self.assertEqual(sharpened.getchannel("A").tobytes(), image.getchannel("A").tobytes())

    def test_rotated_paste_does_not_bring_hidden_background_color_into_edges(self):
        image = Image.new("RGBA", (10, 10), (0, 255, 0, 0))
        ImageDraw.Draw(image).polygon([(0, 0), (2, 0), (2, 7), (9, 7), (9, 9), (0, 9)],
                                      fill=(240, 0, 0, 128))
        target = Image.new("RGBA", (60, 60))
        result = paste_pixels(target, image, (18, 20), (20, 20), 33)
        self.assertEqual(result.getpixel((0, 0))[3], 0)
        self.assertIn(128, result.getchannel("A").get_flattened_data())
        colors = {green for _, green, _, alpha in result.get_flattened_data() if alpha}
        self.assertEqual(colors, {0})

    def test_paste_flips_both_axes_and_scales_alpha(self):
        source = Image.new("RGBA", (4, 4))
        source.putpixel((0, 1), (220, 30, 75, 200))
        target = Image.new("RGBA", (8, 8))
        target.putpixel((2, 3), (35, 80, 150, 255))
        result = paste_pixels(target, source, (2, 2), source.size, 0, opacity=50, flip_x=True, flip_y=True)
        self.assertEqual(result.getpixel((5, 4)), (220, 30, 75, 100))
        self.assertEqual(result.getpixel((2, 3)), (35, 80, 150, 255))
        self.assertEqual(result.getpixel((5, 3))[3], 0)
        self.assertEqual(source.getpixel((0, 1))[3], 200)

    def test_bundle_and_report_independent_hashes_unknown_fields(self):
        loaded=self.p
        key,=add(loaded,layer("text",10,20,180,50,text="Design fixture.\n€98,000 with Shqipëria"))
        export_file(loaded,self.work/"poster.svg","svg")
        f=self.work/"example.zip"; h,raw=bundle(loaded,f,worksheet=True,example=True)
        self.assertEqual(digest(f.read_bytes()),h)
        self.assertEqual(verify_path(f,loaded.anchor()["head"])["exports"],2)
        files=read_zip(f)
        for name,sha in parse(files["manifest.json"]).items():
            self.assertEqual(digest(files[name]),sha)
        self.assertNotIn("manifest.json",parse(files["manifest.json"]))
        self.assertEqual(raw,files["report.pdf"])
        pdf=PdfReader(io.BytesIO(raw))
        text="\n".join(pg.extract_text() for pg in pdf.pages)
        for token in ["Authorship & Design Provenance","No statement supplied","Not provided",
                      "SOFTWARE-GENERATED FIXTURE","registration drafting worksheet"]:
            self.assertIn(token," ".join(text.split()))
        self.assertFalse(pdf.metadata.author)
        self.assertEqual(loaded.state["meta"]["actor"],"")
        self.assertNotIn("manual_gui",{e["origin"] for e in loaded.events})
        verifier=self.work/"verify_bundle.py"; verifier.write_bytes(files["verify_bundle.py"])
        result=subprocess.run([sys.executable,str(verifier),str(f)],capture_output=True,check=True)
        self.assertIn(b'"status": "verified"',result.stdout)


class TestStudio(Studio):
    def commit(self, action, mutation, origin="manual_gui", **kwargs):
        # Exercise real UI machinery with synthetic Qt input without attributing it to any person.
        if origin=="manual_gui":
            origin="unknown"
            kwargs["details"]={**(kwargs.get("details") or {}),"test_only":"synthetic UI input"}
        return super().commit(action,mutation,origin,**kwargs)


class GuiWorkflow(unittest.TestCase):
    def setUp(self):
        self.w=TestStudio()
        self.w.show(); APP.processEvents(); self.w.canvas.fit(); APP.processEvents()
        self.canvas=self.w.canvas
        self.mouse=self.canvas.viewport()
        self.tmp=tempfile.TemporaryDirectory()
        self.w.recovery=Path(self.tmp.name)/"recovery"
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.finish)

    def finish(self):
        self.w.dirty=False; self.w.timer.stop(); self.w.close(); self.w.project.db.close()

    def drag(self,x,y,x2,y2):
        view=self.canvas
        a,b=view.mapFromScene(QPointF(x,y)),view.mapFromScene(QPointF(x2,y2))
        QTest.mousePress(self.mouse,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,a)
        QTest.mouseMove(self.mouse,b,25)
        QTest.mouseRelease(self.mouse,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,b)
        APP.processEvents()

    def click_tool(self, name):
        toolbar = self.w.findChild(QToolBar, "editingTools")
        button = toolbar.widgetForAction(self.w.tool_actions[name])
        self.assertTrue(button.isVisible())
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.canvas.active_tool, name)

    def test_visible_tools_select_and_drag_under_locked_layers_and_paths(self):
        self.click_tool("panel")
        self.drag(20, 30, 80, 70)
        panel, = self.w.selected
        base = self.w.project.state["objects"][panel]
        add(self.w.project, layer("path", 10, 10, 130, 100), layer("panel", 20, 30, 60, 40, locked=True))
        self.w.refresh()
        self.click_tool("select")

        start = self.canvas.mapFromScene(QPointF(43, 57))
        end = self.canvas.mapFromScene(QPointF(73, 77))
        QTest.mousePress(self.mouse, Qt.MouseButton.LeftButton, pos=start)
        self.assertEqual(self.w.selected, [panel])
        # A release can arrive with no final mouse-move sample.
        QTest.mouseRelease(self.mouse, Qt.MouseButton.LeftButton, pos=end)
        updated = self.w.project.state["objects"][panel]
        self.assertEqual((updated["x"], updated["y"]), (base["x"] + 30, base["y"] + 20))
        self.assertEqual(self.w.project.events[-1]["action"], "Drag selection")
        self.w.history_step(False)
        self.assertEqual(self.w.project.state["objects"][panel], base)

    def choose_image_region_action(self, label):
        dialog = APP.activeModalWidget()
        if isinstance(dialog, QMessageBox):
            for button in dialog.buttons():
                if button.text() == label:
                    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                    return
            dialog.reject()

    def test_image_region_can_be_cut_copied_moved_and_undone(self):
        source_file = Path(self.tmp.name) / "region-test.png"
        fixture = raster((100, 80))
        fixture.fill(QColor("#5a85ae"))
        raw = png_bytes(fixture)
        source_file.write_bytes(raw)
        sha = self.w.project.import_file(source_file, "AI-generated")
        image, = add(self.w.project, layer("image", 20, 30, 100, 80, asset=sha,
                                           source="AI-generated", border=0))
        self.w.refresh()
        initial = deepcopy(self.w.project.state)

        self.click_tool("region")
        QTimer.singleShot(150, lambda: self.choose_image_region_action("Cut Out"))
        self.drag(40, 50, 75, 78)
        self.assertEqual(self.w.project.events[-1]["action"], "Cut out image region")
        self.assertEqual(self.w.selected, [image])
        pieces = self.w.project.state["objects"]
        self.assertEqual(len(pieces), 5)
        self.assertAlmostEqual(sum(o["w"] * o["h"] for o in pieces.values()), 8000, delta=0.1)
        self.assertTrue(all(o["asset"] == sha and o["source"] == "AI-generated" for o in pieces.values()))
        self.assertEqual(self.canvas.active_tool, "select")

        self.drag(56, 61, 146, 119)
        self.assertEqual(self.w.project.events[-1]["action"], "Drag selection")
        poster = preview(self.w.project.state, self.w.project.sources, max_side=297)
        poster_image = Image.open(io.BytesIO(poster)).convert("RGB")
        self.assertEqual(poster_image.getpixel((55, 62)), (255, 255, 255))
        self.assertEqual(poster_image.getpixel((150, 121)), (90, 133, 174))
        self.w.history_step(False)
        self.w.history_step(False)
        self.assertEqual(self.w.project.state, initial)

        self.click_tool("region")
        QTimer.singleShot(150, lambda: self.choose_image_region_action("Copy Region"))
        self.drag(40, 50, 75, 78)
        self.assertEqual(self.w.project.events[-1]["action"], "Copy image region")
        copied, = self.w.selected
        self.assertNotEqual(copied, image)
        self.assertEqual(self.w.project.state["objects"][image], initial["objects"][image])
        self.assertEqual(self.w.project.sources[sha], raw)
        self.assertEqual(source_file.read_bytes(), raw)

    def test_pixel_workspace_applies_brush_clone_and_eraser_and_retains_history(self):
        source = Path(self.tmp.name) / "photo.png"
        Image.new("RGB", (80, 60), (48, 97, 154)).save(source)
        original_bytes = source.read_bytes()
        sha = self.w.project.import_file(source, "AI-generated")
        key, = add(self.w.project, layer("image", asset=sha, source="AI-generated"))
        self.w.refresh()
        self.w.set_selection([key])
        errors = []

        def use_editor():
            dialog = APP.activeModalWidget()
            try:
                self.assertIsInstance(dialog, PhotoEditor)
                view = dialog.view
                def click(x, y, button=Qt.MouseButton.LeftButton):
                    QTest.mouseClick(view.viewport(), button, pos=view.mapFromScene(QPointF(x, y)))

                dialog.tool.setCurrentText("Rectangle select")
                QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=view.mapFromScene(QPointF(10, 10)))
                QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=view.mapFromScene(QPointF(26, 25)))
                dialog.tool.setCurrentText("Brush")
                dialog.size.setValue(13)
                dialog.set_color(QColor("#d23946"))
                click(23, 19)
                self.assertEqual(dialog.edits.image.getpixel((23, 19)), (210, 57, 70, 255))
                self.assertEqual(dialog.edits.image.getpixel((28, 19)), (48, 97, 154, 255))
                dialog.clear_selection()
                dialog.tool.setCurrentText("Clone stamp")
                click(23, 19, Qt.MouseButton.RightButton)
                click(55, 35)
                self.assertEqual(dialog.edits.image.getpixel((55, 35)), (210, 57, 70, 255))
                dialog.tool.setCurrentText("Eraser")
                click(43, 48)
                self.assertEqual(dialog.edits.image.getpixel((43, 48))[3], 0)
                dialog.undo()
                self.assertEqual(dialog.edits.image.getpixel((43, 48))[3], 255)
                dialog.redo()
                dialog.before.setChecked(True)
                self.assertEqual(dialog.view.surface.image.pixelColor(43, 48).alpha(), 255)
                dialog.before.setChecked(False)
                self.assertEqual(dialog.view.surface.image.pixelColor(43, 48).alpha(), 0)
                dialog.set_selection([(4, 39), (12, 47)], rectangle=True)
                dialog.tool.setCurrentText(MOVE_TOOL)
                QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=view.mapFromScene(QPointF(8, 43)))
                QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=view.mapFromScene(QPointF(18, 43)))
                self.assertEqual(dialog.edits.image.getpixel((8, 43))[3], 0)
                self.assertEqual(dialog.edits.image.getpixel((18, 43)), (48, 97, 154, 255))
                QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)
            except Exception as exc:
                errors.append(exc)
                if isinstance(dialog, QDialog):
                    dialog.reject()

        QTimer.singleShot(120, use_editor)
        self.w.edit_selected_pixels()
        if errors:
            raise errors[0]
        self.assertEqual(self.w.project.events[-1]["action"], "Edit image pixels")
        self.assertEqual([entry["tool"] for entry in self.w.project.events[-1]["details"]["operations"]],
                         ["Brush", "Clone stamp", "Eraser", MOVE_TOOL])
        raster_hash = self.w.project.state["objects"][key]["asset"]
        self.assertNotEqual(raster_hash, sha)
        self.assertEqual(self.w.project.state["assets"][raster_hash]["type"], "AI-generated")
        original = self.w.project.sources[sha]
        self.assertEqual(original, original_bytes)
        self.assertEqual(open_pixels(self.w.project.sources[raster_hash], [0, 0, 1, 1])
                         .getpixel((55, 35)), (210, 57, 70, 255))
        self.w.history_step(False)
        self.assertEqual(self.w.project.state["objects"][key]["asset"], sha)
        file = Path(self.tmp.name) / "photo-edits.marproj"
        self.w.project.save(file, preview(self.w.project.state, self.w.project.sources, max_side=200))
        verified = verify_path(file)
        self.assertEqual(verified["sources"], 2)
        self.assertTrue(self.w.open_path(file))
        self.w.history_step(True)
        self.assertEqual(self.w.project.state["objects"][key]["asset"], raster_hash)

    def test_exported_cutout_and_hole_keep_alpha_and_allow_selection_through(self):
        source = Path(self.tmp.name) / "cutout.png"
        image = Image.new("RGB", (60, 60), "white")
        ImageDraw.Draw(image).polygon([(10, 10), (15, 10), (15, 24), (29, 24), (29, 29), (10, 29)],
                                      fill="#375788")
        image.save(source)
        sha = self.w.project.import_file(source)
        bottom, key = add(self.w.project, layer("panel", 117, 108, 25, 24),
                          layer("image", 20, 30, 60, 60, asset=sha, border=0, fill="#00000000"))
        self.w.project.edit("Set up transparent page", lambda d: d["paper"].update(background="#00000000"),
                            "noncreative_tool")
        self.w.refresh()
        errors = []

        def create_cutout(cut):
            dialog = APP.activeModalWidget()
            try:
                self.assertIsInstance(dialog, PhotoEditor)
                dialog.tool.setCurrentText(COLOR_TOOL)
                view = dialog.view
                QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.mapFromScene(QPointF(5, 5)))
                dialog.invert_selection()
                dialog.cutout_preview.setChecked(True)
                self.assertEqual(view.surface.image.pixelColor(12, 15).alpha(), 255)
                self.assertEqual(view.surface.image.pixelColor(20, 15).alpha(), 0)
                dialog.send_to_layer(cut)
            except Exception as exc:
                errors.append(exc)
                if isinstance(dialog, QDialog):
                    dialog.reject()

        self.w.set_selection([key])
        QTimer.singleShot(120, lambda: create_cutout(False))
        self.w.edit_selected_pixels()
        if errors:
            raise errors[0]
        self.assertEqual(self.w.project.events[-1]["action"], "Copy selection to new layer")
        self.assertEqual(self.w.project.state["objects"][key]["asset"], sha)
        self.w.history_step(False)

        self.w.set_selection([key])
        QTimer.singleShot(120, lambda: create_cutout(True))
        self.w.edit_selected_pixels()
        if errors:
            raise errors[0]
        self.assertEqual(self.w.project.events[-1]["action"], "Cut selection to new layer")
        cutout, = self.w.selected
        artwork = self.w.project.state["objects"][cutout]
        self.assertEqual((artwork["x"], artwork["y"], artwork["w"], artwork["h"]), (30, 40, 20, 20))
        self.assertNotEqual(self.w.project.state["objects"][key]["asset"], sha)
        self.drag(33, 45, 123, 115)
        self.assertEqual(self.w.project.events[-1]["action"], "Drag selection")
        self.w.set_selection([])
        QTest.mouseClick(self.mouse, Qt.MouseButton.LeftButton,
                         pos=self.canvas.mapFromScene(QPointF(131, 115)))
        self.assertEqual(self.w.selected, [bottom])
        result = Image.open(io.BytesIO(preview(self.w.project.state, self.w.project.sources, max_side=297)))
        self.assertEqual(result.getpixel((33, 45))[3], 0)
        self.assertEqual(result.getpixel((123, 115)), (55, 87, 136, 255))
        self.assertEqual(result.getpixel((131, 115)), (233, 244, 241, 255))
        moved = deepcopy(self.w.project.state)
        self.w.history_step(False)
        self.w.history_step(False)
        self.assertEqual(self.w.project.state["objects"][key]["asset"], sha)
        self.assertNotIn(cutout, self.w.project.state["objects"])
        saved = Path(self.tmp.name) / "transparent-cutout.marproj"
        self.w.project.save(saved, preview(self.w.project.state, self.w.project.sources, max_side=200))
        self.assertEqual(verify_path(saved)["sources"], 3)
        self.assertTrue(self.w.open_path(saved))
        self.w.history_step(True)
        self.w.history_step(True)
        self.assertEqual(self.w.project.state, moved)

    def test_paste_pixels_into_existing_layer_preserves_alpha_and_tracks_sources(self):
        folder = Path(self.tmp.name)
        source = Image.new("RGBA", (24, 20))
        ImageDraw.Draw(source).polygon([(4, 4), (7, 4), (7, 11), (13, 11), (13, 14), (4, 14)],
                                       fill=(220, 40, 80, 128))
        source.save(folder / "paste-source.png")
        target = Image.new("RGBA", (70, 60))
        target.putpixel((29, 12), (25, 60, 95, 255))
        target.save(folder / "paste-target.png")
        donor = self.w.project.import_file(folder / "paste-source.png", "AI-generated")
        recipient = self.w.project.import_file(folder / "paste-target.png", "user-declared human")
        source_layer, target_layer = add(self.w.project, layer("image", asset=donor, source="AI-generated"),
                                         layer("image", asset=recipient, source="user-declared human",
                                               fill="#00000000"))
        self.w.refresh()
        errors = []

        def use_editor(work):
            dialog = APP.activeModalWidget()
            try:
                self.assertIsInstance(dialog, PhotoEditor)
                work(dialog)
            except Exception as exc:
                errors.append(exc)
                if isinstance(dialog, QDialog):
                    dialog.reject()
                    if dialog.isVisible():
                        dialog.reject()

        def copy(dialog):
            dialog.tabs.setCurrentIndex(1)
            dialog.set_selection([(2, 2), (16, 16)], rectangle=True)
            QTest.mouseClick(dialog.copy_pixels_button, Qt.MouseButton.LeftButton)
            self.assertEqual(dialog.clipboard.clip.source_assets, (donor,))
            dialog.reject()

        self.w.set_selection([source_layer])
        QTimer.singleShot(120, lambda: use_editor(copy))
        self.w.edit_selected_pixels()
        if errors:
            raise errors[0]

        def paste(dialog):
            self.assertIsNotNone(dialog.floating_clip)
            dialog.paste_width.setValue(20)
            dialog.paste_rotation.setValue(63)
            dialog.paste_flip_x.setChecked(True)
            dialog.paste_flip_y.setChecked(True)
            dialog.paste_opacity.setValue(25)
            dialog.reset_paste()
            self.assertEqual((dialog.paste_width.value(), dialog.paste_height.value(),
                              dialog.paste_rotation.value(), dialog.paste_opacity.value()), (10, 11, 0, 100))
            self.assertFalse(dialog.paste_flip_x.isChecked() or dialog.paste_flip_y.isChecked())
            self.assertEqual(dialog.paste_position, (30, 24))
            dialog.cancel_paste()
            self.assertFalse(dialog.edits.changed())
            view = dialog.view
            view.setFocus()
            QTest.keyClick(view, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
            self.assertIsNotNone(dialog.floating_clip)
            start = view.mapFromScene(QPointF(32, 26))
            end = view.mapFromScene(QPointF(23, 12))
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(view.viewport(), end, 20)
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
            QTest.keyClick(view, Qt.Key.Key_Right)
            self.assertEqual(dialog.paste_position, (22, 10))
            self.assertFalse(dialog.edits.changed())
            QTest.keyClick(view, Qt.Key.Key_Return)
            self.assertIsNone(dialog.floating_clip)
            self.assertEqual(dialog.edits.image.getpixel((23, 12)), (220, 40, 80, 128))
            self.assertEqual(dialog.edits.image.getpixel((29, 12)), (25, 60, 95, 255))
            self.assertEqual(dialog.edits.image.getpixel((30, 12))[3], 0)
            dialog.undo()
            dialog.start_paste()
            corners, _, _ = view.paste_handles()
            corner = corners[(1, 1)]
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=view.mapFromScene(corner))
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton,
                               pos=view.mapFromScene(corner + QPointF(10, 11)))
            self.assertEqual((dialog.paste_width.value(), dialog.paste_height.value()), (20, 22))
            self.assertEqual(dialog.paste_position, (30, 24))
            dialog.paste_ratio.setChecked(False)
            dialog.paste_height.setValue(30)
            _, _, rotation = view.paste_handles()
            center = dialog.paste_transform().map(dialog.paste_rect().center())
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=view.mapFromScene(rotation))
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier,
                               pos=view.mapFromScene(center + QPointF(23, -1)))
            self.assertEqual(dialog.paste_rotation.value(), 90)
            # This point is inside the rotated preview and outside the unrotated box.
            start = view.mapFromScene(QPointF(27, 30))
            end = view.mapFromScene(QPointF(20, 16))
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(view.viewport(), end, 20)
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
            self.assertEqual(dialog.paste_position, (23, 10))
            self.assertFalse(dialog.edits.changed())
            dialog.paste_flip_x.setChecked(True)
            dialog.paste_opacity.setValue(50)
            QTest.keyClick(view, Qt.Key.Key_Return)
            red, _, _, alpha = dialog.edits.image.getpixel((40, 31))
            self.assertGreater(red, 210)
            self.assertEqual(alpha, 64)
            self.assertEqual(dialog.edits.image.getpixel((40, 19))[3], 0)
            self.assertEqual(dialog.edits.image.getpixel((29, 12)), (25, 60, 95, 255))
            QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)

        self.w.set_selection([target_layer])
        QTimer.singleShot(120, lambda: use_editor(paste))
        self.w.paste_selected_pixels()
        if errors:
            raise errors[0]
        self.assertEqual(set(self.w.project.state["objects"]), {source_layer, target_layer})
        self.assertEqual(self.w.project.state["objects"][source_layer]["asset"], donor)
        merged = self.w.project.state["objects"][target_layer]
        self.assertEqual(merged["source"], "mixed")
        operation = self.w.project.events[-1]["details"]["operations"][-1]
        self.assertEqual((operation["tool"], operation["source_assets"], operation["position_px"]),
                         (PASTE_TOOL, [donor], [23, 10]))
        self.assertEqual((operation["size_px"], operation["rotation_deg"]), ([20, 30], 90))
        self.assertEqual((operation["opacity_pct"], operation["flip_horizontal"], operation["flip_vertical"]),
                         (50, True, False))
        saved = folder / "pasted-selection.marproj"
        self.w.project.save(saved, preview(self.w.project.state, self.w.project.sources, max_side=200))
        self.assertEqual(verify_path(saved)["sources"], 3)
        self.w.history_step(False)
        self.assertEqual(self.w.project.state["objects"][target_layer]["asset"], recipient)

    def write_test_dialog(self):
        for window in APP.topLevelWidgets():
            if isinstance(window,RichDialog) and window.isVisible():
                window.field.setPlainText("Italia → Shqipëria\nReview €98,000")
                c=window.field.textCursor(); c.setPosition(0); c.setPosition(6,QTextCursor.MoveMode.KeepAnchor)
                window.field.setTextCursor(c); window.style("bold")
                QTest.keyClick(window.field,Qt.Key.Key_Return,Qt.KeyboardModifier.ControlModifier)
                return
        # Prevent a failed shortcut/dialog open from hanging future checks.
        APP.exit(1)

    def test_mouse_keyboard_layers_snap_geometry_and_reopen(self):
        self.w.select_tool(2)
        self.drag(18.2,24.1,82.2,52.1)
        (a,)=self.w.selected
        first=self.w.project.state["objects"][a]
        self.assertAlmostEqual(first["x"],20,delta=.01)
        self.assertAlmostEqual(first["y"],25,delta=.01)
        QTest.keyClick(self.canvas,Qt.Key.Key_Right)
        self.assertEqual(self.w.project.state["objects"][a]["x"],first["x"]+1)
        self.w.change_property("fill","#51798bff")
        self.w.select_tool(5); self.drag(25,90,75,118)
        b=self.w.selected[0]
        self.assertEqual(self.w.project.state["objects"][b]["kind"],"path")
        self.w.select_tool(1)
        QTimer.singleShot(150,self.write_test_dialog)
        QTimer.singleShot(2000,lambda: [d.reject() for d in APP.topLevelWidgets() if isinstance(d,RichDialog) and d.isVisible()])
        self.drag(90,120,178,175)
        t=self.w.selected[0]
        self.assertEqual(self.w.project.state["objects"][t]["text"],"Italia → Shqipëria\nReview €98,000")
        self.assertEqual(self.w.project.state["objects"][t]["spans"],[[0,6,True,False]])
        self.w.set_selection([a,b]); self.w.group(); group=self.w.selected[0]
        self.w.set_selection([group,t]); self.w.group(); outer=self.w.selected[0]
        self.assertEqual(self.w.project.state["objects"][group]["parent"],outer)
        self.w.nudge(2,0)
        self.assertEqual(self.w.project.state["objects"][a]["x"],first["x"]+3)
        self.w.duplicate()
        self.assertEqual(len(self.w.project.state["objects"]),10)
        self.w.history_step(False)
        self.assertEqual(len(self.w.project.state["objects"]),5)
        self.w.history_step(True)
        self.assertEqual(len(self.w.project.state["objects"]),10)
        file=Path(self.tmp.name)/"ui.marproj"; self.w.path=file
        self.assertTrue(self.w.save())
        state=deepcopy(self.w.project.state)
        self.assertTrue(self.w.open_path(file))
        self.assertEqual(self.w.project.state,state)
        self.assertTrue(any(e["origin"]=="unknown" and "test_only" in e["details"] for e in self.w.project.events))
        self.assertNotIn("manual_gui",{e["origin"] for e in self.w.project.events})


if __name__=="__main__":
    unittest.main()

