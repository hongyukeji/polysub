# PyInstaller spec: builds dist/PolySub.app (run packaging/macos/build.sh)
import os
from PyInstaller.utils.hooks import collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
ns = {}
exec(open(os.path.join(ROOT, "polysub", "__init__.py")).read(), ns)
VERSION = ns["__version__"]

# Qt modules PolySub does not use (keeps the bundle small)
QT_EXCLUDES = [f"PySide6.{m}" for m in (
    "QtNetwork", "QtQml", "QtQuick", "QtQuickWidgets", "QtPdf", "QtPdfWidgets", "QtSvg", "QtSvgWidgets",
    "QtOpenGL", "QtOpenGLWidgets", "QtSql", "QtTest", "QtXml", "QtConcurrent", "QtDBus", "QtHelp",
    "QtPrintSupport", "QtDesigner", "QtUiTools", "QtMultimedia", "QtWebEngineCore", "QtCharts")]

a = Analysis(
    [os.path.join(ROOT, "packaging", "entry.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "polysub", "assets"), "polysub/assets")] + collect_data_files("opencc"),
    hiddenimports=["polysub.gui.app", "polysub.gui.doctor", "polysub.gui.editor"],
    excludes=QT_EXCLUDES + ["tkinter", "unittest", "pydoc", "IPython", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PolySub", console=False,
          argv_emulation=False, target_arch="arm64", codesign_identity=None)
coll = COLLECT(exe, a.binaries, a.datas, name="PolySub", strip=False, upx=False)
app = BUNDLE(
    coll, name="PolySub.app", icon=os.path.join(SPECPATH, "PolySub.icns"),
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
