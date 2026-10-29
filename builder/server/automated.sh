#!/bin/bash
set -euo pipefail
# Normal VT and serial console both work. Other interactive shells stay shells.
case $(tty) in /dev/tty1|/dev/ttyS0) ;; *) exit 0 ;; esac
# One installer owns the disks even if both consoles autologin.
exec 9>/run/omarchy-server-installer.lock
flock -n 9 || exit 0
umask 077
export OMARCHY_MIRROR=stable OMARCHY_PATH=/usr/share/omarchy
export OMARCHY_INSTALL_CONFIG=/root/user_configuration.json
export OMARCHY_INSTALL_CREDS=/root/user_credentials.json
export OMARCHY_INSTALL_ENCRYPT_FILE=/root/user_encrypt_installation.txt
export OMARCHY_INSTALL_AUTHORIZED_KEYS_FILE=/root/authorized_keys
export OMARCHY_UI_INTERACTIVE=no
if /usr/local/bin/omarchy-cidata-load; then
  echo 'Using installer-only cidata configuration.'
else
  /root/configurator
fi
# Do not let the desktop cidata format turn this image into another edition.
cd /usr/share/omarchy-iso
set +e
python -m orchestrator.main 2>&1 | tee /var/log/omarchy-install.log
status=${PIPESTATUS[0]}
set -e
if ((status)); then
  echo "Installation failed ($status). See /var/log/omarchy-install.log; correct the input and retry /root/.automated_script.sh." >&2
  exit "$status"
fi
sync
if [[ -e /root/server-autoreboot ]]; then systemctl reboot; fi
gum confirm 'Installation complete. Reboot now?' && systemctl reboot
