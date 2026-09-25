# PyInstaller spec for PolySub.app (run packaging/macos/build.sh)
import os
from PyInstaller.utils.hooks import collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
ns = {}
exec(open(os.path.join(ROOT, "src", "polysub", "__init__.py")).read(), ns)
VERSION = ns["__version__"]

# Qt modules PolySub does not use (keeps the bundle small)
QT_EXCLUDES = [f"PySide6.{m}" for m in (
    "QtNetwork", "QtQml", "QtQuick", "QtQuickWidgets", "QtPdf", "QtPdfWidgets", "QtSvg", "QtSvgWidgets",
    "QtOpenGL", "QtOpenGLWidgets", "QtSql", "QtTest", "QtXml", "QtConcurrent", "QtDBus", "QtHelp",
    "QtPrintSupport", "QtDesigner", "QtUiTools", "QtMultimedia", "QtWebEngineCore", "QtCharts")]

# built-in engine servers from packaging/engines/fetch.sh (the app still works without them,
# using oMLX or cloud endpoints)
ENGINES = os.path.join(ROOT, "build", "engines", "bin")
engine_bins = [(os.path.join(ENGINES, n), "engines") for n in ("whisper-server", "llama-server")
               if os.path.isfile(os.path.join(ENGINES, n))]
if len(engine_bins) < 2:
    print("WARNING: built-in engine servers missing; run packaging/engines/fetch.sh")

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=engine_bins,
    datas=[(os.path.join(ROOT, "src", "polysub", "assets"), "polysub/assets")] + collect_data_files("opencc"),
    hiddenimports=["polysub.gui.app", "polysub.gui.environment", "polysub.gui.endpoints", "polysub.gui.editor",
                   "polysub.engine.runtime", "polysub.engine.builtin"],
    excludes=QT_EXCLUDES + ["tkinter", "unittest", "pydoc", "IPython", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PolySub", console=False,
          argv_emulation=False, target_arch="arm64", codesign_identity=None)
coll = COLLECT(exe, a.binaries, a.datas, name="PolySub", strip=False, upx=False)
app = BUNDLE(
    coll, name="PolySub.app", icon=os.path.join(ROOT, "packaging", "macos", "PolySub.icns"),
    bundle_identifier="app.polysub.PolySub", version=VERSION,
    info_plist={
        "CFBundleName": "PolySub",
        "CFBundleDisplayName": "PolySub",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "PolySub",
        # accept videos / audio / folders dropped on the Dock icon
        "CFBundleDocumentTypes": [{
            "CFBundleTypeName": "Media",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Alternate",
            "LSItemContentTypes": ["public.movie", "public.audiovisual-content", "public.audio", "public.folder"],
        }],
    },
)
