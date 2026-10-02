@echo off
chcp 65001 >nul
color 0b
title KRYPTONIX - noyau local
cd /d "%~dp0"

echo ==========================================
echo   KRYPTONIX - demarrage
echo ==========================================
echo.

REM --- 1. Python present ? ---
python --version >nul 2>&1
if errorlevel 1 (
    echo [X] Python est introuvable dans le PATH.
    echo     Installe-le depuis https://www.python.org puis relance ce fichier.
    pause
    exit /b 1
)

REM --- 2. Environnement virtuel ---
if not exist ".venv\Scripts\python.exe" (
    echo [*] Creation de l'environnement virtuel...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    echo [*] Installation des dependances...
    python -m pip install --upgrade pip
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

REM --- 3. Ollama en tache de fond ---
tasklist /FI "IMAGENAME eq ollama.exe" 2>nul | find /I "ollama.exe" >nul
if errorlevel 1 (
    echo [*] Demarrage d'Ollama...
    start "" /min ollama serve
    timeout /t 3 /nobreak >nul
)

REM --- 4. Lancement ---
echo.
python lancer.py %*

echo.
echo KRYPTONIX s'est arrete.
pause
