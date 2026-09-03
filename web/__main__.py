"""Launch the web UI:  python3 -m web

Opens a local Flask server. The agent core is imported from the `agent` package
unchanged -- this is a second front end over the same run_turn().
"""

from __future__ import annotations

from .server import app

# 8000 rather than Flask's default 5000: on macOS, port 5000 is taken by the
# AirPlay Receiver (ControlCenter), which would collide with the demo.
HOST = "127.0.0.1"
PORT = 8000

if __name__ == "__main__":
    print(f"Sierra Outfitters is live at http://{HOST}:{PORT}  🏔️")
    print("Press Ctrl+C to stop.")
    app.run(host=HOST, port=PORT, debug=False)
