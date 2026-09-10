@echo off
cd /d C:\Users\szabo\youtube-shorts-pipeline
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8

tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I "ollama.exe" >NUL
if errorlevel 1 (
    echo Ollama not running, starting it... >> "C:\Users\szabo\youtube-shorts-pipeline\reports\scan_log.txt"
    start "" "G:\AI\Ollama\ollama.exe"
    timeout /t 10 /nobreak >NUL
)

REM Gaming/entertainment b-roll now goes straight to local Stable Diffusion
REM (real screenshots of copyrighted games/movies can't come from stock
REM photo sites), so make sure the webui is up before producing.
REM (PATH change kept OUTSIDE any if(...) block: Windows' PATH contains
REM "Program Files (x86)", and expanding %PATH% inside a parenthesized
REM block splices that "(x86)" in as literal text, breaking the block's
REM own parenthesis parsing.)
set PATH=G:\AI\StableDiffusion;%PATH%
curl -s -m 5 http://127.0.0.1:7860/sdapi/v1/sd-models >NUL 2>&1
if errorlevel 1 (
    echo SD webui not running, starting it... >> "C:\Users\szabo\youtube-shorts-pipeline\reports\scan_log.txt"
    start "" cmd /c "G:\AI\StableDiffusion\webui-user.bat"
    timeout /t 60 /nobreak >NUL
)

"C:\Users\szabo\youtube-shorts-pipeline\venv\Scripts\python.exe" run_two_niches.py >> "C:\Users\szabo\youtube-shorts-pipeline\reports\scan_log.txt" 2>&1
