"""Gunicorn settings, read automatically when Render runs `gunicorn app:app`.

The app applies pending database migrations when it starts. The first start
after an upgrade can take longer than gunicorn's default 30-second limit
(the app runs in a different region from the Neon database), so allow more time.
The bind address comes from Render's $PORT, as before.
"""
workers = 1
timeout = 180
graceful_timeout = 30
