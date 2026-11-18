"""MAR Studio desktop shell. Only real GUI commands enter the manual_gui channel."""
from __future__ import annotations

import math
from collections import Counter
from copy import deepcopy
from functools import partial
from pathlib import Path

from PySide6.QtCore import QMimeData, QPointF, QRectF, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QKeySequence, QPixmap, QTextCharFormat, QTextCursor, QTransform
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QColorDialog,
    QComboBox, QDialog, QDialogButtonBox, QDockWidget, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QSlider, QSplitter, QTabWidget, QTextEdit, QToolBar, QToolButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget)

from .briefs import BRIEFS, TITLES
from .canvas import Canvas
from .evidence import bundle, comparison, export_file, verified_status
from .integrity import (DISCLAIMER, EDGE_TYPES, GUIDANCE, META_FIELDS, SOURCES, Invalid,
                        canonical, identifier, parse, require, validate_object)
from .photo import PixelClipboard
from .project import Project, ancestry, atomic_write, descendants, families, front_layers, is_locked, layer, uid
from .render import FACE, decode_png, preview, qhex, resolved, rgba

ROLE = Qt.ItemDataRole.UserRole
MIME = "application/x-marstudio-layers-json"
TOOLS = [
    ("select","Select / move","V"),("text","Text box","T"),("panel","Panel","R"),("circle","Ellipse","O"),
    ("polygon","Polygon","Y"),("path","Freehand path","P"),("arrow","Arrow","A"),("connector","Linked connector","C"),
    ("label","Label","L"),("chart","Bar chart","B"),("grid","Grid",""),("callout","Callout",""),
    ("icon","Check icon",""),("placeholder","Image placeholder",""),("region","Image Region","M")
]
VISIBLE_TOOLS = ("select", "text", "panel", "circle", "arrow", "path", "region")
TEXT_KINDS = ("text","panel","label","callout")
STYLE = """
QMainWindow,QWidget { background:#182738; color:#e6eff4; font-family:'Segoe UI','DejaVu Sans'; font-size:13px; }
QLabel#brand { color:#8ce4d3; font-weight:750; font-size:14px; padding:6px 14px; }
QLabel#hint { color:#a8bfca; padding:5px; }
QToolBar,QMenuBar,QMenu,QStatusBar { background:#213345; border:0; spacing:5px; }
QMenu::item:selected,QMenuBar::item:selected,QTabBar::tab:selected { background:#32625e; color:#f5fffc; }
QDockWidget::title { background:#263b4c; padding:8px; color:#a9ddd4; font-weight:700; }
QTabBar::tab { background:#24374a; padding:7px 10px; border:0; }
QTreeWidget,QListWidget,QTextEdit,QPlainTextEdit,QLineEdit,QDoubleSpinBox,QComboBox,QScrollArea {
  background:#122131; color:#e6eff4; border:1px solid #364d5e; border-radius:4px; padding:3px;
  selection-background-color:#286359; selection-color:#f8fffd;
}
QHeaderView::section { color:#b6cbd2; background:#24384a; border:none; padding:5px; }
QToolButton,QPushButton { color:#edf8f7; background:#263d50; border:1px solid #395365; border-radius:4px; padding:5px 8px; }
QToolButton:hover,QPushButton:hover { background:#33586a; }
QPushButton:pressed,QToolButton:checked,QPushButton:checked { background:#28776b; border-color:#75c6b8; }
QPushButton:disabled,QToolButton:disabled { color:#758b96; }
QSplitter::handle,QMainWindow::separator { background:#314a5c; width:4px; height:4px; }
QStatusBar { color:#c1d4dd; }
QGraphicsView#canvas { border:0; background:#213243; }
"""


def column(widget=None, margin=8):
    widget = widget or QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(margin,margin,margin,margin)
    layout.setSpacing(6)
    return widget,layout


def hint(text):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label


def button_row(*items):
    w = QWidget(); lay = QHBoxLayout(w); lay.setContentsMargins(0,0,0,0); lay.setSpacing(5)
    for name,fn in items:
        b = QPushButton(name); b.clicked.connect(fn); lay.addWidget(b)
    return w


def buttons(dialog):
    b = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
    b.accepted.connect(dialog.accept); b.rejected.connect(dialog.reject)
    return b


def color_pick(parent,value):
    result = QColorDialog.getColor(rgba(value),parent,"Choose color",QColorDialog.ColorDialogOption.ShowAlphaChannel)
    return qhex(result) if result.isValid() else None


def cp_to_utf16(text, pos):
    return len(text[:pos].encode("utf-16-le")) // 2


def image_slice(image, bounds, name):
    source_x, source_y, source_width, source_height = image["crop"]
    fx, fy = bounds.x() / image["w"], bounds.y() / image["h"]
    fw, fh = bounds.width() / image["w"], bounds.height() / image["h"]
    transform = QTransform().translate(image["x"], image["y"]).rotate(image["angle"])
    position = transform.map(bounds.topLeft())
    return dict(image, name=name, x=round(position.x(), 4), y=round(position.y(), 4),
                w=round(bounds.width(), 4), h=round(bounds.height(), 4), border=0,
                crop=[source_x + fx * source_width, source_y + fy * source_height,
                      fw * source_width, fh * source_height])


class RichDialog(QDialog):
    def __init__(self,window,obj):
        super().__init__(window)
        self.setWindowTitle("Edit text · " + obj["name"])
        self.resize(650,435)
        _,layout = column(self,12)
        layout.addWidget(hint("Edit the selected layer. Ctrl+B / Ctrl+I style selected text. Ctrl+Enter applies it."))
        self.field = QTextEdit()
        self.field.setAcceptRichText(False)
        self.field.setFont(QFont(FACE,12))
        self.field.setPlainText(obj["text"])
        for a,b,bold,italic in obj["spans"]:
            c = QTextCursor(self.field.document())
            c.setPosition(cp_to_utf16(obj["text"],a))
            c.setPosition(cp_to_utf16(obj["text"],b),QTextCursor.MoveMode.KeepAnchor)
            style = QTextCharFormat()
            style.setFontWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
            style.setFontItalic(italic)
            c.mergeCharFormat(style)
        layout.addWidget(button_row(("Bold  Ctrl+B",lambda:self.style("bold")),("Italic  Ctrl+I",lambda:self.style("italic"))))
        layout.addWidget(self.field)
        layout.addWidget(buttons(self))
        for keys,call in [("Ctrl+B",lambda:self.style("bold")),("Ctrl+I",lambda:self.style("italic")),("Ctrl+Return",self.accept)]:
            a = QAction(self); a.setShortcut(QKeySequence(keys)); a.triggered.connect(call); self.addAction(a)
        self.field.setFocus()

    def style(self,kind):
        fmt = QTextCharFormat()
        if kind=="bold":
            fmt.setFontWeight(QFont.Weight.Normal if self.field.fontWeight()>=QFont.Weight.Bold else QFont.Weight.Bold)
        else:
            fmt.setFontItalic(not self.field.fontItalic())
        self.field.mergeCurrentCharFormat(fmt)
        self.field.setFocus()

    def result_data(self):
        spans, fragments, pos = [], [], 0
        b = self.field.document().begin()
        while b.isValid():
            it = b.begin()
            while not it.atEnd():
                f = it.fragment()
                if f.isValid():
                    txt = f.text()
                    style = f.charFormat()
                    bold = style.fontWeight() >= QFont.Weight.Bold
                    italic = style.fontItalic()
                    if txt and (bold or italic):
                        if spans and spans[-1][1]==pos and spans[-1][2:]==[bold,italic]:
                            spans[-1][1] += len(txt)
                        else:
                            spans.append([pos,pos+len(txt),bold,italic])
                    pos += len(txt); fragments.append(txt)
                it += 1
            b = b.next()
            if b.isValid():
                fragments.append("\n"); pos += 1
        return {"text":"".join(fragments),"spans":spans}


class Studio(QMainWindow):
    def __init__(self,path=None):
        super().__init__()
        self.setObjectName("MARStudio")
        self.resize(1460,920)
        self.setMinimumSize(890,590)
        self.setStyleSheet(STYLE)
        self.project = Project()
        self.pixel_clipboard = PixelClipboard()
        self.path = None
        self.dirty = False
        self.selected = []
        self._updating = False
        self._clipboard_tally = 0
        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
        self.recovery = Path(base)/"recovery"
        self.timer = QTimer(self,singleShot=True,interval=1000)
        self.timer.timeout.connect(self.autosave)
        self.canvas = Canvas(self)
        self.setCentralWidget(self.canvas)
        self.setup_layers()
        self.setup_inspector()
        self.setup_sources()
        self.setup_history()
        self.setup_actions()
        self.zoom_label = QLabel(" ")
        self.statusBar().addPermanentWidget(self.zoom_label)
        self.statusBar().showMessage("Select and drag a layer. Use Panel, Text box, or Place PNG to add one.")
        self.refresh(fit=True)
        if path:
            self.open_path(Path(path))

    def setup_dock(self,title,area,widget):
        d = QDockWidget(title,self)
        d.setObjectName(title)
        d.setWidget(widget)
        self.addDockWidget(area,d)
        return d

    def setup_layers(self):
        w,l = column()
        self.tree = QTreeWidget()
        self.tree.setObjectName("layerTree")
        self.tree.setHeaderLabels(["Layer","Show","Lock"])
        self.tree.setColumnWidth(0,167); self.tree.setColumnWidth(1,41); self.tree.setColumnWidth(2,39)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self.tree_selection)
        self.tree.itemChanged.connect(self.tree_changed)
        self.tree.itemDoubleClicked.connect(lambda item,c:self.edit_content(item.data(0,ROLE)) if c==0 else None)
        l.addWidget(self.tree)
        l.addWidget(button_row(("Group",self.group),("Ungroup",self.ungroup)))
        l.addWidget(hint("Top layers paint last. Toggle visibility and lock beside each layer."))
        self.layers_dock = self.setup_dock("01  Layers",Qt.DockWidgetArea.LeftDockWidgetArea,w)

    def setup_inspector(self):
        w,l = column()
        self.ins_title = hint("Select one layer to inspect its properties.")
        l.addWidget(self.ins_title)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        self.form_body = QWidget()
        self.form = QFormLayout(self.form_body)
        self.form.setContentsMargins(8,10,8,10)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.props = {}
        scroll.setWidget(self.form_body)
        l.addWidget(scroll)
        for k,label in [("name","Name"),("alt","Alt text"),("label","Arrow caption")]:
            q = QLineEdit()
            q.setMaxLength(160 if k!="alt" else 4000)
            q.editingFinished.connect(lambda k=k,q=q:self.change_property(k,q.text()))
            self.form.addRow(label,q); self.props[k]=q
        for k,label in [("source","Provenance"),("edge","Edge category")]:
            q = QComboBox(); q.addItems(SOURCES if k=="source" else EDGE_TYPES)
            q.activated.connect(lambda i,k=k,q=q:self.change_property(k,q.itemText(i)))
            self.form.addRow(label,q); self.props[k]=q
        for k,label,lo,hi,step in [
            ("x","X (mm)",-20000,20000,1),("y","Y (mm)",-20000,20000,1),("w","Width (mm)",1,20000,1),
            ("h","Height (mm)",1,20000,1),("angle","Rotation (°)",-360,360,1),("opacity","Opacity",0,1,.05),
            ("size","Font size (pt)",4,360,.5),("line_height","Line spacing",.70,4,.05),
            ("padding","Padding (mm)",0,300,.5),("radius","Corner (mm)",0,300,.5),("border","Border (mm)",0,100,.1)
        ]:
            q = QDoubleSpinBox(); q.setRange(lo,hi); q.setDecimals(3); q.setSingleStep(step)
            q.setKeyboardTracking(False)
            q.editingFinished.connect(lambda k=k,q=q:self.change_property(k,q.value()))
            self.form.addRow(label,q); self.props[k]=q
        for k,label in [("fill","Fill"),("stroke","Stroke"),("ink","Text ink")]:
            q = QPushButton()
            q.clicked.connect(lambda _=False,k=k:self.change_color(k))
            self.form.addRow(label,q); self.props[k]=q
        self.ins_extra = QPushButton("Edit content…")
        self.ins_extra.clicked.connect(lambda:self.edit_content(self.selected[0]) if len(self.selected)==1 else None)
        l.addWidget(self.ins_extra)
        self.ins_dock = self.setup_dock("02  Inspector",Qt.DockWidgetArea.RightDockWidgetArea,w)
        self.resizeDocks([self.ins_dock],[278],Qt.Orientation.Horizontal)

    def setup_sources(self):
        self.ref_tabs = QTabWidget()
        ref, l = column()
        self.ref_list = QComboBox(); self.ref_list.currentIndexChanged.connect(self.update_source)
        l.addWidget(self.ref_list)
        self.ref_view = QLabel("No references imported.")
        self.ref_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ref_view.setMinimumSize(170,132)
        self.ref_view.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Expanding)
        self.ref_view.setTextFormat(Qt.TextFormat.PlainText)
        self.ref_view.setFrameShape(QFrame.Shape.StyledPanel)
        l.addWidget(self.ref_view,1)
        self.ref_text = QPlainTextEdit(); self.ref_text.setReadOnly(True); self.ref_text.hide()
        l.addWidget(self.ref_text,1)
        self.ref_info = hint("Locked copies · excluded from poster exports")
        self.ref_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        l.addWidget(self.ref_info)
        opacity = QSlider(Qt.Orientation.Horizontal); opacity.setRange(15,100); opacity.setValue(100)
        opacity.setToolTip("Reference display opacity only; this never changes the artwork.")
        l.addWidget(opacity)
        opacity.valueChanged.connect(self.update_source)
        self.ref_opacity = opacity
        l.addWidget(button_row(("Import…",self.import_sources),("Place on canvas…",self.place_image)))
        self.ref_tabs.addTab(ref,"Locked source")
        self.brief = QPlainTextEdit(); self.brief.setReadOnly(True)
        self.ref_tabs.addTab(self.brief,"Writing prompts")
        d = self.setup_dock("03  Reference / brief",Qt.DockWidgetArea.LeftDockWidgetArea,self.ref_tabs)
        self.source_dock = d
        self.splitDockWidget(self.layers_dock,d,Qt.Orientation.Vertical)
        self.resizeDocks([self.layers_dock,d],[270,265],Qt.Orientation.Vertical)
        self.resizeDocks([self.layers_dock],[265],Qt.Orientation.Horizontal)

    def setup_history(self):
        w,l = column(margin=4)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.event_list = QListWidget(); self.event_list.setObjectName("eventList")
        self.event_list.setAlternatingRowColors(True)
        self.event_list.currentRowChanged.connect(self.event_details)
        self.event_json = QPlainTextEdit(); self.event_json.setObjectName("eventDetails"); self.event_json.setReadOnly(True)
        split.addWidget(self.event_list); split.addWidget(self.event_json)
        split.setSizes([490,675]); l.addWidget(split)
        l.addWidget(button_row(("Replay selected…",self.replay),("Compare…",self.compare),("Checkpoint",self.checkpoint),
                               ("Verify",self.verify),("Save external anchor…",self.export_anchor)))
        d = self.setup_dock("04  Edit history / observed timestamps",Qt.DockWidgetArea.BottomDockWidgetArea,w)
        self.history_dock = d
        self.resizeDocks([d],[186],Qt.Orientation.Vertical)

    def action(self, menu, title, call, shortcut=None, check=None, bar=None):
        act = QAction(title,self)
        if shortcut: act.setShortcut(QKeySequence(shortcut))
        if check is None:
            act.triggered.connect(lambda _=False:call())
        else:
            act.setCheckable(True); act.setChecked(check); act.toggled.connect(call)
        if menu: menu.addAction(act)
        if bar: bar.addAction(act)
        if not menu and not bar: self.addAction(act)
        return act

    def setup_actions(self):
        menus = {x:self.menuBar().addMenu(x) for x in ("File","Edit","Arrange","View","Evidence","Help")}
        f,e,a,v,p,h = (menus[k] for k in ("File","Edit","Arrange","View","Evidence","Help"))
        bar = QToolBar("Studio tools",self); bar.setObjectName("studioTools"); bar.setMovable(False)
        self.addToolBar(bar)
        brand = QLabel("MAR / STUDIO"); brand.setObjectName("brand"); bar.addWidget(brand)
        self.action(f,"New project…",self.new,"Ctrl+N")
        self.action(f,"Open .marproj…",self.open,"Ctrl+O",bar=bar)
        self.action(f,"Save",self.save,"Ctrl+S",bar=bar)
        self.action(f,"Save as…",lambda:self.save(True),"Ctrl+Shift+S")
        self.action(f,"Recover local copy…",self.recover)
        f.addSeparator()
        self.action(f,"Import locked references…",self.import_sources,"Ctrl+I")
        self.action(f,"Place PNG on canvas…",self.add_image)
        self.action(f,"Page size and background…",self.page)
        exports = f.addMenu("Export locally")
        for label,kind,dpi in [("Editable SVG…","svg",300),("Print PDF…","pdf",300),
                               ("300 dpi PNG…","png",300),("600 dpi PNG…","png",600),("Comparison contact sheet…","comparison",300)]:
            self.action(exports,label,partial(self.do_export,kind,dpi))
        self.action(f,"Quit",self.close,"Ctrl+Q")
        self.action(e,"Undo",lambda:self.history_step(False),"Ctrl+Z",bar=bar)
        self.action(e,"Redo",lambda:self.history_step(True),"Ctrl+Y",bar=bar)
        self.action(e,"Redo (alternate)",lambda:self.history_step(True),"Ctrl+Shift+Z")
        self.action(e,"Cut",self.cut,"Ctrl+X")
        self.action(e,"Copy",self.copy,"Ctrl+C")
        self.action(e,"Paste",self.paste,"Ctrl+V")
        self.action(e,"Duplicate",self.duplicate,"Ctrl+D")
        self.action(e,"Delete layers",self.delete,"Delete")
        self.action(e,"Select all",lambda:self.set_selection(families(self.project.state).get(None,[])),"Ctrl+A")
        self.action(e,"Edit selected content…",lambda:self.edit_content(self.selected[0]) if len(self.selected)==1 else None,"F2")
        edit_pixels = self.action(e, "Edit Pixels…", self.edit_selected_pixels)
        self.action(e, "Paste Pixels into Selected Layer…", self.paste_selected_pixels)
        self.action(e, "Edit PNG crop…", self.edit_selected_crop)
        self.action(a,"Group layers",self.group,"Ctrl+G")
        self.action(a,"Ungroup layers",self.ungroup,"Ctrl+Shift+G")
        self.action(a,"Move above sibling",lambda:self.order(1),"Ctrl+]")
        self.action(a,"Move below sibling",lambda:self.order(-1),"Ctrl+[")
        align = a.addMenu("Align selection")
        for mode in ("Left","Horizontal centers","Right","Top","Vertical centers","Bottom"):
            self.action(align,mode,partial(self.align,mode))
        self.action(a,"Distribute horizontal spacing",lambda:self.align("Distribute X"))
        self.action(a,"Distribute vertical spacing",lambda:self.align("Distribute Y"))
        self.action(v,"Fit page",self.canvas.fit,"Ctrl+0",bar=bar)
        self.action(v,"Zoom in",lambda:self.canvas.zoom(1.22),"Ctrl++")
        self.action(v,"Zoom out",lambda:self.canvas.zoom(1/1.22),"Ctrl+-")
        self.action(v,"Grid",partial(self.canvas_flag,"grid"),check=True)
        self.action(v,"Snap to grid / guides",partial(self.canvas_flag,"snap"),check=True)
        self.action(v,"Show guides",partial(self.canvas_flag,"guides"),check=True)
        self.action(v,"Clear custom guides",self.clear_guides)
        self.action(v,"Set empty guide layout…",self.choose_scaffold)
        for d in (self.layers_dock,self.ins_dock,self.source_dock,self.history_dock):
            v.addAction(d.toggleViewAction())
        self.action(p,"Project record and disclosures…",self.records,bar=bar)
        self.action(p,"Compare source / canvas…",self.compare)
        self.action(p,"Replay history…",self.replay)
        self.action(p,"Checkpoint",self.checkpoint)
        self.action(p,"Verify project",self.verify)
        self.action(p,"Save external anchor…",self.export_anchor)
        self.action(p,"Export authorship report…",lambda:self.do_export("report",300))
        self.action(p,"Export evidence bundle…",self.do_bundle)
        self.action(p,"Accessibility review…",self.accessibility)
        self.action(h,"Studio keyboard and workflow",self.help)
        self.action(h,"About / limitations",lambda:self.inform("MAR Studio",DISCLAIMER+"\nLocal hashes do not verify identity or time."))

        editing = QToolBar("Editing tools", self)
        editing.setObjectName("editingTools")
        editing.setMovable(False)
        self.addToolBarBreak()
        self.addToolBar(editing)
        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_actions = {}
        more_button = QToolButton(editing)
        more_button.setText("More tools")
        more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more_menu = QMenu(more_button)
        more_button.setMenu(more_menu)
        for index, (kind, name, shortcut) in enumerate(TOOLS):
            action = QAction(name, self.tool_group)
            action.setObjectName(f"tool-{kind}")
            action.setCheckable(True)
            action.setChecked(kind == "select")
            action.triggered.connect(partial(self.select_tool, index))
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
                action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
                action.setToolTip(f"{name} ({shortcut})")
                self.canvas.addAction(action)
            self.tool_actions[kind] = action
            if kind in VISIBLE_TOOLS:
                editing.addAction(action)
                if kind == "select":
                    editing.addAction(edit_pixels)
            else:
                more_menu.addAction(action)
        editing.addWidget(more_button)
        editing.addSeparator()
        self.action(None, "Place PNG…", self.add_image, bar=editing)

    def inform(self,title,text):
        m = QMessageBox(self)
        m.setWindowTitle(title)
        m.setTextFormat(Qt.TextFormat.PlainText)
        m.setText(text)
        m.setIcon(QMessageBox.Icon.Information)
        m.setStandardButtons(QMessageBox.StandardButton.Ok)
        m.exec()

    def error(self,exc):
        self.inform("MAR Studio · operation could not finish",str(exc))

    def select_tool(self, index, _checked=False):
        tool, name, _ = TOOLS[index]
        self.tool_actions[tool].setChecked(True)
        self.canvas.set_tool(tool)
        self.canvas.setFocus()
        if tool == "region":
            message = "Image Region: drag a rectangle inside a placed PNG, then choose Cut Out or Copy Region."
        elif tool == "connector":
            message = "Linked connector: drag from the first object to the second."
        elif tool == "select":
            message = "Click a layer to select it. Drag to move, Shift-click to add to selection, or use the corner handles."
        else:
            message = f"{name}: drag on the canvas to draw."
        self.statusBar().showMessage(message)

    def zoom_status(self):
        if hasattr(self,"zoom_label"):
            self.zoom_label.setText(f"  {self.canvas.transform().m11():.2f} px/mm    ·    coordinates in mm   ")

    def canvas_flag(self,flag,value):
        setattr(self.canvas,flag,value); self.canvas.viewport().update()

    def title(self):
        s = self.project.state
        title = s["meta"]["title"] or "Untitled design"
        self.setWindowTitle(f"{title}{' • template' if s['template'] else ''}{' *' if self.dirty else ''}  |  MAR Studio")

    def refresh(self,fit=False):
        self.selected = [key for key in self.selected if key in self.project.state["objects"]]
        self.title()
        self.canvas.rebuild(fit)
        self.refresh_tree()
        self.refresh_inspector()
        self.refresh_sources()
        self.refresh_history()

    def refresh_tree(self):
        self._updating = True
        try:
            opened = set()
            def gather(node):
                for i in range(node.childCount()):
                    item = node.child(i)
                    if item.isExpanded(): opened.add(item.data(0,ROLE))
                    gather(item)
            gather(self.tree.invisibleRootItem())
            known = getattr(self,"tree_items",{})
            self.tree.clear(); self.tree_items = {}
            state = self.project.state
            children = families(state)
            def add(p,item):
                for key in reversed(children.get(p,[])):
                    o = state["objects"][key]
                    line = QTreeWidgetItem([o["name"] or o["kind"],"",""])
                    line.setData(0,ROLE,key)
                    line.setToolTip(0,f"{o['kind'].title()} · {o['source']}" + (f"\n{o['asset']}" if o["asset"] else ""))
                    line.setFlags(line.flags()|Qt.ItemFlag.ItemIsUserCheckable)
                    line.setCheckState(1,Qt.CheckState.Checked if o["visible"] else Qt.CheckState.Unchecked)
                    line.setCheckState(2,Qt.CheckState.Checked if o["locked"] else Qt.CheckState.Unchecked)
                    item.addChild(line)
                    tag = {"AI-generated":"AI","third-party":"3P","user-declared human":"decl.","mixed":"mixed","template":"template"}.get(o["source"],"?")
                    line.setText(0,f"{line.text(0)}  ‹{tag}›")
                    self.tree_items[key]=line
                    if o["kind"]=="group":
                        add(key,line); line.setExpanded(key in opened or key not in known)
                    line.setSelected(key in self.selected)
            add(None,self.tree.invisibleRootItem())
        finally:
            self._updating = False

    def refresh_inspector(self):
        one = len(self.selected)==1
        s = self.project.state
        key = self.selected[0] if one else None
        o = s["objects"][key] if one else None
        linked = bool(one and o["kind"]=="connector" and (o["start"] or o["end"]))
        self._updating = True
        try:
            self.ins_title.setText(f"{o['kind'].upper()} · {o['source']}" + (" · linked" if linked else "") if one
                                   else f"{len(self.selected)} layers selected" if self.selected else "Select a layer to inspect it.")
            self.ins_extra.setEnabled(bool(one and not is_locked(s,key)))
            self.form_body.setEnabled(bool(one and not is_locked(s,key)))
            if not one:
                return
            kind = o["kind"]
            for k,w in self.props.items():
                visible = (k not in ("label","edge") or kind in ("connector","arrow")) and (
                    k not in ("x","y","w","h","angle","fill","stroke","ink","border","radius","size","padding","line_height") or kind!="group")
                visible = visible and (k not in ("ink","size","line_height") or kind in TEXT_KINDS)
                if k=="edge" and s["topic"]!="hypothesis": visible=False
                if k=="label" and s["topic"]=="hypothesis": visible=False
                self.form.setRowVisible(w,visible)
                w.setEnabled(not ((linked and k in ("x","y","w","h","angle")) or kind=="image" and k=="source"))
                value = (resolved(o,s)[k] if linked and k in ("x","y","w","h","angle") else o[k])
                if isinstance(w,QLineEdit): w.setText(value)
                elif isinstance(w,QComboBox): w.setCurrentText(value)
                elif isinstance(w,QDoubleSpinBox): w.setValue(value)
                else:
                    w.setText(value.upper())
                    w.setStyleSheet(f"border-left: 13px solid {value[:7]};")
            self.ins_extra.setText("Edit text…" if kind in TEXT_KINDS else
                                   "Edit Pixels…" if kind=="image" else "Edit points…" if kind in ("chart","path","polygon","arrow") else
                                   "Edit anchors / points…" if kind=="connector" else "Edit content…")
            self.ins_extra.setEnabled(not is_locked(s,key) and kind in (*TEXT_KINDS,"image","chart","path","polygon","arrow","connector"))
        finally:
            self._updating = False

    def refresh_sources(self):
        current = self.ref_list.currentData()
        self.ref_list.blockSignals(True)
        self.ref_list.clear()
        self.ref_list.addItem("Choose a locked source…",None)
        for sha,a in self.project.state["assets"].items():
            self.ref_list.addItem(a["name"],sha)
        index = self.ref_list.findData(current) if current else -1
        if index < 0 and self.ref_list.count()>1: index=1
        self.ref_list.setCurrentIndex(max(0,index))
        self.ref_list.blockSignals(False)
        self.update_source()
        self.brief.setPlainText(BRIEFS[self.project.state["topic"]])

    def update_source(self,*_):
        from PySide6.QtGui import QImage,QPainter
        from PySide6.QtCore import QSize,QRectF
        key = self.ref_list.currentData()
        self.ref_text.hide(); self.ref_view.show()
        self.ref_view.clear()
        if key is None or key not in self.project.sources:
            self.ref_view.setText("No source selected.")
            self.ref_info.setText("Locked copies · excluded from poster exports")
            return
        a = self.project.state["assets"][key]
        self.ref_info.setText(f"{a['type']} · {a['media'].upper()} · locked\nSHA-256 {key[:18]}…")
        self.ref_info.setToolTip(f"{key}\n{a['credit']}\n{a['license']}\n{a['notes']}")
        if a["media"] in ("md","txt"):
            raw = self.project.sources[key]
            shown = raw[:300_000].decode("utf-8-sig",errors="replace")
            self.ref_text.setPlainText(shown + ("\n\n[Preview truncated; original bytes retained.]" if len(raw)>300_000 else ""))
            self.ref_view.hide(); self.ref_text.show(); return
        if a["media"]!="png":
            self.ref_view.setText("Document preserved as source bytes.\nUse a trusted local reader to inspect it.")
            return
        try:
            img = decode_png(self.project.sources[key],key).scaled(
                QSize(max(150,self.ref_view.width()-10),max(120,min(390,self.ref_view.height()-8))),
                Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
            view = QImage(img.size(),QImage.Format.Format_ARGB32_Premultiplied); view.fill(QColor("#1c3345"))
            with QPainter(view) as p:
                p.setOpacity(self.ref_opacity.value()/100)
                p.drawImage(0,0,img)
            self.ref_view.setPixmap(QPixmap.fromImage(view))
        except Invalid as exc:
            self.ref_view.setText(str(exc))

    def refresh_history(self):
        if len(self.project.events)<self.event_list.count() or getattr(self,"history_project",None)!=self.project:
            self.event_list.clear()
            self.history_project=self.project
        for event in self.project.events[self.event_list.count():]:
            pin = " ◇" if event["snapshot"] else "  "
            self.event_list.addItem(f"{event['seq']:04d}{pin}  {event['action']}    ·    {event['origin']}")
        self.event_list.setCurrentRow(self.event_list.count()-1)
        self.event_list.scrollToBottom()

    def event_details(self, row):
        import json
        if not 0<=row<len(self.project.events): self.event_json.clear(); return
        e = self.project.events[row]
        txt = {k:v for k,v in e.items() if k!="patch"}|{"before_after_patch":e["patch"]}
        self.event_json.setPlainText(json.dumps(txt,ensure_ascii=False,indent=2))

    def set_selection(self,keys):
        self.selected = list(dict.fromkeys(k for k in keys if k in self.project.state["objects"]))
        self._updating=True
        try:
            for k,item in self.tree_items.items(): item.setSelected(k in self.selected)
        finally:
            self._updating=False
        self.refresh_inspector()
        self.canvas.viewport().update()

    def tree_selection(self):
        if not self._updating: self.set_selection([i.data(0,ROLE) for i in self.tree.selectedItems()])

    def tree_changed(self,item,col):
        if self._updating or col not in (1,2): return
        k = item.data(0,ROLE)
        field = "visible" if col==1 else "locked"
        checked = item.checkState(col)==Qt.CheckState.Checked
        self.commit("Set "+field,lambda s:s["objects"][k].update({field:checked}))

    def commit(self,action,mutation,origin="manual_gui",*,undoable=True,selected=None,fit=False,details=None):
        try:
            ev = self.project.edit(action,mutation,origin,undoable=undoable,details=details)
        except (Invalid,ValueError,OSError) as exc:
            self.error(exc); self.refresh(); return False
        if ev:
            if selected is not None: self.selected = selected
            self.changed(fit=fit)
            return True
        if selected is not None: self.set_selection(selected)
        return False

    def changed(self,fit=False):
        self.dirty=True
        self.timer.start()
        self.refresh(fit)

    def draw_layer(self,kind,points,start=None,end=None):
        if len(points)<2: return
        x,y = min(p.x() for p in points),min(p.y() for p in points)
        w,h = max(1,max(p.x() for p in points)-x),max(1,max(p.y() for p in points)-y)
        o = layer(kind,x,y,w,h)
        if kind in ("path","polygon","arrow","connector"):
            if kind=="polygon" and len(points)==2:
                o["points"]=[[.5,0],[1,1],[0,1]]
            else:
                o["points"]=[[round((p.x()-x)/w,5),round((p.y()-y)/h,5)] for p in points]
        if kind=="connector":
            state = self.project.state
            a,b = (key if key and state["objects"][key]["kind"] not in ("connector","group") else None for key in (start,end))
            o.update(start=a,end=b if a!=b else None)
        k = uid()
        self.commit(f"Draw {kind}",lambda s:(s["objects"].update({k:o}),s["order"].append(k)),selected=[k])
        self.select_tool(0)
        if kind=="text": self.edit_content(k)

    def edit_content(self,key):
        state = self.project.state
        if key not in state["objects"] or is_locked(state,key): return
        obj = state["objects"][key]
        if obj["kind"] in TEXT_KINDS:
            dialog = RichDialog(self,obj)
            if dialog.exec():
                self.commit("Edit rich text",lambda s:s["objects"][key].update(dialog.result_data()),selected=[key])
        elif obj["kind"]=="image":
            self.edit_pixels(key)
        elif obj["kind"] in ("path","polygon","arrow","chart","connector"):
            dialog = QDialog(self); dialog.setWindowTitle("Edit vector geometry"); dialog.resize(410,390)
            _,l = column(dialog)
            l.addWidget(hint("One X, Y pair per line, each between 0 and 1 within the layer. For charts, each Y is a bar height."))
            p = QPlainTextEdit(); p.setPlainText("\n".join(f"{x:g}, {y:g}" for x,y in obj["points"]))
            l.addWidget(p)
            ends = {}
            if obj["kind"]=="connector":
                form = QFormLayout(); l.addLayout(form)
                for field in ("start","end"):
                    c = QComboBox(); c.addItem("Free endpoint",None)
                    for k,o in state["objects"].items():
                        if o["kind"] not in ("group","connector"): c.addItem(o["name"]+" · "+k[:5],k)
                    c.setCurrentIndex(max(0,c.findData(obj[field])))
                    form.addRow(field.title(),c); ends[field]=c
            l.addWidget(buttons(dialog))
            if dialog.exec():
                try:
                    values = [[float(x) for x in row.split(",")] for row in p.toPlainText().splitlines() if row.strip()]
                    require(len(values)>=(1 if obj["kind"]=="chart" else 3 if obj["kind"]=="polygon" else 2),
                            "Too few points")
                    self.commit("Edit path or chart points",lambda s:s["objects"][key].update(
                        points=values,**{f:c.currentData() for f,c in ends.items()}))
                except (Invalid,ValueError) as exc: self.error(exc)

    def change_property(self,k,value):
        if self._updating or len(self.selected)!=1: return
        key = self.selected[0]
        if is_locked(self.project.state,key): return
        self.commit("Set "+k,lambda s:s["objects"][key].update({k:value}))

    def change_color(self,k):
        if len(self.selected)==1:
            key = self.selected[0]
            c = color_pick(self,self.project.state["objects"][key][k])
            if c: self.change_property(k,c)

    def chosen_roots(self,state=None):
        state = state or self.project.state
        keys = set(self.selected)
        return [k for k in state["order"] if k in keys and not (keys & set(list(ancestry(state,k))[1:]))]

    def movable_layers(self):
        s = self.project.state
        return [k for k in descendants(s,self.selected,leaf=True) if not is_locked(s,k)
                and (s["objects"][k]["kind"]!="connector" or not (s["objects"][k]["start"] or s["objects"][k]["end"]))]

    def apply_geometry(self,action,changes,origin="manual_gui"):
        if changes:
            self.commit(action,lambda s:[s["objects"][k].update(v) for k,v in changes.items()],origin)

    def nudge(self,dx,dy):
        s = self.project.state
        self.apply_geometry("Nudge selection",{k:{"x":s["objects"][k]["x"]+dx,"y":s["objects"][k]["y"]+dy} for k in self.movable_layers()})

    def delete(self):
        s = self.project.state
        roots = [k for k in self.chosen_roots() if not any(is_locked(s,i) for i in descendants(s,[k]))]
        keys = set(descendants(s,roots))
        if not keys: return
        def change(state):
            state["order"] = [k for k in state["order"] if k not in keys]
            for key in keys: del state["objects"][key]
            for o in state["objects"].values():
                for a in ("start","end"):
                    if o[a] in keys: o[a]=None
        self.commit("Delete layers",change,selected=[])

    def group(self):
        s = self.project.state
        keys = self.chosen_roots()
        if len(keys)<2: return
        parents = {s["objects"][k]["parent"] for k in keys}
        if len(parents)!=1 or any(is_locked(s,i) for i in descendants(s,keys)):
            self.inform("Group layers","Select two or more unlocked sibling layers."); return
        k = uid()
        def change(state):
            index = max(state["order"].index(i) for i in keys)
            state["order"].insert(index+1,k)
            state["objects"][k] = layer("group",name="Group",parent=next(iter(parents)))
            for child in keys: state["objects"][child]["parent"]=k
        self.commit("Group layers",change,selected=[k])

    def ungroup(self):
        s = self.project.state
        ks = [k for k in self.chosen_roots() if s["objects"][k]["kind"]=="group"
              and not any(is_locked(s,i) for i in descendants(s,[k]))]
        if not ks: return
        new = [x for x in s["order"] if s["objects"][x]["parent"] in ks]
        def change(state):
            c = families(state)
            for k in ks:
                group = state["objects"][k]
                children = c.get(k,[])
                for child in children:
                    o = state["objects"][child]; o["parent"]=group["parent"]
                    o["visible"] = o["visible"] and group["visible"]
                    o["opacity"] *= group["opacity"]
                    state["order"].remove(child)
                index = state["order"].index(k)
                state["order"][index:index+1]=children
                del state["objects"][k]
        self.commit("Ungroup layers",change,selected=new)

    def order(self,direction):
        s = self.project.state
        chosen = {k for k in self.chosen_roots() if not is_locked(s,k)}
        def change(state):
            for siblings in families(state).values():
                for j in (range(len(siblings)-2,-1,-1) if direction==1 else range(1,len(siblings))):
                    a,b = siblings[j],siblings[j+direction]
                    if a in chosen and b not in chosen:
                        i1,i2=state["order"].index(a),state["order"].index(b)
                        state["order"][i1],state["order"][i2]=b,a
                        siblings[j],siblings[j+direction]=b,a
        self.commit("Raise layers" if direction==1 else "Lower layers",change)

    def align(self,mode):
        state = self.project.state
        keys = self.movable_layers()
        if len(keys)<(3 if mode.startswith("Distribute") else 2): return
        axis = "x" if mode in ("Left","Right","Horizontal centers","Distribute X") else "y"
        idx = 0 if axis=="x" else 1
        from PySide6.QtGui import QTransform
        from PySide6.QtCore import QRectF
        def edge(k):
            o = state["objects"][k]
            r = QTransform().translate(o["x"],o["y"]).rotate(o["angle"]).mapRect(QRectF(0,0,o["w"],o["h"]))
            return (r.x(),r.width()) if idx==0 else (r.y(),r.height())
        bounds = {k:edge(k) for k in keys}
        lo = min(x for x,w in bounds.values()); hi = max(x+w for x,w in bounds.values())
        changes = {}
        if mode.startswith("Distribute"):
            keys.sort(key=lambda k:bounds[k][0])
            gap = (hi-lo-sum(w for x,w in bounds.values()))/(len(keys)-1)
            target = lo
            for k in keys:
                x,w = bounds[k]; changes[k]={axis:round(state["objects"][k][axis]+target-x,4)}; target+=gap+w
        else:
            for k,(x,w) in bounds.items():
                t = lo if mode in ("Left","Top") else hi-w if mode in ("Right","Bottom") else (lo+hi-w)/2
                changes[k]={axis:round(state["objects"][k][axis]+t-x,4)}
        self.apply_geometry(mode,changes,"noncreative_tool")

    def collected_layers(self):
        s = self.project.state
        keys = descendants(s,self.chosen_roots())
        return {k:deepcopy(s["objects"][k]) for k in keys}

    def copy(self):
        data = self.collected_layers()
        if not data: return
        clip = QMimeData()
        clip.setData(MIME,canonical([[k,v] for k,v in data.items()]))
        QApplication.clipboard().setMimeData(clip)

    def cut(self):
        self.copy(); self.delete()

    def paste(self):
        mime = QApplication.clipboard().mimeData()
        if not mime or not mime.hasFormat(MIME): return
        try:
            raw = parse(bytes(mime.data(MIME)))
            require(type(raw) is list and 0<len(raw)<=3500,"Invalid layer clipboard")
            data = {}
            for key,obj in raw:
                require(identifier(key) and key not in data,"Invalid clipboard layer")
                validate_object(obj); data[key]=obj
            self.paste_objects(data,"Paste layers")
        except (Invalid,ValueError,TypeError) as exc: self.error(exc)

    def duplicate(self):
        data = self.collected_layers()
        if data: self.paste_objects(data,"Duplicate layers")

    def paste_objects(self,source,action):
        state = self.project.state
        ids = {k:uid() for k in source}
        result = {}
        for k,original in source.items():
            o = deepcopy(original)
            if o["asset"]:
                a = state["assets"].get(o["asset"])
                if not a or a["type"]!=o["source"]:
                    self.inform("Image unavailable","Import the exact source with its existing classification before pasting an image."); return
            x,y = o["x"]+5,o["y"]+5
            o.update(x=x,y=y,parent=ids.get(o["parent"]),locked=False)
            for field in ("start","end"): o[field] = ids.get(o[field])
            result[ids[k]]=o
        selected = [k for k,v in result.items() if v["parent"] is None]
        self.commit(action,lambda s:(s["objects"].update(result),s["order"].extend(result)),selected=selected,
                    details={"clipboard":"MAR layer data" if action=="Paste layers" else "local selection",
                             "authorship":"existing material; operation does not establish origin"})

    def add_guide(self,axis,at):
        if 0<=at<=self.project.state["paper"]["w" if axis=="x" else "h"]:
            self.commit("Add alignment guide",lambda s:s["guides"].append([axis,round(at,3)]),"noncreative_tool")

    def clear_guides(self):
        self.commit("Clear custom guides",lambda s:s.update(guides=[]),"noncreative_tool")

    def choose_scaffold(self):
        s = self.project.state
        options = GUIDANCE.get(s["topic"],())
        if not options:
            self.inform("Guide layout","Start an Architecture or Hypothesis project to access its optional guide layouts."); return
        current = s["scaffold"]
        opts = ["Blank (no structure guides)",*options]
        name,ok = QInputDialog.getItem(self,"Optional guide layout","Nonprinting guides only",opts,
                                     opts.index(current) if current else 0,False)
        if ok: self.commit("Choose nonprinting scaffold",lambda d:d.update(scaffold="" if name==opts[0] else name),"noncreative_tool")

    def history_step(self,redo):
        try:
            event = self.project.redo() if redo else self.project.undo()
            if event: self.changed()
        except (Invalid,OSError) as exc: self.error(exc)

    def checkpoint(self):
        try:
            self.project.checkpoint("Mark progress checkpoint"); self.changed()
        except Invalid as exc: self.error(exc)

    def open(self):
        path,_ = QFileDialog.getOpenFileName(self,"Open editable project","","MAR Project (*.marproj)")
        if path and self.can_leave(): self.open_path(Path(path))

    def open_path(self,path):
        try:
            p = Project.load(path)
        except (Invalid,OSError) as exc:
            self.error(exc); return False
        self.switch(p,path)
        return True

    def switch(self,p,path=None,dirty=False):
        self.timer.stop()
        old = self.project
        self.project,self.path,self.selected,self.dirty = p,path,[],dirty
        self.pixel_clipboard.clip = None
        self.tree_items = {}
        old.db.close()
        self.select_tool(0)
        self.refresh(fit=True)
        if dirty: self.timer.start()

    def new(self):
        options = ["Blank general project","MAR architecture","Why I Propose This System"]
        name,ok = QInputDialog.getItem(self,"New blank project","Project brief",options,0,False)
        if ok and self.can_leave():
            topic = ("general","architecture","hypothesis")[options.index(name)]
            self.switch(Project(topic,TITLES[topic]))

    def save(self,ask=False):
        target = self.path
        if ask or not target:
            name,_ = QFileDialog.getSaveFileName(self,"Save editable project",
                                                str(target) if target else "design.marproj","MAR Project (*.marproj)")
            if not name: return False
            target = Path(name)
            if target.suffix.lower()!=".marproj": target=target.with_name(target.name+".marproj")
        try:
            self.project.save(target,preview(self.project.state,self.project.sources))
            self.path,self.dirty = target,False
            self.timer.stop()
            self.recovery_path().unlink(missing_ok=True)
            self.title()
            self.statusBar().showMessage(f"Saved {target.name}",4000)
            return True
        except (Invalid,OSError) as exc:
            self.error(exc); return False

    def can_leave(self):
        if not self.dirty: return True
        opts = QMessageBox.StandardButton
        r = QMessageBox.question(self,"Unsaved project","Save this project before leaving it?",opts.Save|opts.Discard|opts.Cancel,opts.Save)
        if r == opts.Save: return self.save()
        if r == opts.Discard:
            self.timer.stop()
            try: self.recovery_path().unlink(missing_ok=True)
            except OSError: pass
            return True
        return False

    def closeEvent(self,event):
        if self.can_leave(): event.accept()
        else: event.ignore()

    def recovery_path(self):
        return self.recovery/(self.project.state["project"]+".marproj")

    def autosave(self):
        if not self.dirty: return
        try:
            self.recovery.mkdir(parents=True,exist_ok=True)
            self.project.save(self.recovery_path(),preview(self.project.state,self.project.sources,480))
        except (Invalid,OSError) as exc:
            self.statusBar().showMessage(f"Recovery copy could not be saved: {exc}",7000)

    def recover(self):
        if not self.recovery.exists() or not list(self.recovery.glob("*.marproj")):
            self.inform("Local crash recovery","No recovery copies are present."); return
        path,_ = QFileDialog.getOpenFileName(self,"Choose a local recovery copy",str(self.recovery),"MAR Project (*.marproj)")
        if path:
            try: p=Project.load(path)
            except (Invalid,OSError) as exc: self.error(exc); return
            if self.can_leave(): self.switch(p,dirty=True)
            else: p.db.close()

    def page(self):
        dialog = QDialog(self); dialog.setWindowTitle("Page dimensions"); _,l=column(dialog)
        form = QFormLayout(); l.addLayout(form)
        state = self.project.state["paper"]; result = {"background":state["background"]}
        transparent = QCheckBox("Transparent background")
        transparent.setChecked(not rgba(state["background"]).alpha())
        form.addRow(transparent)
        preset = QComboBox(); preset.addItems(["Custom","A4 portrait","A4 landscape","US Letter portrait","US Letter landscape"])
        form.addRow("Size preset",preset)
        dims = []
        for key in ("w","h"):
            spin = QDoubleSpinBox(); spin.setRange(38,1200); spin.setDecimals(2); spin.setValue(state[key]); spin.setSuffix(" mm")
            form.addRow("Width" if key=="w" else "Height",spin); dims.append(spin)
        def set_preset(i):
            if i:
                for spin,value in zip(dims,[(210,297),(297,210),(215.9,279.4),(279.4,215.9)][i-1]): spin.setValue(value)
        preset.currentIndexChanged.connect(set_preset)
        def bg():
            c = color_pick(dialog,result["background"])
            if c:
                result["background"] = c
                transparent.setChecked(not rgba(c).alpha())
        l.addWidget(button_row(("Paper color…",bg))); l.addWidget(buttons(dialog))
        if dialog.exec():
            result.update(w=dims[0].value(),h=dims[1].value())
            if transparent.isChecked():
                result["background"] = "#00000000"
            elif not rgba(result["background"]).alpha():
                result["background"] = "#ffffffff"
            self.commit("Set paper dimensions or background",lambda s:s["paper"].update(result),fit=True)

    def records(self):
        dialog = QDialog(self); dialog.setWindowTitle("Project record and disclosures"); dialog.resize(725,590)
        _,l = column(dialog,13)
        l.addWidget(hint(DISCLAIMER+" Author and operator fields may remain blank. Entries are self-declared."))
        tabs = QTabWidget(); l.addWidget(tabs)
        fields = {}
        parts = {
            "Project":[("title","Project title"),("actor","Operator name (self-declared)"),("credit","Author / owner credit (self-declared)"),
                       ("claim","Scope of claim"),("decisions","Creative decisions in your own words")],
            "AI and other material":[("human_text","Original human text (your declaration)"),("composition","Human composition and alterations"),
                                     ("ai_use","AI-generated material and its reuse"),("third_party","Third-party elements"),("unknown","Unknown origin")],
            "Sources and limits":[("references","Research / external references; distinguish verbatim text"),("licenses","Visual license records"),
                                  ("font_notes","Font rights notes"),("drafts","Drafts and outside corroboration"),("gaps","Unresolved evidence gaps")],
            "Registration worksheet":[("worksheet_created","Confirmed human-created material"),("worksheet_excluded","AI-created and other exclusions")]
        }
        for tab,definitions in parts.items():
            pane,pl=column()
            if tab=="Registration worksheet": pl.addWidget(hint("Optional draft only. The app never files or decides a claim."))
            for key,label in definitions:
                pl.addWidget(QLabel(label))
                if key in ("title","actor","credit"):
                    w = QLineEdit(self.project.state["meta"][key]); w.setMaxLength(8000)
                else:
                    w = QPlainTextEdit(self.project.state["meta"][key]); w.setMinimumHeight(47)
                pl.addWidget(w)
                fields[key]=w
            tabs.addTab(pane,tab)
        l.addWidget(buttons(dialog))
        if dialog.exec():
            values = {k:w.text() if isinstance(w,QLineEdit) else w.toPlainText() for k,w in fields.items()}
            self.commit("Edit self-declared project record",lambda s:s["meta"].update(values))

    def import_sources(self, *, png_only=False):
        if png_only:
            path, _ = QFileDialog.getOpenFileName(self, "Choose a PNG to place", "", "PNG images (*.png)")
            paths = [path] if path else []
        else:
            paths, _ = QFileDialog.getOpenFileNames(
                self, "Choose locked reference files", "", "References (*.png *.md *.txt *.pdf)")
        if not paths: return
        d = QDialog(self); d.setWindowTitle("Declare source information"); d.resize(520,260)
        _,l = column(d)
        l.addWidget(hint(f"Selected {len(paths)} file(s). Use Unknown when unsure. Import the files separately if their declarations differ. Copies stay outside the artboard."))
        f = QFormLayout(); l.addLayout(f)
        kind=QComboBox(); kind.addItems([x for x in SOURCES if x!="template"])
        f.addRow("Declared type",kind)
        credit,lic,notes = QLineEdit(),QLineEdit(),QLineEdit()
        f.addRow("Credit",credit); f.addRow("License",lic); f.addRow("Source notes",notes)
        l.addWidget(buttons(d))
        if not d.exec(): return
        failures, last, count = [], None, len(self.project.events)
        for path in paths:
            try: last = self.project.import_file(path,kind.currentText(),credit.text(),lic.text(),notes.text())
            except (Invalid,OSError) as exc: failures.append(f"{Path(path).name}: {exc}")
        if len(self.project.events)!=count: self.changed()
        if last: self.ref_list.setCurrentIndex(max(0,self.ref_list.findData(last)))
        if failures: self.error("\n".join(failures))

    def add_image(self):
        self.source_dock.show()
        self.ref_tabs.setCurrentIndex(0)
        assets = self.project.state["assets"]
        if assets.get(self.ref_list.currentData(), {}).get("media") != "png":
            self.import_sources(png_only=True)
        if self.project.state["assets"].get(self.ref_list.currentData(), {}).get("media") == "png":
            self.place_image()

    def place_image(self,_=False,*,key=None):
        state = self.project.state
        obj = state["objects"].get(key)
        sha = obj["asset"] if obj else self.ref_list.currentData()
        info = state["assets"].get(sha)
        if not info or info["media"]!="png":
            self.inform("Place image fragment","Choose a locked PNG reference first."); return
        d = QDialog(self)
        d.setWindowTitle("Edit image crop" if obj else "Place PNG on canvas")
        d.resize(530, 290)
        _,l=column(d)
        l.addWidget(hint(f"{info['name']} · {info['type']}\nThis uses part or all of the original in the final artboard. The layer keeps the source classification and hash."))
        f=QFormLayout(); l.addLayout(f)
        coords=[]
        for title,amount in zip(("Crop left %","Crop top %","Crop width %","Crop height %"),obj["crop"] if obj else [0,0,1,1]):
            sp=QDoubleSpinBox(); sp.setRange(0,100); sp.setDecimals(2); sp.setValue(amount*100); f.addRow(title,sp); coords.append(sp)
        controls = buttons(d)
        controls.button(QDialogButtonBox.StandardButton.Ok).setText("Apply crop" if obj else "Place on canvas")
        l.addWidget(controls)
        if not d.exec(): return
        crop=[q.value()/100 for q in coords]
        if obj:
            self.commit("Edit image crop",lambda s:s["objects"][key].update(crop=crop))
        else:
            k=uid()
            aspect=info["height"]*crop[3]/max(1,info["width"]*crop[2])
            o=layer("image",18,24,68,min(150,68*aspect),name=info["name"][:140],
                    asset=sha,source=info["type"],crop=crop,fill="#00000000",border=0)
            if self.commit("Place explicitly selected image fragment",
                           lambda s: (s["objects"].update({k: o}), s["order"].append(k)), selected=[k]):
                self.select_tool(0)

    def extract_image_region(self, key, bounds):
        if min(bounds.width(), bounds.height()) < 1:
            self.statusBar().showMessage("Drag a rectangle at least 1 mm wide and high.", 4000)
            return
        state = self.project.state
        image = state["objects"][key]
        if is_locked(state, key):
            return
        width, height = image["w"], image["h"]
        left, top, right, bottom = bounds.left(), bounds.top(), bounds.right(), bounds.bottom()
        surrounding = [
            QRectF(0, 0, width, top),
            QRectF(0, bottom, width, height - bottom),
            QRectF(0, top, left, bounds.height()),
            QRectF(right, top, width - right, bounds.height()),
        ]
        surrounding = [piece for piece in surrounding if not piece.isEmpty()]

        dialog = QMessageBox(self)
        dialog.setWindowTitle("Image region")
        dialog.setText("Cut out the selected region or copy it to a new layer?")
        linked = any(key in (item["start"], item["end"]) for item in state["objects"].values())
        if linked:
            detail = "Copy the region or detach the connectors before cutting it out."
        elif any(min(piece.width(), piece.height()) < 1 for piece in surrounding):
            detail = "Copy the region or leave at least 1 mm for each remaining strip."
        else:
            detail = "Cut Out keeps the surrounding pieces on the canvas. Copy Region keeps the full image layer."
        dialog.setInformativeText(detail)
        cut = dialog.addButton("Cut Out", QMessageBox.ButtonRole.ActionRole)
        cut.setEnabled(not linked and all(min(piece.width(), piece.height()) >= 1 for piece in surrounding))
        copy = dialog.addButton("Copy Region", QMessageBox.ButtonRole.ActionRole)
        cancel = dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(copy)
        dialog.setEscapeButton(cancel)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked not in (cut, copy):
            return

        clip = image_slice(image, bounds, image["name"][:145] + " · region")
        if clicked is cut:
            pieces = {uid(): image_slice(image, piece, image["name"][:142] + " · remainder")
                      for piece in surrounding}

            def change(document):
                document["objects"].update(pieces)
                document["objects"][key] = clip
                index = document["order"].index(key)
                document["order"][index:index + 1] = [*pieces, key]

            committed = self.commit("Cut out image region", change, selected=[key])
        else:
            clip_key = uid()

            def change(document):
                document["objects"][clip_key] = clip
                document["order"].insert(document["order"].index(key) + 1, clip_key)

            committed = self.commit("Copy image region", change, selected=[clip_key])
        if committed:
            self.select_tool(0)
            self.statusBar().showMessage("Region selected. Drag it to move it.", 4500)

    def selected_editable_image(self):
        if len(self.selected) == 1:
            key = self.selected[0]
            state = self.project.state
            if state["objects"][key]["kind"] == "image" and not is_locked(state, key):
                return key
        self.inform("Edit image", "Select one unlocked PNG layer on the canvas. Use Place PNG if the source is only in the reference panel.")
        return None

    def edit_selected_pixels(self):
        key = self.selected_editable_image()
        if key is not None:
            self.edit_pixels(key)

    def paste_selected_pixels(self):
        if self.pixel_clipboard.clip is None:
            self.inform("Paste pixels", "Open the source image in Edit Pixels, select the area, and click Copy Pixels.")
            return
        key = self.selected_editable_image()
        if key is not None:
            self.edit_pixels(key, paste=True)

    def edit_selected_crop(self):
        key = self.selected_editable_image()
        if key is not None:
            self.place_image(key=key)

    def edit_pixels(self, key, *, paste=False):
        from .photo import encode_pixels, erase_mask, masked_pixels, open_pixels, pasted_sources
        from .photo_ui import PhotoEditor

        obj = self.project.state["objects"][key]
        source_hash = obj["asset"]
        try:
            photo = open_pixels(self.project.sources[source_hash], obj["crop"])
            dialog = PhotoEditor(self, photo, clipboard=self.pixel_clipboard, source_asset=source_hash)
            if paste:
                QTimer.singleShot(0, dialog.start_paste)
            if not dialog.exec() or (dialog.layer_result is None and not dialog.edits.changed()):
                return
            pixels = dialog.edits.image
            turns = sum(operation["tool"] == "Rotate 90° clockwise" for operation in dialog.edits.operations)
            width, height = (obj["h"], obj["w"]) if turns % 2 else (obj["w"], obj["h"])
            frame = dict(obj, w=width, h=height, crop=[0, 0, 1, 1])
            details = {"from_asset": source_hash, "operations": dialog.edits.operations}
            keep_base = dialog.layer_result is None or dialog.edits.changed()
            new_layer = None
            action = "Edit image pixels"
            if dialog.layer_result is not None:
                cut, fragment, bounds = dialog.layer_result
                left, top, right, bottom = bounds
                sx, sy = width / pixels.width, height / pixels.height
                area = QRectF(left * sx, top * sy, (right - left) * sx, (bottom - top) * sy)
                if min(area.width(), area.height()) < 1:
                    # Preserve the scale of very small selections within the layer size limit.
                    fragment = masked_pixels(pixels, dialog.mask)
                    area = QRectF(0, 0, width, height)
                new_layer = image_slice(frame, area, obj["name"][:145] + " · selection")
                new_layer.update(fill="#00000000", crop=[0, 0, 1, 1])
                action = "Cut selection to new layer" if cut else "Copy selection to new layer"
                details = dialog.with_selection(details)
                if cut:
                    keep_base = True
                    pixels = erase_mask(pixels, dialog.mask)
                    frame["fill"] = "#00000000"

            previous_length = len(self.project.events)
            data = [encode_pixels(pixels)] if keep_base else []
            if new_layer is not None:
                data.append(encode_pixels(fragment))
            outputs = iter(self.project.retain_edited_pngs(
                source_hash, data, additional_sources=pasted_sources(dialog.edits.operations)))
            if keep_base:
                frame["asset"] = next(outputs)
                frame["source"] = self.project.state["assets"][frame["asset"]]["type"]
                details["to_asset"] = frame["asset"]
            selected = key
            if new_layer is not None:
                selected = uid()
                new_layer["asset"] = next(outputs)
                new_layer["source"] = self.project.state["assets"][new_layer["asset"]]["type"]
                details["new_layer_asset"] = new_layer["asset"]

            def change(document):
                if keep_base:
                    document["objects"][key] = frame
                if new_layer is not None:
                    document["objects"][selected] = new_layer
                    document["order"].insert(document["order"].index(key) + 1, selected)

            if self.commit(action, change, selected=[selected], details=details):
                if new_layer is not None:
                    self.select_tool(0)
                    self.statusBar().showMessage("New layer selected. Drag the cutout to move it.", 5000)
            elif len(self.project.events) > previous_length:
                self.changed()
        except (Invalid, OSError, ValueError, MemoryError) as exc:
            self.error(exc)

    def replay(self):
        dialog = QDialog(self); dialog.setWindowTitle("Replay verified state transitions"); dialog.resize(800,690)
        _, l = column(dialog)
        pic=QLabel(); pic.setAlignment(Qt.AlignmentFlag.AlignCenter); l.addWidget(pic,1)
        caption=hint(""); l.addWidget(caption)
        slide=QSlider(Qt.Orientation.Horizontal); slide.setRange(1,len(self.project.events))
        slide.setValue(max(1,self.event_list.currentRow()+1)); l.addWidget(slide)
        t=QTimer(dialog,interval=380)
        def frame(seq):
            try:
                raw=preview(self.project.state_at(seq),self.project.sources,520)
                pix=QPixmap(); pix.loadFromData(raw); pic.setPixmap(pix)
                e=self.project.events[seq-1]
                caption.setText(f"{seq}/{len(self.project.events)}  {e['action']}  ·  {e['origin']}  ·  {e['utc']}")
            except Invalid as exc: caption.setText(str(exc))
        def next_():
            if slide.value()==slide.maximum(): t.stop()
            else: slide.setValue(slide.value()+1)
        def play():
            if slide.value()==slide.maximum(): slide.setValue(1)
            t.start()
        t.timeout.connect(next_); slide.valueChanged.connect(frame)
        l.addWidget(button_row(("Play",play),("Pause",t.stop),("Close",dialog.accept)))
        frame(slide.value())
        dialog.exec(); t.stop()

    def compare(self):
        dialog=QDialog(self); dialog.setWindowTitle("Source, first canvas, current canvas and GUI-touched layers")
        dialog.resize(1100,650)
        _,l=column(dialog); host=QScrollArea(); pic=QLabel()
        try:
            data=comparison(self.project,self.ref_list.currentData())
            pix=QPixmap(); pix.loadFromData(data); pic.setPixmap(pix)
        except Invalid as exc: self.error(exc); return
        host.setWidget(pic); l.addWidget(host)
        l.addWidget(hint("Pink outlines record GUI touches. They do not establish who created the material. The locked source occupies a separate column."))
        l.addWidget(button_row(("Close",dialog.accept))); dialog.exec()

    def verify(self):
        try:
            status=verified_status(self.project)
            self.inform("Internal verification passed",f"{status['events']} events · {status['snapshots']} snapshots · {status['sources']} sources\n"
                        f"Head SHA-256: {status['anchor']['head']}\n\n{status['limits']}")
        except (Invalid,OSError) as exc: self.error(exc)

    def export_anchor(self):
        name,_ = QFileDialog.getSaveFileName(self,"Write anchor for optional independent timestamping","mar-anchor.json","JSON (*.json)")
        if name:
            try: atomic_write(name,canonical(self.project.anchor())+b"\n")
            except OSError as exc: self.error(exc)
            else: self.statusBar().showMessage("Anchor saved locally. Keep it independently if used for corroboration.",5000)

    def disclosure(self,kind):
        s = self.project.state
        only_evidence = kind in ("report","bundle")
        d=QDialog(self); d.setWindowTitle("Review export disclosure"); d.resize(590,360)
        _,l=column(d,12)
        placed=[(k,o) for k,o,_ in front_layers(s) if o["kind"]=="image"]
        counts=Counter(o["source"] for _,o in placed)
        text=("Poster export: visible layers, including explicitly placed image fragments; the reference panel, "
              "operator name, metadata fields, and audit log are omitted. SVG contains alt text and editable text; "
              "PDF software may add a creation timestamp.")
        if kind=="comparison":
            text="The contact sheet contains the selected locked PNG (if any), first state, current design, and GUI-touched layer view."
        elif only_evidence:
            text=("The report includes operator credit, notes, timestamps, filenames, image thumbnails, and relevant hashes. "
                  + ("This bundle also includes exact original bytes and the full project history (including undone text). "
                     "Original files may contain their own metadata." if kind=="bundle" else
                     "The complete log stays in the project and bundle."))
        l.addWidget(hint(text+"\n\n"+DISCLAIMER))
        l.addWidget(hint(f"Visible placed images: {len(placed)}" + (" · "+", ".join(f"{n} {k}" for k,n in counts.items()) if counts else "")))
        if placed:
            listing=QPlainTextEdit(); listing.setReadOnly(True)
            listing.setPlainText("\n".join(f"{s['assets'][o['asset']]['name']} · {o['source']} · {o['asset']}" for _,o in placed)); l.addWidget(listing)
        if only_evidence:
            listing=QPlainTextEdit(); listing.setReadOnly(True)
            listing.setPlainText(f"Operator: {s['meta']['actor'] or '(blank)'}\nCredit: {s['meta']['credit'] or '(blank)'}\n"
                                 +"\n".join(f"{a['name']} · {a['type']}" for a in s["assets"].values()))
            l.addWidget(listing)
        worksheet=QCheckBox("Include operator-entered registration worksheet")
        if only_evidence: l.addWidget(worksheet)
        l.addWidget(buttons(d))
        return bool(d.exec()),worksheet.isChecked()

    def do_export(self,kind,dpi):
        ok,worksheet=self.disclosure(kind)
        if not ok: return
        ext={"comparison":"png","report":"pdf"}.get(kind,kind)
        stem={"comparison":"comparison","report":"mar-authorship-record"}.get(kind,"poster")
        name,_=QFileDialog.getSaveFileName(self,"Export locally",f"{stem}.{ext}",f"{ext.upper()} (*.{ext})")
        if not name: return
        if not name.lower().endswith("."+ext): name+="."+ext
        self.run_output(lambda:export_file(self.project,name,kind,dpi=dpi,source=self.ref_list.currentData(),worksheet=worksheet))

    def do_bundle(self):
        ok,worksheet=self.disclosure("bundle")
        if not ok: return
        name,_=QFileDialog.getSaveFileName(self,"Export evidence bundle","evidence_bundle.zip","ZIP (*.zip)")
        if not name: return
        if not name.lower().endswith(".zip"): name+=".zip"
        self.run_output(lambda:bundle(self.project,name,worksheet=worksheet)[0])

    def run_output(self,fn):
        seq=len(self.project.events)
        try:
            hash_=fn()
            self.statusBar().showMessage(f"Export saved · SHA-256 {hash_[:20]}…",8500)
        except (Invalid,OSError,ValueError,RuntimeError) as exc: self.error(exc)
        finally:
            if len(self.project.events)!=seq: self.changed()

    def accessibility(self):
        s=self.project.state; issues=[]
        for key,o,_ in front_layers(s):
            if o["kind"] in ("image","chart","icon") and not o["alt"].strip():
                issues.append(f"{o['name']}: add an alt description.")
            if o["text"]:
                if o["size"]<10: issues.append(f"{o['name']}: small text ({o['size']} pt).")
                from .render import text_lines
                emitted="".join(seg for _,runs in text_lines(o) for _,seg,_,_ in runs)
                if len("".join(emitted.split()))<len("".join(o["text"].split())):
                    issues.append(f"{o['name']}: text extends past its frame.")
        self.inform("Basic accessibility review","\n".join(issues[:40]) if issues else "No missing image/chart/icon alt text, small text, or clipped text detected. Check reading order and contrast manually.")

    def help(self):
        self.inform("Studio workflow", "1. Choose Text box or Panel and drag on the canvas. Use Place PNG to add an image layer.\n"
                    "2. Use Select / Move to drag layers. Shift-click adds to the selection. Double-click a text layer to edit it.\n"
                    "3. Double-click a PNG to paint, erase, clone, or adjust pixels. Image Region separates movable rectangular pieces.\n"
                    "4. Hold Alt to bypass snapping. Arrow keys nudge 1 mm. Ctrl+wheel zooms and Space+drag pans.\n"
                    "5. Save the editable project, then export a poster or verify a separate evidence bundle.\n\n"+DISCLAIMER)

