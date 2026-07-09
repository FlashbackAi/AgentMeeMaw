#!/usr/bin/env bash
#
# deploy.sh — one-shot deploy for the Flashback agent service on EC2.
#
#   git pull -> pip install -> (optionally) run migrations -> restart units -> status
#
# Fast by default: the package is installed editable (`pip install -e .`), so
# `git pull` + restart picks up code changes with no reinstall. This script
# only runs `pip install -e .` when it sees pyproject.toml change in the pull
# (i.e. dependencies may have changed) -- or when you force it. If it detects
# a NON-editable install (flashback importing from site-packages instead of
# the checkout), it converts it automatically: a frozen install would make
# `git pull` + restart silently deploy nothing (new routes 404).
#
# Usage (run from anywhere on the box; defaults match the prod install):
#   sudo bash /opt/AgentMeeMaw/scripts/deploy.sh            # pull + (deps-only)install + ask-migrate + restart
#   sudo bash /opt/AgentMeeMaw/scripts/deploy.sh --migrate  # run pending migrations without asking
#   sudo bash /opt/AgentMeeMaw/scripts/deploy.sh --no-migrate
#   sudo bash /opt/AgentMeeMaw/scripts/deploy.sh --no-pull  # skip git pull (deploy current tree)
#   sudo bash /opt/AgentMeeMaw/scripts/deploy.sh --install  # force `pip install -e .` (e.g. first-time setup)
#   sudo bash /opt/AgentMeeMaw/scripts/deploy.sh --skip-install
#
# Env overrides: APP_DIR, ENV_FILE, VENV
#
# The whole body runs inside main() so bash parses the entire file before
# executing — a `git pull` that rewrites this script mid-run can't corrupt it.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/AgentMeeMaw}"
ENV_FILE="${ENV_FILE:-/etc/flashback-agent.env}"
VENV="${VENV:-$APP_DIR/.venv}"

# systemd units, in restart order (API first so it picks up new env fastest).
SERVICES=(
  flashback-agent-api
  flashback-agent-worker@embedding
  flashback-agent-worker@extraction
  flashback-agent-worker@thread_detector
  flashback-agent-worker@trait_synthesizer
  flashback-agent-worker@profile_summary
  flashback-agent-worker@tribute_render
  flashback-agent-worker@storybook_render
  flashback-agent-producers-per-session
  flashback-agent-producers-weekly
)

# --- tiny output helpers ----------------------------------------------------
c_blue=$'\033[1;34m'; c_grn=$'\033[1;32m'; c_yel=$'\033[1;33m'; c_red=$'\033[1;31m'; c_rst=$'\033[0m'
step() { printf '\n%s==>%s %s\n' "$c_blue" "$c_rst" "$*"; }
ok()   { printf '%s  ok%s %s\n'  "$c_grn"  "$c_rst" "$*"; }
warn() { printf '%s warn%s %s\n' "$c_yel"  "$c_rst" "$*"; }
die()  { printf '%s fail%s %s\n' "$c_red"  "$c_rst" "$*" >&2; exit 1; }

main() {
  local DO_PULL=1 INSTALL_MODE="auto" MIGRATE_MODE="ask"   # ask|yes|no ; auto|yes|no
  local before="" after="" changed=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --migrate)      MIGRATE_MODE="yes" ;;
      --no-migrate)   MIGRATE_MODE="no" ;;
      --no-pull)      DO_PULL=0 ;;
      --install)      INSTALL_MODE="yes" ;;
      --skip-install) INSTALL_MODE="no" ;;
      -h|--help)      grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
      *)              die "unknown arg: $1 (try --help)" ;;
    esac
    shift
  done

  # sudo only if we aren't already root.
  local SUDO=""; [[ $EUID -ne 0 ]] && SUDO="sudo"

  [[ -d "$APP_DIR" ]]   || die "APP_DIR not found: $APP_DIR"
  [[ -f "$ENV_FILE" ]]  || die "ENV_FILE not found: $ENV_FILE"
  [[ -x "$VENV/bin/python" ]] || die "venv python not found: $VENV/bin/python"
  cd "$APP_DIR"

  # --- 1. git pull --------------------------------------------------------
  if [[ $DO_PULL -eq 1 ]]; then
    step "git pull ($APP_DIR)"
    before="$(git rev-parse HEAD)"
    git pull --ff-only
    after="$(git rev-parse HEAD)"
    if [[ "$before" == "$after" ]]; then
      ok "already up to date ($(git rev-parse --short HEAD))"
    else
      changed="$(git diff --name-only "$before" "$after")"
      ok "$(git rev-parse --short "$before") -> $(git rev-parse --short "$after") ($(echo "$changed" | grep -c .) files)"
    fi
  else
    warn "skipping git pull (--no-pull); HEAD=$(git rev-parse --short HEAD)"
  fi

  # --- 2. pip install (editable; deps only) -------------------------------
  # The package is installed editable, so code changes need NO reinstall --
  # only dependency changes do. Auto-install only when pyproject.toml moved.
  local do_install="no"
  case "$INSTALL_MODE" in
    yes) do_install="yes" ;;
    no)  warn "skipping pip install (--skip-install)" ;;
    auto)
      # A non-editable install serves a frozen site-packages copy of the
      # package -- git pull + restart would deploy nothing. Convert it.
      local pkg_dir
      pkg_dir="$("$VENV/bin/python" -c 'import flashback, os; print(os.path.dirname(os.path.abspath(flashback.__file__)))' 2>/dev/null || true)"
      if [[ "$pkg_dir" != "$APP_DIR/src/flashback" ]]; then
        do_install="yes"
        step "non-editable install detected (flashback imports from ${pkg_dir:-nowhere}) -> converting with pip install -e ."
      elif echo "$changed" | grep -qE '(^|/)pyproject\.toml$'; then
        do_install="yes"; step "pyproject.toml changed -> reinstalling deps"
      else
        ok "editable install, no dependency changes; skipping pip install (restart picks up code)"
      fi
      ;;
  esac
  if [[ "$do_install" == "yes" ]]; then
    step "pip install -e . (into $VENV)"
    "$VENV/bin/pip" install -e . --quiet
    ok "dependencies installed (editable)"
  fi

  # --- 3. migrations ------------------------------------------------------
  # Run inside a subshell so the sourced env doesn't leak into systemctl.
  step "checking for pending migrations"
  local pending
  pending="$( set -a; source "$ENV_FILE"; set +a; "$VENV/bin/python" scripts/migrate.py --dry-run )"

  if [[ -z "${pending//[[:space:]]/}" ]]; then
    ok "no pending migrations"
  else
    printf '%spending:%s\n%s\n' "$c_yel" "$c_rst" "$pending"
    local run_it="no"
    case "$MIGRATE_MODE" in
      yes) run_it="yes" ;;
      no)  warn "skipping migrations (--no-migrate)" ;;
      ask)
        if [[ -t 0 ]]; then
          read -r -p "Run these migrations now? [y/N] " ans
          [[ "$ans" =~ ^[Yy]$ ]] && run_it="yes"
        else
          warn "non-interactive shell and no --migrate/--no-migrate; skipping migrations"
        fi
        ;;
    esac
    if [[ "$run_it" == "yes" ]]; then
      step "running migrations"
      ( set -a; source "$ENV_FILE"; set +a; "$VENV/bin/python" scripts/migrate.py )
      ok "migrations applied"
    fi
  fi

  # --- 4. restart units ---------------------------------------------------
  step "restarting services"
  local failed=()
  for unit in "${SERVICES[@]}"; do
    if $SUDO systemctl restart "$unit" 2>/dev/null; then
      ok "restarted $unit"
    else
      warn "could not restart $unit (not installed/enabled?)"
      failed+=("$unit")
    fi
  done

  # --- 5. status ----------------------------------------------------------
  step "status (flashback-agent-*)"
  $SUDO systemctl --no-pager --no-legend list-units 'flashback-agent-*' || true
  echo
  $SUDO systemctl status flashback-agent-api --no-pager -l | head -8 || true

  echo
  if [[ ${#failed[@]} -gt 0 ]]; then
    warn "deploy finished with ${#failed[@]} unit(s) needing attention: ${failed[*]}"
    warn "if flashback-agent-worker@tribute_render or @storybook_render is new, create its SQS_MAX_MESSAGES=1 drop-in + enable it (see docs/ec2-deploy.md), then re-run."
  else
    ok "deploy complete — all units restarted"
  fi
}

main "$@"
