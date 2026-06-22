@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
REM charles16888-research daily build + git push (all comments ASCII-only)
REM cmd parses bat in cp950 before chcp 65001, any non-ASCII before chcp breaks bat

setlocal

set "PY=C:\Python\python.exe"
if not exist "%PY%" set "PY=python"

set "REPO=%~dp0"
set "LOG=%REPO%push_to_charles16888.log"

echo. >> "%LOG%"
echo ================================== >> "%LOG%"
echo [%date% %time%] Start daily push to charles16888 >> "%LOG%"
echo ================================== >> "%LOG%"

REM 1) trading day check
"%PY%" "E:\stock_chip_crawler\is_trading_day.py" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] non trading day, skip >> "%LOG%"
    exit /b 0
)

REM 2) verify data completeness
"%PY%" "E:\stock_chip_crawler\verify_today_data.py" >> "%LOG%" 2>&1

REM 3) build scripts (write to charles1688-research repo)
cd /d "%REPO%"

REM 3a) sync remote changes before generating files
echo [%date% %time%] git sync before build >> "%LOG%"
git fetch origin main >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] ERROR: git fetch failed before build >> "%LOG%"
    exit /b 2
)
git rebase origin/main >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] ERROR: git rebase failed before build >> "%LOG%"
    exit /b 2
)

echo [%date% %time%] [1/8] build_sectors_assets >> "%LOG%"
"%PY%" tools\build_sectors_assets.py >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] ERROR: build_sectors_assets failed >> "%LOG%"
    exit /b 4
)

echo [%date% %time%] [2/8] build_market_chart >> "%LOG%"
"%PY%" tools\build_market_chart.py >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] ERROR: build_market_chart failed >> "%LOG%"
    exit /b 4
)

echo [%date% %time%] [3/8] build_futures_chart >> "%LOG%"
"%PY%" tools\build_futures_chart.py >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] ERROR: build_futures_chart failed >> "%LOG%"
    exit /b 4
)

echo [%date% %time%] [4/8] build_options_chart >> "%LOG%"
"%PY%" tools\build_options_chart.py >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] ERROR: build_options_chart failed >> "%LOG%"
    exit /b 4
)

echo [%date% %time%] [5/8] build_chip_concentration >> "%LOG%"
"%PY%" tools\build_chip_concentration.py >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] ERROR: build_chip_concentration failed >> "%LOG%"
    exit /b 4
)

echo [%date% %time%] [6/8] build_view2_shareholder_divergence >> "%LOG%"
"%PY%" tools\build_view2_shareholder_divergence.py >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] ERROR: build_view2_shareholder_divergence failed >> "%LOG%"
    exit /b 4
)

echo [%date% %time%] [7/8] build_view4_tri_source_lamp >> "%LOG%"
"%PY%" tools\build_view4_tri_source_lamp.py >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] ERROR: build_view4_tri_source_lamp failed >> "%LOG%"
    exit /b 4
)

echo [%date% %time%] [8/8] build_three_factor_ranking >> "%LOG%"
"%PY%" tools\build_three_factor_ranking.py >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] ERROR: build_three_factor_ranking failed >> "%LOG%"
    exit /b 4
)

REM 4) git commit + push (PAT embedded in remote URL)
echo [%date% %time%] git push charles16888-research >> "%LOG%"

git add . >> "%LOG%" 2>&1

git diff-index --quiet HEAD --
if errorlevel 1 (
    set "TODAY=%date:~0,10%"
    git commit -m "daily: %TODAY% sectors + taiex + chips" >> "%LOG%" 2>&1
    git push >> "%LOG%" 2>&1
    if errorlevel 1 (
        echo [%date% %time%] WARN: git push failed, fetch/rebase/retry once >> "%LOG%"
        git fetch origin main >> "%LOG%" 2>&1
        if errorlevel 1 (
            echo [%date% %time%] ERROR: git fetch failed after push rejection >> "%LOG%"
            exit /b 3
        )
        git rebase origin/main >> "%LOG%" 2>&1
        if errorlevel 1 (
            echo [%date% %time%] ERROR: git rebase failed after push rejection >> "%LOG%"
            exit /b 3
        )
        git push >> "%LOG%" 2>&1
        if errorlevel 1 (
            echo [%date% %time%] ERROR: git push failed after retry >> "%LOG%"
            exit /b 3
        )
    )
    echo [%date% %time%] OK: pushed to GitHub, Cloudflare will deploy >> "%LOG%"
) else (
    echo [%date% %time%] SKIP: no changes to push >> "%LOG%"
)

echo [%date% %time%] DONE >> "%LOG%"
endlocal
exit /b 0
