#!/bin/bash
set -euo pipefail
# Normal VT and serial console both work. Other interactive shells stay shells.
case $(tty) in /dev/tty1|/dev/ttyS0) ;; *) exit 0 ;; esac
# One installer owns the disks even if both consoles autologin.
exec 9>/run/omarchy-server-installer.lock
flock -n 9 || exit 0
umask 077
# A successful install leaves the live console available without re-running
# the disk wizard on the next login. /run is discarded on a fresh ISO boot.
if [[ -e /run/omarchy-server-install-complete ]]; then
  echo 'Installation already completed. Reboot to start the installed server.'
  exit 0
fi

cleanup_successful_install() {
  # All these paths are installer-owned on the live image. Never delete an
  # attached cidata drive or external key media. A failed installation keeps
  # its inputs so the operator can inspect and safely retry it.
  rm -f -- /root/user_configuration.json /root/user_credentials.json \
    /root/user_encrypt_installation.txt /root/authorized_keys \
    /run/omarchy-install/archinstall-user_configuration.json \
    /run/omarchy-install/provisioning-user_credentials.json \
    /run/omarchy-install/server-validated-*.json
}
export OMARCHY_MIRROR=stable OMARCHY_PATH=/usr/share/omarchy
export OMARCHY_INSTALL_CONFIG=/root/user_configuration.json
export OMARCHY_INSTALL_CREDS=/root/user_credentials.json
export OMARCHY_INSTALL_ENCRYPT_FILE=/root/user_encrypt_installation.txt
export OMARCHY_INSTALL_AUTHORIZED_KEYS_FILE=/root/authorized_keys
export OMARCHY_INSTALL_DEFER_PROVISIONING_FILE=/root/defer-provisioning
export OMARCHY_INSTALL_TAILSCALE_AUTHKEY_FILE=/root/tailscale_authkey
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
  echo 'Installer credentials remain in the live session for retry. If you will not retry, power off the live environment and remove the installation media.' >&2
  exit "$status"
fi
# The target is finalized; now remove live-only credential copies before
# permitting an automatic or interactive reboot, or returning to the shell.
cleanup_successful_install
sync
printf 'completed\n' > /run/omarchy-server-install-complete
if [[ -e /root/server-autoreboot ]]; then
  systemctl reboot
  exit $?
fi
if gum confirm 'Installation complete. Reboot now?'; then
  systemctl reboot
fi
exit 0
