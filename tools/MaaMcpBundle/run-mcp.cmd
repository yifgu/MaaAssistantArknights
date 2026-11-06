@echo off
"%~dp0python\python.exe" -I -B "%~dp0run_mcp.py" %*
exit /b %errorlevel%
