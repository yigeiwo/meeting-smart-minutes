@echo off
set PYTHONPATH=%~dp0ai-passport-simulator;%PYTHONPATH%
cd /d "%~dp0ai-passport-simulator"
python -m sim.panel --host 127.0.0.1 --port 5566
pause
