@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0relink_premiere.ps1" -ProjectFile "%~dp0MV_Edit.prproj"
if errorlevel 1 (
  echo.
  echo Relink failed. Keep this window open and check the error above.
  pause
  exit /b 1
)
echo.
echo Project media paths are ready. Open MV_Edit.prproj in Premiere or Jianying.
pause
