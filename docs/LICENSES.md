# Asset and dependency inventory

The [MIT License](../LICENSE) covers the original project software. The original README and documentation prose is also offered under [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/). Credit MAR Studio contributors when using that option.

| Material | Origin and terms |
| --- | --- |
| Bundled DejaVu Sans 2.37, regular/bold/oblique/bold oblique | [Official DejaVu 2.37 release](https://dejavu-fonts.github.io/Download.html). Exact upstream license in marstudio/fonts/LICENSE. Bitstream Vera terms with public-domain DejaVu changes; used as shipped. Original upstream ZIP SHA-256: 7576310b219e04159d35ff61dd4a4ec4cdba4f35c00e002a136f00e96a908b0a. |
| Icons and geometry | Basic shapes and the check icon are draw commands in the project source. No third-party icon packs. |
| Demo screenshot, generated templates, and generated evidence bundle | The screenshot shows the app displaying the synthetic fixture from `scripts/make_examples.py`. The generated blank templates contain writing prompts and empty artboards. The evidence bundle contains a software-drawn test page and a synthetic QA PNG. |
| User imports | No app-granted license or inferred authorship. Record the source and permission you actually have. Originals in an evidence bundle retain their own metadata. |
| PySide6 Essentials, shiboken6, Qt | Qt for Python use under applicable LGPLv3/GPL/commercial terms; see [Qt for Python licensing](https://doc.qt.io/qtforpython-6/licenses.html) and [Qt licensing information](https://www.qt.io/licensing/open-source-lgpl-obligations). Binary modules/DLLs are kept separate in the onedir build. Corresponding [Qt source](https://download.qt.io/archive/qt/6.10/6.10.2/submodules/) and [Qt for Python source](https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.10.2). |
| Python, SQLite, ReportLab, Pillow, charset-normalizer | Python PSF; SQLite public domain; ReportLab BSD; Pillow MIT-CMU; charset-normalizer MIT. Upstream wheel metadata/license files are copied into the build. |
| PyInstaller, hooks, pypdf | PyInstaller GPLv2 or later with its distribution exception; community standard hooks GPLv2 or later and runtime hooks Apache-2.0; pypdf BSD-3-Clause. pypdf is only used by tests. |

This is an inventory, not a license opinion. Review applicable Qt obligations and wheel notices before public distribution of a binary. The repo and example output make no representation about the rights in externally imported images, writing, trademarks, or templates.

