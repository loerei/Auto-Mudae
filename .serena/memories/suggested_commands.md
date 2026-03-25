Windows-native commands for this repo:
- Setup and launch WebUI: `setup.bat` or `run_webui.bat`
- Backend import path for direct commands: `$env:PYTHONPATH=(Resolve-Path .\\src).Path`
- Run server directly: `.\\.venv\\Scripts\\python.exe -m mudae.web.server`
- Frontend install/build: `cd webui && npm install && npm run build`
- Run targeted tests: `.\\.venv\\Scripts\\python.exe -m pytest tests\\unit\\test_webui_runtime.py tests\\unit\\test_webui_server.py`
- Run broader tests: `.\\.venv\\Scripts\\python.exe -m pytest`
- Fast smoke with TestClient: `.\\.venv\\Scripts\\python.exe -c "from fastapi.testclient import TestClient; from mudae.web.server import app; client=TestClient(app); client.__enter__(); print(client.get('/api/overview').status_code); client.__exit__(None,None,None)"`
- Frontend build only: `cd webui && npm run build`