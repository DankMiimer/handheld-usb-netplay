#!/bin/sh
# Handheld Link: browse another handheld over a USB cable and send it files.
# Runs on the console like the menu does, with the Python 3.12 this OPK
# brings (FunKey OS has none). Log: /mnt/FunKey/hlink/hlink.log
HERE="$(cd "$(dirname "$0")" && pwd)"
export LD_LIBRARY_PATH="$HERE/rt/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$HERE"
mkdir -p /mnt/FunKey/hlink
cd /
exec "$HERE/rt/bin/python3" -m hlink.main "$@" 2>> /mnt/FunKey/hlink/stderr.log
