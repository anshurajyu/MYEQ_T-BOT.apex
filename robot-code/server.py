"""Phase 1 connection test for the MYEQUATION T-BOT phone controller.

This module intentionally contains no servo imports or motor-control code.
It is safe to run while verifying phone-to-host Wi-Fi connectivity.
"""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="MYEQUATION T-BOT", version="0.1.0")


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    """Show a mobile-friendly confirmation page for Phase 1."""
    return """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>T-BOT Gesture Control</title>
    <style>
      body { background: #101820; color: #f2f7fb; font-family: system-ui, sans-serif;
             margin: 0; min-height: 100vh; display: grid; place-items: center; }
      main { max-width: 34rem; padding: 2rem; text-align: center; }
      .status { color: #52e28f; font-size: 1.4rem; font-weight: 700; }
      .notice { color: #b8c7d5; }
    </style>
  </head>
  <body>
    <main>
      <h1>MYEQUATION T-BOT</h1>
      <h2>Phase 1: Connection Test</h2>
      <p class="status">CONNECTED</p>
      <p>Phone &rarr; Host connection is working.</p>
      <p class="notice">This phase does not access the camera or robot motors.</p>
    </main>
  </body>
</html>"""


@app.get("/test")
def test() -> dict[str, str]:
    """Provide a small machine-readable connectivity check."""
    return {"status": "connected", "robot": "MYEQUATION T-BOT", "phase": "1"}
