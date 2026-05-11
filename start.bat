@echo off
title UserBot Rassylshchik

echo.
echo  ==========================================
echo       USERBOT RASSYLSHCHIK
echo  ==========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo  [OSHIBKA] Python ne naiden!
    echo  Skachayte s https://python.org
    echo  Pri ustanovke otmetyte Add Python to PATH
    pause
    exit /b 1
)
echo  [OK] Python naiden.

if not exist ".env" (
    echo.
    echo  [OSHIBKA] Fayl .env ne naiden!
    echo  Ubedites chto .env lezhit ryadom s etim faylom.
    pause
    exit /b 1
)
echo  [OK] Fayl .env naiden.

echo.
echo  Proveryayu zavisimosti...

pip show pyrogram >nul 2>&1
if errorlevel 1 (
    echo  Ustanavlivayu pyrogram tgcrypto python-dotenv...
    pip install pyrogram tgcrypto python-dotenv
    goto run
)

pip show python-dotenv >nul 2>&1
if errorlevel 1 (
    echo  Ustanavlivayu python-dotenv...
    pip install python-dotenv
    goto run
)

echo  [OK] Vse zavisimosti ustanovleny.

:run
echo.
echo  Zapuskayu userbot_sender.py...
echo  ------------------------------------------
echo.

python userbot_sender.py

echo.
echo  ------------------------------------------
echo  Skript zavershil rabotu.
pause