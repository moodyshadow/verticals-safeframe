@echo off
cd /d G:\AI\YoutubeShortsPipeline
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
"G:\AI\YoutubeShortsPipeline\venv\Scripts\python.exe" track_performance.py >> "G:\AI\YoutubeShortsPipeline\reports\track_performance_log.txt" 2>&1
