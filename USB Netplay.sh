#!/bin/sh
# USB Netplay: RetroArch netplay over a USB cable between two handhelds.
# Put this file and the usb-netplay folder in the Ports folder (see README.md)
# and run it from the menu. It sets this handheld up (or repairs the setup),
# brings the cable link up if a handheld is plugged in, and shows the status.
# Running it again is safe.
exec /bin/sh "$(dirname "$0")/usb-netplay/usbnp" menu
