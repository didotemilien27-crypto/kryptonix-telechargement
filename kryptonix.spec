# -*- mode: python ; coding: utf-8 -*-
# Spécification PyInstaller de KRYPTONIX (construite par GitHub Actions : pyinstaller kryptonix.spec)
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [
    ("interfaces/templates", "interfaces/templates"),
    ("ressources", "ressources"),
    ("config.json", "."),
]
binaries = []
hiddenimports = []

# Bibliothèques qui embarquent des données/binaires (voix, ffmpeg, reconnaissance, fenêtre...)
for paquet in ("edge_tts", "aiohttp", "webview", "speech_recognition", "imageio_ffmpeg", "imageio",
               "pyttsx3", "fpdf", "docx", "openpyxl", "pypdf", "pygame", "sympy", "certifi",
               # transcription locale des messages vocaux et vidéos (le modèle, lui, se télécharge à l'usage)
               "faster_whisper", "ctranslate2", "av", "onnxruntime", "tokenizers", "huggingface_hub"):
    try:
        d, b, h = collect_all(paquet)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass

hiddenimports += collect_submodules("pyttsx3.drivers")
hiddenimports += collect_submodules("noyau.extensions")        # modules « Jarvis » chargés dynamiquement
hiddenimports += ["interfaces.routes_extensions"]
hiddenimports += ["comtypes", "win32com", "pythoncom", "PIL.Image", "psutil"]

# Le bac à sable relance l'application elle-même pour exécuter le code Python de l'utilisateur
# (app.py --kx-python). Ce code doit pouvoir importer toute la bibliothèque standard utile :
# sans cette liste, PyInstaller n'embarque que les modules qu'il voit utilisés dans l'appli.
hiddenimports += [
    "runpy", "builtins", "math", "cmath", "random", "statistics", "itertools", "functools",
    "operator", "collections", "heapq", "bisect", "array", "string", "textwrap", "re",
    "json", "csv", "datetime", "time", "calendar", "fractions", "decimal", "numbers",
    "copy", "pprint", "typing", "dataclasses", "enum", "abc", "contextlib", "types",
    "hashlib", "base64", "binascii", "uuid", "secrets", "struct", "io", "unicodedata",
    "difflib", "queue", "threading", "zlib", "html", "graphlib", "weakref", "traceback",
]

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
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
