#!/usr/bin/env bash
# Sets up the WSL / Linux side (mt5_client + mt5_cli.py).
#
#   ./install.sh [--dev] [--test] [--features LIST] [--yes] [--allow-sdist]
#
# The client itself needs no packages (standard library only), so a plain run
# only checks Python and that `import mt5_client` works. Optional extras and dev
# tools go into ./.venv - never into the system Python.
#
# It never asks for or writes the API key. Copy the value of MT5_BRIDGE_API_KEY
# from the Windows .env into your shell yourself.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

DEV=0 TEST=0 YES=0 SDIST=0 ACCEPT=0 FEATURES=""
usage() {
  cat <<'EOF'
install.sh [options]
  --dev             create .venv and install requirements-dev.txt (pytest, ruff, mypy)
  --test            run the test suite (implies --dev)
  --features LIST   optional groups from requirements-features.txt:
                    comma list of numbers, "all" or "none" (no option = ask; --yes = none)
  --accept-policy   I have read POLICY.md and accept it (for unattended installs)
  --yes             never ask questions about extras (the policy is still required)
  --allow-sdist     let pip build source packages (default: wheels only)
  --help            this text
EOF
}
while [ $# -gt 0 ]; do
  case "$1" in
    --dev) DEV=1 ;;
    --test) TEST=1; DEV=1 ;;
    --yes) YES=1 ;;
    --allow-sdist) SDIST=1 ;;
    --accept-policy) ACCEPT=1 ;;
    --features) shift; [ $# -gt 0 ] || { echo "ERROR: --features needs a value" >&2; exit 2; }; FEATURES="$1" ;;
    --help|-h) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

say()  { printf '==> %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# ---- policy gate: nothing is installed until POLICY.md is accepted
POLICY_FILE="../POLICY.md"
POLICY_RECORD=".policy-accepted"
accept_policy() {
  [ -f "$POLICY_FILE" ] || { printf 'ERROR: %s not found - use a complete checkout.\n' "$POLICY_FILE" >&2; exit 5; }
  local hash method="" answer
  hash="$(printf '%s' "$(tr -d '\r' < "$POLICY_FILE")" | sha256sum | cut -d' ' -f1)"
  if [ -f "$POLICY_RECORD" ] && grep -qx "policy_sha256=$hash" "$POLICY_RECORD"; then return 0; fi
  if [ "$ACCEPT" -eq 1 ]; then
    method="flag"
  elif [ -t 0 ]; then
    cat "$POLICY_FILE"
    echo
    printf 'Policy sha256 %s...  (Thai reading copy: POLICY.th.md)\n' "${hash:0:16}"
    for _try in 1 2 3; do
      read -r -p "Do you accept this policy?  Y = accept and install,  N = decline (Enter also declines)  [y/N]: " answer || answer=""
      answer="$(printf '%s' "$answer" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | tr '[:upper:]' '[:lower:]')"
      case "$answer" in
        y|yes|"i accept") method="interactive"; break ;;
        ""|n|no|"i not accept"|"i do not accept"|decline|cancel|quit|exit)
          echo "You declined. Nothing was installed." >&2; exit 5 ;;
      esac
      echo "Please answer Y or N." >&2
    done
    [ "$method" = "interactive" ] || { echo "No valid answer. Nothing was installed." >&2; exit 5; }
  else
    echo "ERROR: policy not accepted. Read POLICY.md, then pass --accept-policy for unattended installs." >&2
    exit 5
  fi
  printf 'policy_sha256=%s\naccepted_utc=%s\naccepted_by=%s\nmethod=%s\n' \
    "$hash" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${USER:-unknown}" "$method" > "$POLICY_RECORD"
}
accept_policy

command -v python3 >/dev/null 2>&1 || die "python3 not found. Install it (Ubuntu/Debian: sudo apt install python3 python3-venv)."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "Python 3.11+ is required (found $(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])'))."

say "Checking the client imports"
python3 -c 'import mt5_client' || die "import mt5_client failed - run this script from the linux-side folder of an intact checkout."
echo "    import mt5_client ok"

# One tiny stdlib helper does all requirement-file parsing so the whitelist
# lives in a single place: only "name[extras] <op> version" lines are allowed;
# --index-url, URLs, -e and other -r files are rejected.
helper() {
python3 - "$@" <<'PY'
import re, sys
PAT = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?'
                 r'(\s*(==|>=|<=|~=|!=|>|<)\s*[0-9][A-Za-z0-9.*+!_-]*'
                 r'(\s*,\s*(==|>=|<=|~=|!=|>|<)\s*[0-9][A-Za-z0-9.*+!_-]*)*)?$')
HDR = re.compile(r'^#\s*--\s*(?P<t>.+?)\s*-{2,}\s*$')

def spec(line):
    text = re.sub(r'\s+#.*$', '', line).strip()
    if not text or text.startswith('#') or text == '-r requirements.txt':
        return None
    if not PAT.match(text):
        sys.exit('unsupported line in a requirements file: %s' % text)
    return text

mode = sys.argv[1]
if mode == 'specs':
    for line in open(sys.argv[2], encoding='utf-8'):
        s = spec(line)
        if s:
            print(s)
elif mode == 'groups':
    groups, cur = [], None
    for line in open(sys.argv[2], encoding='utf-8'):
        m = HDR.match(line.rstrip('\n'))
        if m:
            cur = [m.group('t'), []]
            groups.append(cur)
            continue
        s = spec(line)
        if s is None:
            continue
        if cur is None:
            sys.exit('packages before the first group header')
        cur[1].append(s)
    n = 0
    for title, specs in groups:
        if specs:
            n += 1
            print('%d\t%s\t%s' % (n, title, ', '.join(specs)))
elif mode == 'select':
    text, count = sys.argv[2].strip().upper(), int(sys.argv[3])
    dev, keys = False, []
    if text in ('', 'NONE'):
        pass
    elif text == 'ALL':
        keys = list(range(1, count + 1))
    else:
        for part in text.split(','):
            p = part.strip()
            if p == 'D':
                dev = True
            elif p.isdigit() and 1 <= int(p) <= count:
                if int(p) not in keys:
                    keys.append(int(p))
            else:
                sys.exit(1)
    print('D' if dev else '-', ' '.join(map(str, keys)))
PY
}

# ---- choose optional groups
FEATURES_FILE="requirements-features.txt"
GROUPS_TSV=""
if [ -f "$FEATURES_FILE" ]; then
  GROUPS_TSV="$(helper groups "$FEATURES_FILE")" || { warn "could not read $FEATURES_FILE; optional groups skipped"; GROUPS_TSV=""; }
fi
NGROUPS=0
[ -n "$GROUPS_TSV" ] && NGROUPS="$(printf '%s\n' "$GROUPS_TSV" | wc -l | tr -d ' ')"

SEL_DEV="-" SEL_KEYS=""
choose() {  # $1 = text
  local out
  out="$(helper select "$1" "$NGROUPS")" || return 1
  SEL_DEV="${out%% *}"
  SEL_KEYS="${out#* }"
}
if [ -n "$FEATURES" ]; then
  choose "$FEATURES" || die "invalid --features '$FEATURES' ($NGROUPS optional group(s) available; use none, all, or numbers such as 1,2)"
elif [ "$YES" -eq 0 ] && [ -t 0 ] && { [ "$NGROUPS" -gt 0 ] || [ "$DEV" -eq 0 ]; }; then
  echo
  echo "Optional extras (the client itself needs none) - numbers separated by commas, \"all\", or Enter for none:"
  [ "$DEV" -eq 0 ] && echo "  D   Dev tools   pytest, ruff, mypy"
  [ -n "$GROUPS_TSV" ] && printf '%s\n' "$GROUPS_TSV" | awk -F'\t' '{printf "  %s   %s   %s\n", $1, $2, $3}'
  while true; do
    read -r -p "Install which extras: " ANSWER || ANSWER=""
    choose "$ANSWER" && break
    warn "Not understood. Use e.g. 1,3,D or all or Enter."
  done
fi
[ "$SEL_DEV" = "D" ] && DEV=1

# ---- venv + pip (only when something needs installing)
FAILED=()
DONE=()
if [ "$DEV" -eq 1 ] || [ -n "${SEL_KEYS// /}" ]; then
  python3 -c 'import venv, ensurepip' 2>/dev/null \
    || die "python3-venv is missing. Ubuntu/Debian: sudo apt install python3-venv"
  if [ ! -x .venv/bin/python ]; then
    say "Creating .venv"
    python3 -m venv .venv
  fi
  TMP="$(mktemp -d)"; chmod 700 "$TMP"
  trap 'rm -rf "$TMP"' EXIT
  PIPFLAGS=(--disable-pip-version-check)
  [ "$SDIST" -eq 1 ] || PIPFLAGS+=(--only-binary ':all:')

  pip_install() {  # $1 = label, $2 = requirements file with validated specs
    if .venv/bin/python -m pip install "${PIPFLAGS[@]}" -r "$2"; then DONE+=("$1"); else FAILED+=("$1"); return 1; fi
  }
  if [ "$DEV" -eq 1 ]; then
    say "Installing dev tools (requirements-dev.txt)"
    helper specs requirements-dev.txt > "$TMP/dev.txt"
    pip_install dev "$TMP/dev.txt" || { [ "$TEST" -eq 1 ] && die "dev tools failed; cannot run tests"; true; }
  fi
  for k in $SEL_KEYS; do
    say "Installing optional group $k"
    printf '%s\n' "$GROUPS_TSV" | awk -F'\t' -v k="$k" '$1==k {print $3}' | tr ',' '\n' | sed 's/^ *//;s/ *$//' > "$TMP/g$k.txt"
    pip_install "group $k" "$TMP/g$k.txt" || true
  done
fi

if [ "$TEST" -eq 1 ]; then
  say "Running the test suite"
  .venv/bin/python -m pytest -q || FAILED+=("tests")
fi

echo
echo "------------------------------------------------------------"
[ ${#DONE[@]} -gt 0 ] && echo "Installed: ${DONE[*]}"
[ ${#FAILED[@]} -gt 0 ] && echo "FAILED:    ${FAILED[*]}"
cat <<'EOF'

Next steps:
  1. Put the same key as the Windows .env into your shell (do not paste it into scripts or commit it):
       export MT5_BRIDGE_API_KEY="<value of MT5_BRIDGE_API_KEY in windows-side/.env>"
  2. Check the connection:   python3 mt5_cli.py health
  If that cannot connect: WSL2 in NAT mode cannot reach a bridge bound to 127.0.0.1 on Windows.
  Set MT5_BRIDGE_URL, or use mirrored networking - see mt5_client/config.py and SECURITY.md
  (read its network-exposure section before binding the bridge to anything but 127.0.0.1).
EOF
[ ${#FAILED[@]} -eq 0 ] || exit 2
