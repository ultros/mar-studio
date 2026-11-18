"""Check the Windows source launcher's pinned packages without invoking pip."""

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


RUNTIME_IMPORTS = (
    "PySide6.QtCore",
    "PySide6.QtWidgets",
    "shiboken6",
    "reportlab.pdfbase.ttfonts",
    "PIL.Image",
    "charset_normalizer",
)


def main():
    lockfile = Path(__file__).resolve().parents[1] / "requirements.lock"
    try:
        pins = []
        for line in lockfile.read_text(encoding="utf-8").splitlines():
            requirement = line.partition("#")[0].strip()
            if not requirement:
                continue
            name, separator, wanted = requirement.partition("==")
            if not (name.strip() and separator and wanted.strip()):
                raise ValueError(f"expected an exact package pin, found {requirement!r}")
            pins.append((name.strip(), wanted.strip()))
        if not pins:
            raise ValueError("no package pins found")
    except (OSError, ValueError) as exc:
        print(f"Could not read {lockfile.name}: {exc}")
        return 3

    mismatches = []
    for name, wanted in pins:
        try:
            installed = version(name)
        except PackageNotFoundError:
            mismatches.append(f"  {name} {wanted} is missing")
        else:
            if installed != wanted:
                mismatches.append(f"  {name} {installed} is installed, with {wanted} required")
    if mismatches:
        print("MAR Studio dependencies need setup.")
        print("\n".join(mismatches))
        return 1

    for module in RUNTIME_IMPORTS:
        try:
            import_module(module)
        except Exception as exc:
            print(f"The pinned packages are installed, but {module} failed to load ({exc}).")
            return 2

    print("MAR Studio dependencies are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
