# Validation and packaging

## Reproduce the tests

On Windows, set up the environment first.

```powershell
.\startup.bat --install
.\.venv\Scripts\python.exe -m pip install -r requirements-test.lock
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

On Linux, use the environment from the [installation guide](../README.md#linux-x86-64).

```sh
.venv/bin/python -m pip install -r requirements-test.lock
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

The suite uses synthetic PNGs and scripted GUI events. It covers layer selection and geometry, rich text, snapping, grouping, alpha-preserving cut and paste, floating paste transforms, retouching, save and reopen, undo/redo, SVG and PDF text, print dimensions, recovery from an interrupted write, archive verification, and rejection of malformed project inputs. GitHub Actions runs it on Windows and Ubuntu.

## Reproduce the demo and verify a bundle

With the environment's Python, run the examples script, then verify the resulting files.

```sh
python scripts/make_examples.py
python -m marstudio.integrity examples/MAR-Architecture-Blank.marproj
python -m marstudio.integrity examples/Why-I-Propose-This-System-Blank.marproj
python -m marstudio.integrity output/example-evidence-bundle.zip
```

The script generates fresh project IDs and records the local clock, so digest values vary. To compare a file to an independently retained digest, add `--expected-head` followed by that digest. The screenshot in the README shows the software-generated fixture from the bundle. The fixture records automated steps.

## Build a Windows executable

Run on Windows 11 x64 with the local environment ready.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-windows.ps1
.\dist\MARStudio\MARStudio.exe
```

Keep the entire `dist/MARStudio` directory together. The build is an unsigned portable folder running on the same architecture. Python is bundled. Windows reputation prompts may appear. If Windows cannot load the DLLs, check the architecture and the Microsoft Visual C++ 2015–2022 redistributable.

Run a startup and evidence smoke check against a generated example project.

```powershell
.\.venv\Scripts\python.exe scripts\make_examples.py
New-Item -ItemType Directory -Force tmp | Out-Null
Start-Process -FilePath .\dist\MARStudio\MARStudio.exe -ArgumentList @(
    'examples\MAR-Architecture-Blank.marproj', '--startup-check',
    'tmp\startup-windows.json', '--check-evidence'
) -Wait
Get-Content tmp\startup-windows.json
```

The probe renders the window, checks the packaged export and verifier in a temporary directory, and writes a JSON result and a screenshot. To check the source launcher, pass the same arguments to `python run_studio.py`.

The lock files pin dependency versions. For an offline build, download wheels for the target platform ahead of time and install them with pip `--no-index --find-links`. The build recipe uses a native Windows toolchain. See the [third-party notices](LICENSES.md) before redistributing a package that includes Qt and other dependencies.

## Current limits

- Vector text uses the bundled DejaVu Sans family. Exported SVG text remains editable in runs, and layout metrics may vary in external editors. Paths use polylines and polygons. Linked connectors remain live in native projects, with SVG exporting their rendered geometry.
- Groups share stacking, visibility, opacity, and lock controls. Resize and rotation operate on individual layer geometry.
- The basic contrast review can help check a design. PDF exports are untagged. Scenario templates include their own unproven-case labels.
- The project history records tool operations, edits, and source declarations. The verifier checks continuity and file integrity. Device times and operator names are self-reported data. See the [format and privacy notes](FORMAT.md).
