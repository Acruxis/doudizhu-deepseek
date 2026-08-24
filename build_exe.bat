@echo off
REM One-click Windows packaging script (PyInstaller single-file .exe)
REM Run this on a Windows machine with Python installed.

REM --- Enter the folder containing this script ---
REM `pushd` maps UNC/WSL paths (\\wsl.localhost\...) to a temporary drive
REM letter, because CMD cannot use a UNC path as the current directory.
REM Without this, pip would look for requirements.txt in C:\Windows and fail.
pushd "%~dp0" >nul 2>&1 || (
    echo Cannot enter the script directory: %~dp0
    pause
    exit /b 1
)

REM --- Install dependencies (optional; skip if already installed) ---
REM Always call via `python -m`, so it works even when the scripts folder is
REM not on PATH (e.g. pip installed into the per-user site-packages).
python -m pip install -e .
python -m pip install pyinstaller

REM --- Build the single-file GUI executable ---
python -m PyInstaller --onefile --windowed --name DouDiZhu main.py

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ==========================================
    echo  Build successful!
    echo  Executable: %CD%\dist\DouDiZhu.exe
    echo ==========================================
) else (
    echo.
    echo Build failed! Review errors above.
)

REM --- Leave the mapped drive ---
popd
pause
