"""Run with `python -m app`. Listens on $PORT (default 8000)."""

import os

import uvicorn

uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), proxy_headers=True)
