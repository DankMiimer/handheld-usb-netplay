#!/bin/sh
# Run every test: shellcheck, syntax under dash, the usbnp tests on fake
# firmware trees, and the Python tests. Needs dash, shellcheck and python3.
cd "$(dirname "$0")/.." || exit 1
rc=0
SH_FILES="usb-netplay/usbnp usb-netplay/lib/*.sh usb-netplay/lib/fw/*.sh USB?Netplay.sh USB?Netplay?-?Remove.sh"

echo "== shellcheck"
# shellcheck disable=SC2086   # the file list is a glob on purpose
shellcheck -s sh -x -e SC3043 $SH_FILES && echo ok || rc=1

echo "== syntax under dash"
for f in $SH_FILES; do dash -n "$f" || { echo "syntax error: $f"; rc=1; }; done
[ $rc = 0 ] && echo ok

echo "== usbnp on fake firmware trees"
bash tests/test_usbnp.sh || rc=1

echo "== python"
python3 -m py_compile usb-netplay/adb/tunnel.py usb-netplay/adb/helper.py usb-netplay/lib/dhcp_probe.py || rc=1
out=$(python3 tests/test_python.py 2>&1) || rc=1
printf '%s\n' "$out" | tail -n 12

[ $rc = 0 ] && echo "ALL TESTS PASSED" || echo "SOME TESTS FAILED"
exit $rc
