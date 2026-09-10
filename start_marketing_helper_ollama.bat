@echo off
REM Starts the dedicated "marketing-helper" Ollama instance (port 11435,
REM forced CPU-only via OLLAMA_LLM_LIBRARY=cpu so it never competes for GPU
REM with the main Ollama/SD webui/Forge instances already running through
REM the day). Serves both TopicEngine.auto_pick() (qwen2.5:14b-instruct)
REM and the b-roll vision-QA check (llava:7b) used throughout the whole
REM production run - needs to already be up and warmed before
REM DailyOverclocked_OpportunityScan fires, not started cold by it.
REM
REM Scheduled to run standalone, ~15 min ahead of the main pipeline
REM (DailyOverclocked_MarketingHelperOllama, 06:45 daily) instead of being
REM folded into run_scan_two.bat's own startup checks - a cold model-load
REM race on the very first call was a real, recurring cause of empty-JSON
REM draft failures earlier in this project (see llm.py's retry fix), and a
REM 15-minute head start gives it time to actually finish loading first.
REM Also called directly (idempotent) from run_scan_gaming.bat/
REM run_scan_tech.bat as a fallback, since the standalone timer failing
REM silently (2026-09-05) took the whole production run down with it.

curl -s -m 5 http://127.0.0.1:11435/api/tags >NUL 2>&1
if not errorlevel 1 (
    echo %DATE% %TIME%: marketing-helper Ollama already running, nothing to do. >> "G:\AI\YoutubeShortsPipeline\ollama_marketing_helper.log"
    exit /b 0
)

echo %DATE% %TIME%: marketing-helper Ollama not running, starting it (CPU-only, port 11435)... >> "G:\AI\YoutubeShortsPipeline\ollama_marketing_helper.log"
set OLLAMA_HOST=127.0.0.1:11435
set OLLAMA_LLM_LIBRARY=cpu
start "" "G:\AI\Ollama\ollama.exe" serve
