"""Entry point: ``python main.py`` locally, ``main:app`` for Gunicorn/Uvicorn."""

import os

from resan.app import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
