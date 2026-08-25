"""app — JARVIS backend, organized MVC-style:

- config.py, history.py, graph.py, retrieval.py, persona.py, providers/,
  images.py  — Model layer (settings, data, external services, parsing)
- controllers.py                                — Controller layer (request handling)
- http_server.py                                 — HTTP routing + static file serving
                                                     (adapts controllers.py to real HTTP)
"""
