#!/bin/sh
set -e
python manage.py check
python manage.py migrate --noinput
