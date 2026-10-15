@echo off
chcp 65001 >nul
echo === Gilam boti: o'rnatish ===
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Tayyor! Endi ishga tushirish uchun:  run.bat
pause
