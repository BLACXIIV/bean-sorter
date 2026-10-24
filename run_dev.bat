@echo off
rem Dev run on a laptop: webcam + simulated valve + separate dev database.
rem Double-click or run from a terminal. Ctrl+C (or Enter) stops everything.
rem All logic lives in run_dev.ps1 - cmd can't trap Ctrl+C cleanly.
rem Optional arg: camera source, e.g. run_dev.bat webcam:1  /  run_dev.bat sim
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_dev.ps1" %*
