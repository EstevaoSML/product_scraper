"""Entrypoint for the Azure Functions custom handler; no Chrome in this process."""
import os
from pathlib import Path
import site

site.addsitedir(str(Path(__file__).parent / ".python_packages/lib/site-packages"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.functions_app:app", host="127.0.0.1", port=int(os.getenv("FUNCTIONS_CUSTOMHANDLER_PORT", "8000")),
                workers=1, access_log=False, proxy_headers=False, limit_concurrency=16)
