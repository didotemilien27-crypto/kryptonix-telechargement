@echo off
chcp 65001 >nul
title KRYPTONIX - Construction de l'installateur
cd /d "%~dp0"

echo ==========================================================
echo   KRYPTONIX - construction de Kryptonix-Setup.exe
echo   (a lancer sur un PC Windows, une seule fois par version)
echo ==========================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [X] Python 3.12 est introuvable. Installe-le depuis python.org
    echo     en cochant "Add python.exe to PATH", puis relance ce fichier.
    pause & exit /b 1
)

echo [1/4] Environnement Python...
if not exist ".venv\Scripts\python.exe" python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt pyinstaller --quiet
if errorlevel 1 (
    echo [!] Une librairie a echoue ^(souvent PyAudio, sans gravite^). On continue.
)

echo [2/4] Nettoyage...
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul

echo [3/4] Construction de l'application ^(2 a 6 minutes^)...
pyinstaller kryptonix.spec --noconfirm
if errorlevel 1 ( echo [X] Echec de PyInstaller. & pause & exit /b 1 )

echo [4/4] Creation de l'installateur...
set ISCC=
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe
if "%ISCC%"=="" (
    echo [X] Inno Setup 6 est introuvable. Installe-le ^(gratuit^) : https://jrsoftware.org/isdl.php
    echo     puis relance ce fichier.
    pause & exit /b 1
)
for /f %%v in ('python -c "from noyau.version import VERSION; print(VERSION)"') do set VERSION=%%v
"%ISCC%" /DMaVersion=%VERSION% installateur\kryptonix.iss
if errorlevel 1 ( echo [X] Echec d'Inno Setup. & pause & exit /b 1 )

echo.
echo ==========================================================
echo   TERMINE !  Ton installateur :  sortie\Kryptonix-Setup.exe
echo ==========================================================
explorer sortie
pause
