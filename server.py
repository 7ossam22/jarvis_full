#!/usr/bin/env python3
"""server.py — JARVIS entry point. Run with `python3 server.py`.

The actual implementation lives in app/ — see app/http_server.py for HTTP
routing and static file serving, app/controllers.py for request handling,
and the other app/ modules for the model/persona/provider layers. This
file just starts it, so the run command stays unchanged.

Standard library only. No pip installs.
"""
from app.http_server import main

if __name__ == "__main__":
    main()
