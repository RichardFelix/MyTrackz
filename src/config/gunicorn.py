bind = "localhost:8001"
preload_app = True
# Gunicorn's default is a single sync worker, which lets one slow
# provider-bound request block every other user; a few workers keep the app
# responsive (the provider rate limiter is Redis-backed, so shared limits
# hold across processes).
workers = 3
timeout = 200
max_requests = 500
max_requests_jitter = 10

accesslog = "-"
errorlog = "-"
