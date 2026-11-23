#!/usr/bin/env bash
set -euo pipefail

readonly state_dir="${RUNNER_TEMP:?}/renovate-private-harbor"
readonly bin="$state_dir/tailscale_1.102.3_amd64"
readonly socket="$state_dir/tailscaled.sock"
readonly unit="renovate-private-harbor-tailscaled"
readonly runner_tag="tag:homelab-ci-renovate"
readonly registry_tag="tag:homelab-harbor-registry"
readonly registry_peer="homelab-harbor-registry.tail67beb.ts.net."
readonly harbor="harbor.stinkyboi.com"

join() {
  set +x
  : "${RENOVATE_TAILSCALE_AUTH_KEY:?Dedicated signed tailnet key is missing}"
  export -n RENOVATE_TAILSCALE_AUTH_KEY
  umask 077
  mkdir "$state_dir"
  cd "$state_dir"
  curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
    --retry 3 --max-time 120 \
    https://pkgs.tailscale.com/stable/tailscale_1.102.3_amd64.tgz -o release.tgz
  sha256sum --check --status <<<'36ddd9b51be57ffc2990cf76323cfa13643bfbb1b8a969f6183fa164741cdef5  release.tgz'
  tar xzf release.tgz tailscale_1.102.3_amd64/tailscale tailscale_1.102.3_amd64/tailscaled

  touch "$state_dir/daemon-attempted"
  sudo systemd-run --quiet --collect --unit="$unit" --property=Type=exec \
    --property=TimeoutStopSec=20s --property=StandardOutput=null --property=StandardError=null \
    "$bin/tailscaled" --statedir="$state_dir/state" --socket="$socket"
  for _ in {1..30}; do
    [[ -S "$socket" ]] && break
    sleep 0.5
  done
  test -S "$socket"
  if ! builtin printf '%s' "$RENOVATE_TAILSCALE_AUTH_KEY" | sudo "$bin/tailscale" --socket="$socket" up \
    --auth-key=file:/dev/stdin --hostname="renovate-${GITHUB_RUN_ID:?}-${GITHUB_RUN_ATTEMPT:?}" \
    --accept-routes=false --accept-dns=false --timeout=2m >/dev/null 2>&1; then
    echo "::error::Signed private registry enrollment failed."
    return 1
  fi
  unset RENOVATE_TAILSCALE_AUTH_KEY

  local registry_ip
  if ! registry_ip="$(
    sudo "$bin/tailscale" --socket="$socket" status --json 2>/dev/null |
      jq -er --arg runner "$runner_tag" --arg registry "$registry_tag" --arg peer "$registry_peer" '
        select(.BackendState == "Running" and .Self.Tags == [$runner]) |
        [.Peer[] | select(.DNSName == $peer and .Tags == [$registry]) |
         .TailscaleIPs[] | select(test("^[0-9]+(\\.[0-9]+){3}$"))] |
        select(length == 1) | .[0]' |
      python3 -c 'import ipaddress, sys
ip = ipaddress.IPv4Address(sys.stdin.read().strip())
if ip not in ipaddress.IPv4Network("100.64.0.0/10"):
    sys.exit(1)
print(ip)' 2>/dev/null
  )"; then
    echo "::error::The exact private registry peer and runner tags were not verified."
    return 1
  fi

  if grep -Eiq '(^|[[:space:]])harbor\.stinkyboi\.com([[:space:]]|$)' /etc/hosts; then
    echo "::error::The runner already has a Harbor hosts entry."
    return 1
  fi
  cp /etc/hosts "$state_dir/hosts.original"
  {
    cat "$state_dir/hosts.original"
    printf '\n%s %s\n' "$registry_ip" "$harbor"
  } >"$state_dir/hosts.expected"
  sudo sh -c 'cat "$1" > /etc/hosts' runner "$state_dir/hosts.expected"
  test "$(getent ahostsv4 "$harbor" | awk '{print $1}' | sort -u)" = "$registry_ip"
}

leave() {
  local result=0
  if [[ -f "$state_dir/hosts.expected" ]]; then
    if cmp -s /etc/hosts "$state_dir/hosts.expected"; then
      sudo sh -c 'cat "$1" > /etc/hosts' runner "$state_dir/hosts.original" || result=1
    elif ! cmp -s /etc/hosts "$state_dir/hosts.original"; then
      echo "::error::Cannot safely restore the runner hosts file after an unexpected change."
      result=1
    fi
  fi
  if [[ -S "$socket" ]]; then
    sudo timeout 20 "$bin/tailscale" --socket="$socket" logout >/dev/null 2>&1 || result=1
  fi
  if [[ -f "$state_dir/daemon-attempted" ]]; then
    sudo systemctl stop "$unit" >/dev/null 2>&1 || result=1
    sudo "$bin/tailscaled" --cleanup >/dev/null 2>&1 || result=1
  fi
  sudo rm -rf -- "$state_dir" || result=1
  return "$result"
}

case "${1:-}" in
  join) join ;;
  leave) leave ;;
  *) exit 2 ;;
esac
