@echo off
set ROOT=%~dp0..
python "%ROOT%\src\generate_dataset.py" --output "%ROOT%"
if errorlevel 1 exit /b 1
python "%ROOT%\src\validate_dataset.py" --root "%ROOT%"
