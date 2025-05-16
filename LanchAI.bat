@echo off
REM Launch the AI in Windows Terminal (wt.exe) if available, fallback to cmd
cd /d "%~dp0"
where wt >nul 2>nul
if %errorlevel%==0 (
    start wt -d "%CD%" cmd /k "python gemini_ai_v2.py"
) else (
    start cmd /k "python gemini_ai_v2.py"
)