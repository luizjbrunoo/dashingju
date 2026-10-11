#!/bin/sh
set -e
export DJANGO_PROCESS="${DJANGO_PROCESS:-worker}"
exec python manage.py qcluster
