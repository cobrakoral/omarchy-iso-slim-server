#!/bin/bash
set -euo pipefail
export OMARCHY_EDITION=server OMARCHY_ARCH=x86_64
# All temporary roots and downloaded packages belong to this edition's cache.
export TMPDIR=/server-cache/tmp
mkdir -p "$TMPDIR"
trap 'chown -R "${HOST_UID}:${HOST_GID}" /out /server-cache' EXIT
pacman-key --init
pacman --noconfirm -Syu archiso arch-install-scripts btrfs-progs git sudo base-devel grub jq
pacman-key --add /builder/omarchy.gpg
pacman-key --lsign-key 40DFB630FF42BCFFB047046CF0134EE680CAC571
online=/tmp/server-online.conf
# The server uses the same stable Arch/Omarchy repositories, no unsigned T2 repo.
sed '/^\[arch-mact2\]/,$d' /configs/pacman-online-stable.conf > "$online"
pacman --config "$online" --noconfirm -Sy omarchy-keyring
pacman-key --populate archlinux omarchy
work=/server-cache/profile
mirror=/server-cache/mirror
# This is our generated profile only, never a host/shared cache.
rm -rf "$work"
mkdir -p "$work" "$mirror"
cp -a /archiso/configs/releng/. "$work/"
cp -a /configs/. "$work/"
# Replace the inherited releng live package set, not just the target set.
cp /builder/server/live.packages "$work/packages.x86_64"
cp /builder/linux-omarchy.preset "$work/airootfs/etc/mkinitcpio.d/linux-omarchy.preset"
rm -f "$work/airootfs/etc/mkinitcpio.d/linux.preset"
# No graphical splash, network daemon competition or live cloud-init.
cat > "$work/airootfs/etc/mkinitcpio.conf.d/archiso.conf" <<'CONF'
MODULES=(loop)
HOOKS=(base udev microcode modconf memdisk archiso archiso_loop_mnt archiso_pxe_common archiso_pxe_nbd archiso_pxe_http archiso_pxe_nfs block filesystems keyboard)
CONF
find "$work/airootfs/etc/systemd/system" -type l \( -name '*cloud*' -o -name '*NetworkManager*' -o -name '*reflector*' -o -name '*iwd*' -o -name '*dhcpcd*' -o -name '*sshd*' \) -delete
rm -rf "$work/airootfs/etc/systemd/system/reflector.service.d"
# root autologin is live-media only; installed getty comes from systemd.
cp /builder/server/automated.sh "$work/airootfs/root/.automated_script.sh"
cp /builder/server/configurator "$work/airootfs/root/configurator"
mkdir -p "$work/airootfs/usr/share/omarchy-iso"
cp /builder/server/config.py "$work/airootfs/usr/share/omarchy-iso/server_config.py"
# Installed systems retain the signed server channel, independently of the build mirror.
sed '/^\[options\]/a CheckSpace' "$online" > "$work/airootfs/usr/share/omarchy-iso/server-online.conf"
printf '\nInclude = /etc/pacman.d/omarchy-server.conf\n' >> "$work/airootfs/usr/share/omarchy-iso/server-online.conf"
cp /builder/server/check-packages.py "$work/airootfs/usr/share/omarchy-iso/check-server-packages.py"
printf 'server\n' > "$work/airootfs/etc/omarchy-edition"
# Releng's normal root shell is zsh; the server live console needs only Bash.
printf 'root:x:0:0:root:/root:/bin/bash\n' > "$work/airootfs/etc/passwd"
printf '/root/.automated_script.sh\n' > "$work/airootfs/root/.bash_profile"
mkdir -p "$work/airootfs/etc/systemd/system/serial-getty@ttyS0.service.d"
printf '[Service]\nExecStart=\nExecStart=-/sbin/agetty --autologin root --noclear --keep-baud 115200,38400,9600 %%I $TERM\n' > "$work/airootfs/etc/systemd/system/serial-getty@ttyS0.service.d/autologin.conf"
printf 'stable\n' > "$work/airootfs/root/omarchy_mirror"
printf 'quattro\n' > "$work/airootfs/root/omarchy_iso_ref"
printf 'linux-omarchy\n' > "$work/airootfs/root/omarchy_kernel"
printf 'OMARCHY_RUNTIME_PACKAGE=omarchy-server\nOMARCHY_SETTINGS_PACKAGE=omarchy-server\nOMARCHY_NVIM_PACKAGE=omarchy-server\n' > "$work/airootfs/usr/share/omarchy-iso/package-targets"
sed -i -e 's/^iso_name=.*/iso_name="omarchy-slim-server-unofficial"/' \
  -e 's/iso_label="OMARCHY_/iso_label="OMARCHY_SRV_/' \
  -e 's/^iso_publisher=.*/iso_publisher="Unofficial community edition"/' \
  -e 's/^iso_application=.*/iso_application="Omarchy Slim Server Installer (unofficial)"/' \
  -e "/'-comp'/i\  '-processors' '2'" "$work/profiledef.sh"
source /builder/grub-platform.sh
for f in "$work"/grub/{grub,loopback}.cfg; do
  configure_grub_platform "$f" x86_64
  sed -i -e 's/%KERNEL%/linux-omarchy/g' -e 's/%BOOT_SPLASH_KERNEL_OPTIONS%//g' \
    -e 's/%KERNEL_OPTIONS%/console=tty0 console=ttyS0,115200 initramfs_async=0/g' "$f"
done
for f in "$work"/syslinux/*linux.cfg; do
  sed -i -e 's/quiet splash //g' -e '/^APPEND /s/$/ console=tty0 console=ttyS0,115200/' "$f"
done
printf 'source_commit=%s\narchiso_revision=%s\n' "${OMARCHY_SERVER_SOURCE_COMMIT:-unknown}" "${OMARCHY_SERVER_ARCHISO_COMMIT:-unknown}" > "$work/airootfs/usr/share/omarchy-iso/server-build-info"
# Drop the Mac-specific entry whose second kernel is deliberately not shipped.
python - "$work/grub/grub.cfg" "$work/grub/loopback.cfg" <<'PY'
import sys
from pathlib import Path
for name in sys.argv[1:]:
    p=Path(name); s=p.read_text(); lines=s.splitlines(True); out=[]; depth=0; skip=False
    for line in lines:
        if 'menuentry ' in line and ('T2' in line or 'Mac' in line): skip=True; depth=0
        if skip:
            depth+=line.count('{')-line.count('}')
            if depth==0: skip=False
        else: out.append(line)
    p.write_text(''.join(out))
PY
# These packages were built and signed outside this container. Only public trust
# anchors and signed artifacts are mounted; no private signing keys are copied.
pacman-key --add /server-repository/omarchy-server.asc
pacman-key --lsign-key 7C5C6FEB40A5B493007FAA3650B1EB533F640140
sed '/^\[omarchy\]/i [omarchy-server]\nSigLevel = PackageRequired DatabaseRequired TrustedOnly\nServer = file:///server-repository/\n' "$online" > /tmp/server-with-local.conf
online=/tmp/server-with-local.conf
mapfile -t target < <(grep -Ev '^(#|$)' /builder/server/target.packages)
mapfile -t live < <(grep -Ev '^(#|$)' /builder/server/live.packages)
mkdir -p /tmp/server-db
pacman --config "$online" --dbpath /tmp/server-db --cachedir "$mirror" -Syw --noconfirm "${target[@]}" "${live[@]}" cloud-init omarchy-server
# Persist the exact upstream dependency transaction for audit/replay.
pacman --config "$online" --dbpath /tmp/server-db -Sp --print-format '%n %v %f %h' "${target[@]}" "${live[@]}" cloud-init omarchy-server > /out/server-packages.lock
pacman --config "$online" --dbpath /tmp/server-db -Sp --print-format '%n' "${target[@]}" omarchy-server > /tmp/server-target-names
python /builder/server/check-packages.py /tmp/server-target-names
pacman --config "$online" --dbpath /tmp/server-db -Sp --print-format '%f' "${target[@]}" "${live[@]}" cloud-init omarchy-server | bash /builder/prune-offline-mirror.sh "$mirror"
bash /builder/index-offline-mirror.sh "$mirror"
mkdir -p /var/cache/omarchy/mirror
ln -s "$mirror" /var/cache/omarchy/mirror/offline
cp /configs/pacman-offline.conf "$work/pacman-offline.conf"
sed '/^\[options\]/a CacheDir = /server-cache/mirror/' "$work/pacman-offline.conf" >/tmp/server-image.conf
# Build the official packed Btrfs image directly from the minimal closure.
source /etc/makepkg.conf
gcc -std=gnu11 $CFLAGS -Wall -Wextra -pthread $LDFLAGS -o /usr/local/bin/omarchy-image-write /builder/omarchy-image-write.c -lzstd
install -m755 /usr/local/bin/omarchy-image-write "$work/airootfs/usr/local/bin/omarchy-image-write"
image_dir="$work/work/iso/arch/x86_64"
mkdir -p "$image_dir"
OMARCHY_IMAGE_LOCALDB_COPY=/server-cache/image-localdb \
 bash /builder/build-root-image.sh /tmp/server-image.conf "$image_dir/omarchy-root.img.zst" "${target[@]}" omarchy-server
(cd "$image_dir" && sha256sum omarchy-root.img.zst > omarchy-root.img.zst.sha256)
cp /builder/server/target.packages "$work/airootfs/usr/share/omarchy-iso/omarchy-base.packages"
: > "$work/airootfs/usr/share/omarchy-iso/omarchy-other.packages"
find /server-cache/image-localdb/local -mindepth 1 -maxdepth 1 -type d | wc -l > "$work/airootfs/usr/share/omarchy-iso/expected-packages"
# Only cloud-init's delta is shipped; complete DB lets --needed skip the base.
pacman --config "$online" --dbpath /tmp/server-db -Sp --print-format '%f' cloud-init > /tmp/cloud-files
python - "$mirror" /server-cache/image-localdb /tmp/cloud-files <<'PY' > "$work/airootfs/usr/share/omarchy-iso/offline-mirror.shipped"
import pathlib, subprocess, sys
mirror,db,closure=map(pathlib.Path,sys.argv[1:]); installed={}
for desc in (db/'local').glob('*/desc'):
    s=desc.read_text().splitlines(); installed[s[s.index('%NAME%')+1]]=s[s.index('%VERSION%')+1]
for f in closure.read_text().splitlines():
    name,version=subprocess.check_output(['pacman','-Qp',str(mirror/f)],text=True).split()
    if installed.get(name)!=version: print(f)
PY
(cd "$mirror"; { cat "$work/airootfs/usr/share/omarchy-iso/offline-mirror.shipped"; echo offline.db.tar.gz; } | xargs -d '\n' sha256sum) > "$work/airootfs/usr/share/omarchy-iso/offline-mirror.sha256"
# Copying the full mirror here would duplicate disk use; mkarchiso sees it
# through the profile symlink, then customize_airootfs prunes its own copy.
mkdir -p "$work/airootfs/var/cache/omarchy/mirror"
cp -al "$mirror" "$work/airootfs/var/cache/omarchy/mirror/offline"
cp "$work/pacman-offline.conf" "$work/airootfs/etc/pacman.conf"
for unit in omarchy-root-image-verify omarchy-mirror-verify; do
  mkdir -p "$work/airootfs/etc/systemd/system/$unit.service.d"
  printf '[Service]\nTimeoutStartSec=1800\n' > "$work/airootfs/etc/systemd/system/$unit.service.d/50-timeout.conf"
done
mkarchiso -v -w "$work/work" -o /out "$work"
(cd /out && sha256sum omarchy-slim-server-unofficial-*.iso > SHA256SUMS)
