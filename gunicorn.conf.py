import os

workers = 1
worker_class = "gthread"
threads = 4
bind = "0.0.0.0:" + os.environ.get("PORT", "8000")
timeout = 120
