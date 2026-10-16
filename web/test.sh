#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/backend"

if [ ! -x .venv-unix/bin/python ]; then
  printf 'Web dependencies are not set up yet. Run "make web-setup" or ./setup.sh first.\n' >&2
  exit 1
fi

PYTHONPATH=core .venv-unix/bin/python -m unittest discover -s tests -v
