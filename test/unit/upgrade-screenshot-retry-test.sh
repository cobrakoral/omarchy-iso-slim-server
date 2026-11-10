#!/bin/bash
set -euo pipefail

# Offline regression test for screenshot retry logic in .github/workflows/upgrade-test.yml:
# Extracts the actual 'Commit the pictures' run step from the workflow file,
# and verifies behavior against a local bare Git remote under two conditions:
#   1. Ordinary first push.
#   2. Simulated lost ack (push succeeds on remote, but client gets exit code 1),
#      verifying that the next retry cleanly commits and pushes without failing on "nothing to commit".

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$repo_root/.github/workflows/upgrade-test.yml"

[ -f "$workflow" ] || { echo "Workflow not found: $workflow" >&2; exit 1; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

real_git=$(command -v git)

# Extract the 'Commit the pictures' step from the workflow using python stdlib and dedent
step_script="$tmp/commit-pictures.sh"
python3 -c "
import sys, textwrap

with open(sys.argv[1]) as f:
    content = f.read()

part = content.split('- name: Commit the pictures')[1].split('run: |')[1]
step = textwrap.dedent(part.split('\n      - ')[0])
with open(sys.argv[2], 'w') as out:
    out.write(step)
" "$workflow" "$step_script"

# Git wrapper capable of simulating lost acknowledgment on the first push
mkdir -p "$tmp/bin"
cat << EOF > "$tmp/bin/git"
#!/bin/bash
if [ "\$1" = "push" ] && [ -n "\${SIMULATE_LOST_ACK:-}" ] && [ ! -f "\$SIMULATE_LOST_ACK" ]; then
  touch "\$SIMULATE_LOST_ACK"
  "$real_git" "\$@"
  exit 1
fi
exec "$real_git" "\$@"
EOF
chmod +x "$tmp/bin/git"

# Helper to execute the extracted workflow step against a local bare remote
run_workflow_step() {
  local target_remote="$1"
  local lost_ack_flag="${2:-}"
  local work_dir="$tmp/work-$$-$RANDOM"

  mkdir -p "$work_dir/evidence"
  echo "before-png-content" > "$work_dir/evidence/before.png"
  echo "after-png-content" > "$work_dir/evidence/after.png"
  touch "$work_dir/github_output"

  # Replace only the URL assignment for offline testing
  sed "s|^url=.*|url=\"$target_remote\"|" "$step_script" > "$work_dir/step.sh"

  (
    cd "$work_dir"
    export PATH="$tmp/bin:$PATH"
    export GITHUB_RUN_ID="1234"
    export GITHUB_RUN_ATTEMPT="1"
    export SHOTS_BRANCH="upgrade-test-screenshots"
    export FROM="1.0.0"
    export TO="2.0.0"
    export GITHUB_REPOSITORY="omacom/omarchy"
    export GITHUB_OUTPUT="$work_dir/github_output"
    if [ -n "$lost_ack_flag" ]; then
      export SIMULATE_LOST_ACK="$lost_ack_flag"
    fi
    bash step.sh
  )
}

# Test 1: Ordinary first push
bare1="$tmp/bare1.git"
"$real_git" init --bare -q "$bare1"
run_workflow_step "$bare1" ""

verify1="$tmp/verify1"
"$real_git" clone -q -b upgrade-test-screenshots "$bare1" "$verify1"
[ -s "$verify1/1234-1/before.png" ] || { echo "Test 1: before.png missing on remote" >&2; exit 1; }
[ -s "$verify1/1234-1/after.png" ] || { echo "Test 1: after.png missing on remote" >&2; exit 1; }
commits1=$("$real_git" -C "$verify1" rev-list --count HEAD)
[ "$commits1" -eq 1 ] || { echo "Test 1: expected 1 commit, got $commits1" >&2; exit 1; }

# Test 2: Push with simulated lost acknowledgment on first attempt
bare2="$tmp/bare2.git"
"$real_git" init --bare -q "$bare2"
ack_flag="$tmp/ack_lost"
run_workflow_step "$bare2" "$ack_flag"
[ -f "$ack_flag" ] || { echo "Test 2: wrapper did not intercept push" >&2; exit 1; }

verify2="$tmp/verify2"
"$real_git" clone -q -b upgrade-test-screenshots "$bare2" "$verify2"
[ -s "$verify2/1234-1/before.png" ] || { echo "Test 2: before.png missing on remote" >&2; exit 1; }
[ -s "$verify2/1234-1/after.png" ] || { echo "Test 2: after.png missing on remote" >&2; exit 1; }
commits2=$("$real_git" -C "$verify2" rev-list --count HEAD)
[ "$commits2" -eq 1 ] || { echo "Test 2: expected 1 commit, got $commits2" >&2; exit 1; }

echo "All tests passed."
