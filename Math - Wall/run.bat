@echo off
echo ====================================================
echo  Tracker Setup
echo ====================================================
echo.

python --version
if errorlevel 1 (
    echo [ERROR] Python not found
    pause & exit /b 1
)

echo [setup] Installing packages...
pip install ultralytics pyrealsense2 python-osc opencv-python numpy openni --quiet
echo [setup] Done

:menu
echo.
echo ====================================================
echo  Select tracker:
echo ====================================================
echo  1. Combined Tracker  (zone + wrist, 1 camera)  OSC port 7000
echo  2. Zone Only Tracker (floor/wall zones only)    OSC port 7001
echo  3. Wrist Only Tracker (hand tracking only)      OSC port 7000
echo ====================================================
set /p choice="Enter [1/2/3]: "

if "%choice%"=="1" (
    python combined_tracker.py
    goto end
)
if "%choice%"=="2" (
    python topdown_tracker.py
    goto end
)
if "%choice%"=="3" (
    python yolo_tracker.py
    goto end
)
echo Invalid — please enter 1, 2 or 3
goto menu

:end
echo.
pause
