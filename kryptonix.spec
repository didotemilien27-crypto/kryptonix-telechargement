# -*- mode: python ; coding: utf-8 -*-
# Spécification PyInstaller de KRYPTONIX. Construire avec :  pyinstaller kryptonix.spec
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [
    ("interfaces/templates", "interfaces/templates"),
    ("ressources", "ressources"),
    ("config.json", "."),
]
binaries = []
hiddenimports = []

# Bibliothèques qui embarquent des données/binaires (voix, ffmpeg, reconnaissance, fenêtre...)
for paquet in ("edge_tts", "webview", "speech_recognition", "imageio_ffmpeg", "imageio",
               "pyttsx3", "fpdf", "docx", "openpyxl", "pypdf", "gtts", "pygame", "sympy",
               "certifi"):
    try:
        d, b, h = collect_all(paquet)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass

hiddenimports += collect_submodules("pyttsx3.drivers")
hiddenimports += ["comtypes", "win32com", "pythoncom", "PIL.Image", "psutil"]

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "customtkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="KRYPTONIX",
    console=False,                 # AUCUN terminal
    icon="ressources/kryptonix.ico",
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="KRYPTONIX", upx=False)
