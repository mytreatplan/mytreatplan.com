"""Gunicorn's entry point on the servers (deploy/systemd/gunicorn.service.template runs
`gunicorn wsgi:application` in the live release). Every site repo has this file, naming
its own project, so the host's one unit template serves the portal and the admin site."""
from mytreatplan.wsgi import application  # noqa: F401
