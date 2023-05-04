import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "graphql.settings")

application = get_wsgi_application()
app = application  # @vercel/python looks for a callable named ``app``
