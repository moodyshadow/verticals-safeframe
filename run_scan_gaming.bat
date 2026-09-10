@echo off
cd /d C:\Users\szabo\youtube-shorts-pipeline
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8

REM tasklist/find checked only whether an ollama.exe PROCESS existed, not
REM whether the server was actually answering on its port - a stale/zombie
REM process (or the tray helper without the API listener up) satisfied the
REM check while still leaving the API unreachable, a real recurring cause
REM of "connection refused" draft failures. curl against the actual API
REM endpoint (same pattern already used for the SD webui check below) is
REM what genuinely proves it's ready.
curl -s -m 5 http://127.0.0.1:11434/api/tags >NUL 2>&1
if errorlevel 1 (
    echo Ollama not running, starting it... >> "C:\Users\szabo\youtube-shorts-pipeline\reports\scan_log.txt"
    start "" "G:\AI\Ollama\ollama.exe"
    timeout /t 15 /nobreak >NUL
)

REM Marketing-helper Ollama (port 11435, CPU-only) is also needed for
REM TopicEngine.auto_pick() and the b-roll vision-QA pass - normally kept
REM warm by the standalone DailyOverclocked_MarketingHelperOllama task
REM (06:45 daily), but that timer failing silently (as happened
REM 2026-09-05) used to take the whole production run down with it since
REM nothing here double-checked it. Calling its own start script is
REM idempotent - it no-ops instantly if already running.
call "G:\AI\YoutubeShortsPipeline\start_marketing_helper_ollama.bat"
curl -s -m 5 http://127.0.0.1:11435/api/tags >NUL 2>&1
if errorlevel 1 (
    echo Marketing-helper Ollama still not responding after start attempt, waiting longer... >> "C:\Users\szabo\youtube-shorts-pipeline\reports\scan_log.txt"
    timeout /t 30 /nobreak >NUL
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

"C:\Users\szabo\youtube-shorts-pipeline\venv\Scripts\python.exe" run_two_niches.py --niche gaming >> "C:\Users\szabo\youtube-shorts-pipeline\reports\scan_log.txt" 2>&1
