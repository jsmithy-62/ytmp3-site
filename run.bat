@echo off
REM Activate virtualenv (if you use one)
if exist venv\Scripts\activate.bat (
  call venv\Scripts\activate
)

REM Force QR/download links to use your LAN IP
set PUBLIC_HOST=http://192.168.0.132:5000

REM Start the app
python app.py

pause