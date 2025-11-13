from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from server.utils import load_config, save_config

app = FastAPI()

app.add_middleware(
  CORSMiddleware,
  allow_origins=["*"],
  allow_credentials=True,
  allow_methods=["*"],
  allow_headers=["*"],
)

@app.get("/config")
def get_config():
  return load_config()

@app.post("/config")
def update_config(new_config: dict):
  save_config(new_config)
  return {"status": "success", "data": new_config}

PATH = Path(__file__).resolve().parent.parent / "web" / "dist"

@app.get("/")
async def root_index():
  return FileResponse(PATH / "index.html", headers={
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0"
  })

@app.get("/{path:path}")
async def fallback(path: str):
  file_path = PATH / path
  headers = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0"
  }

  if file_path.is_file():
    media_type = "application/javascript" if file_path.suffix in {".js", ".mjs"} else None
    return FileResponse(file_path, media_type=media_type, headers=headers)

  return FileResponse(PATH / "index.html", headers=headers)
