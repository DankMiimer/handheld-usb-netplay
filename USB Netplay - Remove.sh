#!/bin/sh
# USB Netplay: remove it from this handheld. Deletes its udev rules and
# background service and puts back every setting it changed.
exec /bin/sh "$(dirname "$0")/usb-netplay/usbnp" menu-remove
