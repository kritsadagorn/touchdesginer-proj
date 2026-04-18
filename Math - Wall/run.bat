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

echo [setup] Checking and installing packages...
pip install ultralytics pyrealsense2 python-osc opencv-python numpy openni --quiet
if errorlevel 1 (
    echo [ERROR] Install failed - trying one by one...
    pip install ultralytics
    pip install pyrealsense2
    pip install python-osc
    pip install opencv-python
    pip install numpy
    pip install openni
)
echo [setup] Done

:menu
echo.
echo ====================================================
echo  Select tracker:
echo ====================================================
echo  1. Wrist Tracker   (hand hover for level select) OSC port 7000
echo  2. Top-Down Tracker (person position tracking)   OSC port 7001
echo  3. Both trackers   (run simultaneously)
echo ====================================================
set /p choice="Enter [1/2/3]: "

if "%choice%"=="1" (
    python yolo_tracker.py
    goto end
)
if "%choice%"=="2" (
    python topdown_tracker.py
    goto end
)
if "%choice%"=="3" (
    start "Wrist Tracker" cmd /k python yolo_tracker.py
    python topdown_tracker.py
    goto end
)
echo Invalid — please enter 1, 2 or 3
goto menu

:end
echo.
pause
