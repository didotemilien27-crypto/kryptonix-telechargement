@echo off
setlocal enableextensions
title KRYPTONIX - Lancement (test)
cd /d "%~dp0"

echo ==========================================================
echo   KRYPTONIX - lancement en mode test
echo ==========================================================
echo.

rem ---------- 1. Trouver Python (3.12 de preference) ----------
set "PY="
py -3.12 --version >nul 2>&1 && set "PY=py -3.12"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [X] Python est introuvable.
    echo     Installe Python 3.12 : https://www.python.org/downloads/
    echo     et COCHE "Add python.exe to PATH" pendant l'installation.
    goto :erreur
)
for /f "tokens=2" %%v in ('%PY% --version 2^>^&1') do set "PYVER=%%v"
echo Python %PYVER% detecte.
echo %PYVER% | findstr /b /c:"3.10" /c:"3.11" /c:"3.12" >nul
if errorlevel 1 (
    echo [!] Python 3.12 est recommande : avec une autre version, certaines
    echo     librairies peuvent ne pas s'installer. On essaie quand meme.
)
echo.

rem ---------- 2. Environnement Python isole (cree une seule fois) ----------
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creation de l'environnement Python ^(une seule fois^)...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [X] Impossible de creer l'environnement Python.
        goto :erreur
    )
) else (
    echo [1/3] Environnement Python deja present.
)
set "VPY=.venv\Scripts\python.exe"

rem ---------- 3. Librairies (reinstallees seulement si requirements.txt a change) ----------
fc /b requirements.txt ".venv\requirements.installed" >nul 2>&1
if errorlevel 1 (
    echo [2/3] Installation des librairies ^(2 a 5 minutes la premiere fois^)...
    "%VPY%" -m pip install --upgrade pip --quiet
    "%VPY%" -m pip install -r requirements.txt --quiet
    if errorlevel 1 (
        echo.
        echo [!] Une librairie a echoue ^(souvent PyAudio : seul le micro sera desactive^).
        echo     Nouvelle tentative sans PyAudio...
        findstr /v /i "PyAudio" requirements.txt > ".venv\requirements.sans_pyaudio.txt"
        "%VPY%" -m pip install -r ".venv\requirements.sans_pyaudio.txt" --quiet
        if errorlevel 1 (
            echo [X] Installation impossible. Verifie ta connexion internet.
            goto :erreur
        )
    )
    copy /y requirements.txt ".venv\requirements.installed" >nul
) else (
    echo [2/3] Librairies deja installees.
)

rem ---------- 4. Lancement ----------
echo [3/3] Lancement de KRYPTONIX...
echo.
echo   Cette fenetre affiche les messages techniques : garde-la ouverte.
echo   Si quelque chose ne marche pas, copie ce qui s'y affiche.
echo   Ferme la fenetre KRYPTONIX pour tout arreter.
echo.
"%VPY%" app.py
if errorlevel 1 goto :erreur
goto :fin

:erreur
echo.
echo ==========================================================
echo   Un probleme est survenu. Copie le texte ci-dessus
echo   (ou fais une capture) et envoie-le moi.
echo ==========================================================
pause
exit /b 1

:fin
endlocal
