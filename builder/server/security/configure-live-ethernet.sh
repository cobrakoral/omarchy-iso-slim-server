#!/usr/bin/env bash
# Patch only the pinned ArchISO releng Ethernet bootstrap profile, not the host.
set -euo pipefail
profile=${1:?Provide the staged live Ethernet profile}
for required in '[Network]' 'DHCP=yes' 'MulticastDNS=yes' '[DHCPv4]'; do
  grep -Fxq "$required" "$profile" || {
    printf 'Unexpected ArchISO live Ethernet profile: missing %s\n' "$required" >&2
    exit 1
  }
done
sed -i \
  -e 's/^DHCP=yes$/DHCP=ipv4/' \
  -e 's/^MulticastDNS=yes$/MulticastDNS=no/' \
  -e '/^\[Network\]$/a IPv6AcceptRA=yes\nLinkLocalAddressing=ipv6\nLLMNR=no' \
  -e '/^\[DHCPv4\]$/a UseHostname=no' \
  "$profile"
printf '\n[DHCPv6]\nUseHostname=no\n' >> "$profile"
