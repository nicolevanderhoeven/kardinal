# Gunicorn settings for the container (see Dockerfile). Load with: gunicorn -c gunicorn.conf.py app:app
#
# gthread worker: 2 processes x 4 threads, so one slow request can no longer
# occupy a whole worker. The caches in app.py are thread-safe and per process.
import os

import gunicorn.http.wsgi

bind = "0.0.0.0:5000"
workers = 2
threads = 4
timeout = 30
graceful_timeout = 30

# Recycle workers now and then to bound memory growth on a small host.
max_requests = 1000
max_requests_jitter = 100

# Keep the heartbeat file in RAM (works with a read-only root filesystem).
if os.path.isdir("/dev/shm"):  # always true in the container; absent on macOS
    worker_tmp_dir = "/dev/shm"

# The control socket wants a writable home directory; the container filesystem
# is read-only and nothing uses it.
control_socket_disable = True

accesslog = "-"
errorlog = "-"

# Do not advertise "Server: gunicorn". (Traefik can strip it too; see
# kardinal-traefik-config.yml.)
gunicorn.http.wsgi.SERVER = "kardinal"
