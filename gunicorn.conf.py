"""Gunicorn settings for a small long-running Flask + PyMongo app.

preload_app is False so each worker builds its own MongoClient after fork.
Forking an already-connected client is unsafe.
"""

bind = "127.0.0.1:8000"
workers = 3
worker_class = "sync"
timeout = 60
graceful_timeout = 30
keepalive = 5
preload_app = False
accesslog = "-"
errorlog = "-"
loglevel = "info"
proc_name = "podcast-transcript-search"
