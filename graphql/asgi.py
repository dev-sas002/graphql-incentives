"""ASGI entry point. Gunicorn serves ``graphql.wsgi``; this exists for ASGI hosts."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "graphql.settings")

application = get_asgi_application()
