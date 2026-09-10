@echo off
cd /d G:\AI\YoutubeShortsPipeline
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
"G:\AI\YoutubeShortsPipeline\venv\Scripts\python.exe" bot_discussion.py >> "G:\AI\YoutubeShortsPipeline\reports\bot_discussion_log.txt" 2>&1
