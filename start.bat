@echo off
echo Installing dependencies...
C:\Python314\python.exe -m pip install structlog uvicorn fastapi pydantic-settings aiohttp websockets vaderSentiment redis tenacity streamlink itsdangerous python-multipart >nul 2>&1

echo Starting Highlightz...
start "Highlightz" /d "C:\Users\Ian\Desktop\SuperClipBot" C:\Python314\python.exe -m src.main

echo Waiting for server...
timeout /t 6 /nobreak >nul

start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" http://localhost:8000
