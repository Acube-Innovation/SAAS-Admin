from .staging import *  # noqa: F403
from .staging import env

SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", "31536000"))
SECURE_HSTS_PRELOAD = True
