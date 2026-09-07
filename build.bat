@echo off
echo ========================================
echo   AI ??? - ????
echo ========================================
echo.

echo [1/3] Cleaning old build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [2/3] Building exe with PyInstaller...
pyinstaller --noconfirm --onedir --windowed ^
    --name "AI???" ^
    --add-data "static;static" ^
    --add-data "skills;skills" ^
    --hidden-import uvicorn ^
    --hidden-import uvicorn.logging ^
    --hidden-import uvicorn.loops ^
    --hidden-import uvicorn.loops.auto ^
    --hidden-import uvicorn.protocols ^
    --hidden-import uvicorn.protocols.http ^
    --hidden-import uvicorn.protocols.http.auto ^
    --hidden-import uvicorn.protocols.websockets ^
    --hidden-import uvicorn.protocols.websockets.auto ^
    --hidden-import uvicorn.lifespan ^
    --hidden-import uvicorn.lifespan.on ^
    --collect-submodules uvicorn ^
    run.py

echo [3/3] Copying config files to dist...
copy requirements.txt "dist\AI???\" >nul
copy ????.md "dist\AI???\" >nul

echo.
echo ========================================
echo   ?????
echo   ????: dist\AI???\
echo ========================================
echo.
echo ????: ?? dist\AI???\AI???.exe
pause
