"""Launch the desktop app; --startup-check is a packaging smoke test."""
import argparse
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QTimer, qVersion
from PySide6.QtWidgets import QApplication

from marstudio import __version__
from marstudio.integrity import canonical
from marstudio.project import atomic_write
from marstudio.render import register_fonts
from marstudio.ui import Studio


def main():
    parser = argparse.ArgumentParser(description="MAR Studio — local vector editor and provenance record")
    parser.add_argument("project", type=Path, nargs="?")
    parser.add_argument("--startup-check", type=Path, metavar="RESULT_JSON",
                        help="Show and render the desktop UI, write a smoke result, then exit.")
    parser.add_argument("--check-evidence",action="store_true",
                        help="With --startup-check, exercise exports in a temporary directory.")
    args = parser.parse_args()
    if args.check_evidence and not args.startup_check:
        parser.error("--check-evidence requires --startup-check")
    app = QApplication([sys.argv[0]])
    app.setApplicationName("MAR Studio")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("MARStudio")
    app.setStyle("Fusion")
    register_fonts()
    studio = Studio()
    if args.project and not studio.open_path(args.project):
        return 1
    studio.show()
    QTimer.singleShot(0,studio.canvas.fit)
    if args.startup_check:
        def check():
            shot = studio.grab()
            results = {"app":__version__,"qt":qVersion(),"platform":app.platformName(),
                       "window":studio.windowTitle(),"visible":studio.isVisible(),
                       "canvas_px":[studio.canvas.viewport().width(),studio.canvas.viewport().height()],
                       "opened_project":bool(args.project and studio.path),
                       "events":len(studio.project.events)}
            code = 0 if results["visible"] and min(results["canvas_px"])>30 and not shot.isNull() else 1
            if args.check_evidence:
                from marstudio.evidence import bundle, export_file
                from marstudio.integrity import verify_path
                try:
                    with tempfile.TemporaryDirectory() as tmp:
                        base = Path(tmp)
                        export_file(studio.project,base/"startup.svg","svg")
                        b = base/"evidence.zip"
                        bundle(studio.project,b,example=True)
                        results["evidence"] = verify_path(b)["status"]
                except Exception as exc:
                    results["evidence_error"] = f"{type(exc).__name__}: {exc}"
                    code = 1
            atomic_write(args.startup_check, canonical(results)+b"\n")
            shot.save(str(args.startup_check.with_suffix(".png")),"PNG")
            app.exit(code)
        QTimer.singleShot(500,check)
    return app.exec()


if __name__=="__main__":
    raise SystemExit(main())

