#!/bin/bash
# Optional Gum menu. Nothing here reconfigures the running live installer.
network_menu() {
  local config_script=$1 choice name family mode address gateway dns draft current
  local devices
  local -a names
  NETWORK_INTERFACES_JSON='{}'
  choice=$(gum choose --header 'Networking (automatic IPv4 + IPv6 by default)' \
    'Automatic dual-stack (default)' 'Configure interfaces') || return 1
  [[ $choice == 'Configure interfaces' ]] || return 0
  devices=$(python "$config_script" --list-interfaces) || return 1
  mapfile -t names < <(jq -r '.[].name' <<<"$devices")
  if ((${#names[@]} == 0)); then
    printf 'No Ethernet interfaces detected; the installer will continue without network profiles.\n' >&2
    return 0
  fi

  show_network() {
    local selected=$1 interface
    for interface in "${names[@]}"; do
      if [[ $selected == all || $selected == "$interface" ]]; then
        printf '\n--- %s (installed system) ---\n' "$interface"
        jq -c --arg name "$interface" '.[$name] // {}' <<<"$NETWORK_INTERFACES_JSON" |
          python "$config_script" --render-interface "$interface"
      fi
    done | gum pager
  }

  configure_family() {
    local name=$1 family=$2 mode current draft address gateway dns error
    if [[ $family == 4 ]]; then
      choice=$(gum choose --header "$name IPv4" 'Automatic (DHCP)' 'Manual (static)' 'Disabled') || return 1
      case $choice in
        'Automatic (DHCP)') mode=dhcp ;;
        'Manual (static)') mode=static ;;
        Disabled) mode=disabled ;;
      esac
    else
      choice=$(gum choose --header "$name IPv6" 'Automatic (RA/SLAAC)' 'Manual (static)' 'Disabled') || return 1
      case $choice in
        'Automatic (RA/SLAAC)') mode=auto ;;
        'Manual (static)') mode=static ;;
        Disabled) mode=disabled ;;
      esac
    fi
    current=$(jq -c --arg name "$name" '.[$name] // {}' <<<"$NETWORK_INTERFACES_JSON")
    draft=$(jq -cn --argjson base "$current" --arg family "$family" --arg mode "$mode" '
      $base | if $family == "4" then
        .ipv4=$mode | del(.address4,.gateway4,.dns4)
      else
        .ipv6=$mode | del(.address6,.gateway6,.dns6)
      end')
    if [[ $mode == static ]]; then
      address=$(gum input --header "$name IPv$family address/prefix") || return 1
      gateway=$(gum input --header "$name IPv$family gateway (optional)") || return 1
      dns=$(gum input --header "$name IPv$family DNS servers (optional, space-separated)") || return 1
      draft=$(jq -cn --argjson base "$draft" --arg family "$family" \
        --arg address "$address" --arg gateway "$gateway" --arg dns "$dns" '
        $base | if $family == "4" then
          .address4=$address | .gateway4=$gateway | .dns4=$dns
        else
          .address6=$address | .gateway6=$gateway | .dns6=$dns
        end')
    fi
    if ! error=$(python "$config_script" --render-interface "$name" <<<"$draft" 2>&1); then
      printf 'Invalid IPv%s configuration for %s: %s\n' "$family" "$name" "$error" >&2
      return 0
    fi
    NETWORK_INTERFACES_JSON=$(jq -c --arg name "$name" --argjson spec "$draft" \
      '.[$name] = $spec' <<<"$NETWORK_INTERFACES_JSON")
  }

  while :; do
    choice=$(printf '%s\n' 'Show current configuration' 'Apply and return' "${names[@]}" |
      gum choose --header 'Configure installed server networking') || return 1
    case $choice in
      'Show current configuration') show_network all ;;
      'Apply and return') break ;;
      *)
        name=$choice
        while :; do
          current=$(jq -c --arg name "$name" '.[$name] // {}' <<<"$NETWORK_INTERFACES_JSON")
          choice=$(gum choose --header "$name (IPv4: $(jq -r '.ipv4 // "dhcp"' <<<"$current"); IPv6: $(jq -r '.ipv6 // "auto"' <<<"$current"))" \
            'Configure IPv4' 'Configure IPv6' 'Show current configuration' 'Restore defaults' 'Back') || return 1
          case $choice in
            'Configure IPv4') configure_family "$name" 4 ;;
            'Configure IPv6') configure_family "$name" 6 ;;
            'Show current configuration') show_network "$name" ;;
            'Restore defaults') NETWORK_INTERFACES_JSON=$(jq -c --arg name "$name" 'del(.[$name])' <<<"$NETWORK_INTERFACES_JSON") ;;
            Back) break ;;
          esac
        done
        ;;
    esac
  done
}
