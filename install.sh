#!/usr/bin/env bash
# ============================================================================
# fossilrepo -- Omnibus Installer
#
# Installs fossilrepo on any Linux box. Supports two modes:
#   - Docker: full stack via docker compose (recommended)
#   - Bare Metal: native install with systemd services
#
# Usage:
#   curl -sSL https://get.fossilrepo.dev | bash
#   -- or --
#   ./install.sh --docker --domain fossil.example.com --ssl
#
# https://github.com/ConflictHQ/fossilrepo
# MIT License
# ============================================================================

set -euo pipefail

# Ensure HOME is set (SSM/cloud-init may not set it)
export HOME="${HOME:-/root}"

# ============================================================================
# Section 1: Constants + Version Pins
# ============================================================================

readonly INSTALLER_VERSION="1.0.0"
readonly FOSSILREPO_VERSION="0.1.0"

readonly FOSSIL_VERSION="2.24"
readonly LITESTREAM_VERSION="0.3.13"
readonly CADDY_VERSION="2.9"
readonly PYTHON_VERSION="3.12"
readonly POSTGRES_VERSION="16"
readonly REDIS_VERSION="7"

readonly REPO_URL="https://github.com/ConflictHQ/fossilrepo.git"
readonly DEFAULT_PREFIX="/opt/fossilrepo"
readonly DATA_DIR="/data"
readonly LOG_DIR="/var/log/fossilrepo"
readonly CADDY_DOWNLOAD_BASE="https://caddyserver.com/api/download"
readonly LITESTREAM_DOWNLOAD_BASE="https://github.com/benbjohnson/litestream/releases/download"

# Globals -- set by arg parser, interactive TUI, or defaults
OPT_MODE=""                         # docker | bare-metal
OPT_DOMAIN="localhost"
OPT_SSL="false"
OPT_PREFIX="$DEFAULT_PREFIX"
OPT_PORT="8000"
OPT_DB_NAME="fossilrepo"
OPT_DB_USER="dbadmin"
OPT_DB_PASSWORD=""
OPT_ADMIN_USER="admin"
OPT_ADMIN_EMAIL=""
OPT_ADMIN_PASSWORD=""
OPT_S3_BUCKET=""
OPT_S3_REGION=""
OPT_S3_ENDPOINT=""
OPT_S3_ACCESS_KEY=""
OPT_S3_SECRET_KEY=""
OPT_CONFIG_FILE=""
OPT_YES="false"
OPT_VERBOSE="false"

# Detected at runtime
OS_ID=""
OS_VERSION=""
OS_ARCH=""
PKG_MANAGER=""

# Generated secrets
GEN_SECRET_KEY=""
GEN_DB_PASSWORD=""
GEN_ADMIN_PASSWORD=""

# Color codes -- set by _color_init
_C_RESET=""
_C_RED=""
_C_GREEN=""
_C_YELLOW=""
_C_BLUE=""
_C_CYAN=""
_C_BOLD=""

# ============================================================================
# Section 2: Logging
# ============================================================================

_supports_color() {
    # Check if stdout is a terminal and TERM is not dumb
    [[ -t 1 ]] && [[ "${TERM:-dumb}" != "dumb" ]] && return 0
    # Also support NO_COLOR convention
    [[ -z "${NO_COLOR:-}" ]] || return 1
    return 1
}

_color_init() {
    if _supports_color; then
        _C_RESET='\033[0m'
        _C_RED='\033[0;31m'
        _C_GREEN='\033[0;32m'
        _C_YELLOW='\033[0;33m'
        _C_BLUE='\033[0;34m'
        _C_CYAN='\033[0;36m'
        _C_BOLD='\033[1m'
    fi
}

log_info()  { printf "${_C_BLUE}[INFO]${_C_RESET}  %s\n" "$*"; }
log_ok()    { printf "${_C_GREEN}[  OK]${_C_RESET}  %s\n" "$*"; }
log_warn()  { printf "${_C_YELLOW}[WARN]${_C_RESET}  %s\n" "$*" >&2; }
log_error() { printf "${_C_RED}[ ERR]${_C_RESET}  %s\n" "$*" >&2; }
log_step()  { printf "\n${_C_CYAN}${_C_BOLD}==> %s${_C_RESET}\n" "$*"; }

die() {
    log_error "$@"
    exit 1
}

verbose() {
    [[ "$OPT_VERBOSE" == "true" ]] && log_info "$@"
    return 0
}

# ============================================================================
# Section 3: Utilities
# ============================================================================

generate_password() {
    # 32-char alphanumeric password -- no special chars to avoid escaping issues
    local length="${1:-32}"
    if command -v openssl &>/dev/null; then
        openssl rand -base64 48 | tr -dc 'a-zA-Z0-9' | head -c "$length"
    elif [[ -r /dev/urandom ]]; then
        tr -dc 'a-zA-Z0-9' < /dev/urandom | head -c "$length"
    else
        die "Cannot generate random password: no openssl or /dev/urandom"
    fi
}

generate_secret_key() {
    # 50-char Django secret key
    if command -v openssl &>/dev/null; then
        openssl rand -base64 72 | tr -dc 'a-zA-Z0-9!@#$%^&*(-_=+)' | head -c 50
    elif [[ -r /dev/urandom ]]; then
        tr -dc 'a-zA-Z0-9!@#$%^&*(-_=+)' < /dev/urandom | head -c 50
    else
        die "Cannot generate secret key: no openssl or /dev/urandom"
    fi
}

command_exists() {
    command -v "$1" &>/dev/null
}

version_gte() {
    # Returns 0 if $1 >= $2 (dot-separated version comparison)
    local IFS=.
    local i ver1=($1) ver2=($2)
    for ((i = 0; i < ${#ver2[@]}; i++)); do
        local v1="${ver1[i]:-0}"
        local v2="${ver2[i]:-0}"
        if ((v1 > v2)); then return 0; fi
        if ((v1 < v2)); then return 1; fi
    done
    return 0
}

require_root() {
    if [[ $EUID -ne 0 ]]; then
        die "This installer must be run as root. Use: sudo ./install.sh"
    fi
}

confirm() {
    if [[ "$OPT_YES" == "true" ]]; then
        return 0
    fi
    local prompt="${1:-Continue?}"
    local reply
    printf "${_C_BOLD}%s [y/N] ${_C_RESET}" "$prompt"
    read -r reply
    case "$reply" in
        [yY][eE][sS]|[yY]) return 0 ;;
        *) die "Aborted by user." ;;
    esac
}

backup_file() {
    local file="$1"
    if [[ -f "$file" ]]; then
        local backup="${file}.bak.$(date +%Y%m%d%H%M%S)"
        cp "$file" "$backup"
        verbose "Backed up $file -> $backup"
    fi
}

write_file() {
    # Write content to a file, creating parent dirs and backing up existing files.
    # Usage: write_file <path> <content> [mode]
    local path="$1"
    local content="$2"
    local mode="${3:-0644}"

    mkdir -p "$(dirname "$path")"
    backup_file "$path"
    printf '%s\n' "$content" > "$path"
    chmod "$mode" "$path"
    verbose "Wrote $path (mode $mode)"
}

retry_command() {
    # Retry a command up to N times with a delay between attempts
    local max_attempts="${1:-3}"
    local delay="${2:-5}"
    shift 2
    local attempt=1
    while [[ $attempt -le $max_attempts ]]; do
        if "$@"; then
            return 0
        fi
        log_warn "Command failed (attempt $attempt/$max_attempts): $*"
        ((attempt++))
        sleep "$delay"
    done
    return 1
}

# ============================================================================
# Section 4: OS Detection
# ============================================================================

detect_os() {
    log_step "Detecting operating system"

    if [[ ! -f /etc/os-release ]]; then
        die "Cannot detect OS: /etc/os-release not found. This installer requires a modern Linux distribution."
    fi

    # shellcheck source=/dev/null
    . /etc/os-release

    OS_ID="${ID:-unknown}"
    OS_VERSION="${VERSION_ID:-0}"

    case "$OS_ID" in
        debian)
            PKG_MANAGER="apt"
            ;;
        ubuntu)
            PKG_MANAGER="apt"
            ;;
        rhel|centos|rocky|almalinux)
            OS_ID="rhel"
            PKG_MANAGER="dnf"
            ;;
        amzn)
            PKG_MANAGER="dnf"
            ;;
        fedora)
            OS_ID="rhel"
            PKG_MANAGER="dnf"
            ;;
        alpine)
            PKG_MANAGER="apk"
            log_warn "Alpine Linux detected. Only Docker mode is supported on Alpine."
            if [[ "$OPT_MODE" == "bare-metal" ]]; then
                die "Bare metal installation is not supported on Alpine Linux."
            fi
            OPT_MODE="docker"
            ;;
        *)
            die "Unsupported OS: $OS_ID. Supported: debian, ubuntu, rhel/centos/rocky/alma, fedora, amzn, alpine."
            ;;
    esac

    # Detect architecture
    local machine
    machine="$(uname -m)"
    case "$machine" in
        x86_64|amd64)  OS_ARCH="amd64" ;;
        aarch64|arm64) OS_ARCH="arm64" ;;
        *)             die "Unsupported architecture: $machine. Supported: amd64, arm64." ;;
    esac

    log_ok "OS: $OS_ID $OS_VERSION ($OS_ARCH), package manager: $PKG_MANAGER"
}

# ============================================================================
# Section 5: YAML Config Parser
# ============================================================================

parse_config_file() {
    local config_file="$1"

    if [[ ! -f "$config_file" ]]; then
        die "Config file not found: $config_file"
    fi

    log_info "Loading config from $config_file"

    local current_section=""
    local line key value

    while IFS= read -r line || [[ -n "$line" ]]; do
        # Strip comments and trailing whitespace
        line="${line%%#*}"
        line="${line%"${line##*[![:space:]]}"}"

        # Skip empty lines
        [[ -z "$line" ]] && continue

        # Detect section (key followed by colon, no value, next lines indented)
        if [[ "$line" =~ ^([a-zA-Z_][a-zA-Z0-9_-]*):[[:space:]]*$ ]]; then
            current_section="${BASH_REMATCH[1]}"
            continue
        fi

        # Indented key:value under a section
        if [[ "$line" =~ ^[[:space:]]+([a-zA-Z_][a-zA-Z0-9_-]*):[[:space:]]*(.+)$ ]]; then
            key="${BASH_REMATCH[1]}"
            value="${BASH_REMATCH[2]}"
            # Strip quotes
            value="${value%\"}"
            value="${value#\"}"
            value="${value%\'}"
            value="${value#\'}"
            _set_config_value "${current_section}_${key}" "$value"
            continue
        fi

        # Top-level key: value
        if [[ "$line" =~ ^([a-zA-Z_][a-zA-Z0-9_-]*):[[:space:]]*(.+)$ ]]; then
            current_section=""
            key="${BASH_REMATCH[1]}"
            value="${BASH_REMATCH[2]}"
            value="${value%\"}"
            value="${value#\"}"
            value="${value%\'}"
            value="${value#\'}"
            _set_config_value "$key" "$value"
            continue
        fi
    done < "$config_file"
}

_set_config_value() {
    local key="$1"
    local value="$2"

    # Normalize key: lowercase, dashes to underscores
    key="${key,,}"
    key="${key//-/_}"

    case "$key" in
        mode)              OPT_MODE="$value" ;;
        domain)            OPT_DOMAIN="$value" ;;
        ssl)               OPT_SSL="$value" ;;
        prefix)            OPT_PREFIX="$value" ;;
        port)              OPT_PORT="$value" ;;
        db_name)           OPT_DB_NAME="$value" ;;
        db_user)           OPT_DB_USER="$value" ;;
        db_password)       OPT_DB_PASSWORD="$value" ;;
        admin_user)        OPT_ADMIN_USER="$value" ;;
        admin_email)       OPT_ADMIN_EMAIL="$value" ;;
        admin_password)    OPT_ADMIN_PASSWORD="$value" ;;
        s3_bucket)         OPT_S3_BUCKET="$value" ;;
        s3_region)         OPT_S3_REGION="$value" ;;
        s3_endpoint)       OPT_S3_ENDPOINT="$value" ;;
        s3_access_key)     OPT_S3_ACCESS_KEY="$value" ;;
        s3_secret_key)     OPT_S3_SECRET_KEY="$value" ;;
        *)                 verbose "Ignoring unknown config key: $key" ;;
    esac
}

# ============================================================================
# Section 6: Arg Parser
# ============================================================================

show_help() {
    cat <<'HELPTEXT'
fossilrepo installer -- deploy a full Fossil forge in one command.

USAGE
    install.sh [OPTIONS]
    install.sh --docker --domain fossil.example.com --ssl
    install.sh --bare-metal --domain fossil.example.com --config fossilrepo.yml
    install.sh                  # interactive mode

INSTALLATION MODE
    --docker                Docker Compose deployment (recommended)
    --bare-metal            Native install with systemd services

NETWORK
    --domain <fqdn>         Domain name (default: localhost)
    --ssl                   Enable automatic HTTPS via Caddy/Let's Encrypt
    --port <port>           Application port (default: 8000)

DATABASE
    --db-password <pass>    PostgreSQL password (auto-generated if omitted)

ADMIN ACCOUNT
    --admin-user <name>     Admin username (default: admin)
    --admin-email <email>   Admin email address
    --admin-password <pass> Admin password (auto-generated if omitted)

S3 BACKUP (LITESTREAM)
    --s3-bucket <name>      S3 bucket for Litestream replication
    --s3-region <region>    AWS region (default: us-east-1)
    --s3-endpoint <url>     S3-compatible endpoint (for MinIO etc.)
    --s3-access-key <key>   AWS access key ID
    --s3-secret-key <key>   AWS secret access key

GENERAL
    --prefix <path>         Install prefix (default: /opt/fossilrepo)
    --config <file>         Load options from YAML config file
    --yes                   Skip all confirmation prompts
    --verbose               Enable verbose output
    -h, --help              Show this help and exit
    --version               Show version and exit

EXAMPLES
    # Interactive guided install
    sudo ./install.sh

    # Docker with auto-SSL
    sudo ./install.sh --docker --domain fossil.example.com --ssl --yes

    # Bare metal with config file
    sudo ./install.sh --bare-metal --config /etc/fossilrepo/install.yml

    # Docker on localhost for testing
    sudo ./install.sh --docker --yes
HELPTEXT
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --docker)
                OPT_MODE="docker"
                shift
                ;;
            --bare-metal)
                OPT_MODE="bare-metal"
                shift
                ;;
            --domain)
                [[ -z "${2:-}" ]] && die "--domain requires a value"
                OPT_DOMAIN="$2"
                shift 2
                ;;
            --ssl)
                OPT_SSL="true"
                shift
                ;;
            --prefix)
                [[ -z "${2:-}" ]] && die "--prefix requires a value"
                OPT_PREFIX="$2"
                shift 2
                ;;
            --port)
                [[ -z "${2:-}" ]] && die "--port requires a value"
                OPT_PORT="$2"
                shift 2
                ;;
            --db-password)
                [[ -z "${2:-}" ]] && die "--db-password requires a value"
                OPT_DB_PASSWORD="$2"
                shift 2
                ;;
            --admin-user)
                [[ -z "${2:-}" ]] && die "--admin-user requires a value"
                OPT_ADMIN_USER="$2"
                shift 2
                ;;
            --admin-email)
                [[ -z "${2:-}" ]] && die "--admin-email requires a value"
                OPT_ADMIN_EMAIL="$2"
                shift 2
                ;;
            --admin-password)
                [[ -z "${2:-}" ]] && die "--admin-password requires a value"
                OPT_ADMIN_PASSWORD="$2"
                shift 2
                ;;
            --s3-bucket)
                [[ -z "${2:-}" ]] && die "--s3-bucket requires a value"
                OPT_S3_BUCKET="$2"
                shift 2
                ;;
            --s3-region)
                [[ -z "${2:-}" ]] && die "--s3-region requires a value"
                OPT_S3_REGION="$2"
                shift 2
                ;;
            --s3-endpoint)
                [[ -z "${2:-}" ]] && die "--s3-endpoint requires a value"
                OPT_S3_ENDPOINT="$2"
                shift 2
                ;;
            --s3-access-key)
                [[ -z "${2:-}" ]] && die "--s3-access-key requires a value"
                OPT_S3_ACCESS_KEY="$2"
                shift 2
                ;;
            --s3-secret-key)
                [[ -z "${2:-}" ]] && die "--s3-secret-key requires a value"
                OPT_S3_SECRET_KEY="$2"
                shift 2
                ;;
            --config)
                [[ -z "${2:-}" ]] && die "--config requires a value"
                OPT_CONFIG_FILE="$2"
                shift 2
                ;;
            --yes|-y)
                OPT_YES="true"
                shift
                ;;
            --verbose|-v)
                OPT_VERBOSE="true"
                shift
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            --version)
                echo "fossilrepo installer v${INSTALLER_VERSION} (fossilrepo v${FOSSILREPO_VERSION})"
                exit 0
                ;;
            *)
                die "Unknown option: $1 (use --help for usage)"
                ;;
        esac
    done

    # Load config file if specified (CLI args take precedence -- already set above)
    if [[ -n "$OPT_CONFIG_FILE" ]]; then
        # Save CLI-set values
        local saved_mode="$OPT_MODE"
        local saved_domain="$OPT_DOMAIN"
        local saved_ssl="$OPT_SSL"
        local saved_prefix="$OPT_PREFIX"
        local saved_port="$OPT_PORT"
        local saved_db_password="$OPT_DB_PASSWORD"
        local saved_admin_user="$OPT_ADMIN_USER"
        local saved_admin_email="$OPT_ADMIN_EMAIL"
        local saved_admin_password="$OPT_ADMIN_PASSWORD"
        local saved_s3_bucket="$OPT_S3_BUCKET"
        local saved_s3_region="$OPT_S3_REGION"

        parse_config_file "$OPT_CONFIG_FILE"

        # Restore CLI overrides (non-default values take precedence)
        [[ -n "$saved_mode" ]]           && OPT_MODE="$saved_mode"
        [[ "$saved_domain" != "localhost" ]] && OPT_DOMAIN="$saved_domain"
        [[ "$saved_ssl" == "true" ]]     && OPT_SSL="$saved_ssl"
        [[ "$saved_prefix" != "$DEFAULT_PREFIX" ]] && OPT_PREFIX="$saved_prefix"
        [[ "$saved_port" != "8000" ]]    && OPT_PORT="$saved_port"
        [[ -n "$saved_db_password" ]]    && OPT_DB_PASSWORD="$saved_db_password"
        [[ "$saved_admin_user" != "admin" ]] && OPT_ADMIN_USER="$saved_admin_user"
        [[ -n "$saved_admin_email" ]]    && OPT_ADMIN_EMAIL="$saved_admin_email"
        [[ -n "$saved_admin_password" ]] && OPT_ADMIN_PASSWORD="$saved_admin_password"
        [[ -n "$saved_s3_bucket" ]]      && OPT_S3_BUCKET="$saved_s3_bucket"
        [[ -n "$saved_s3_region" ]]      && OPT_S3_REGION="$saved_s3_region"
    fi
}

# ============================================================================
# Section 7: Interactive TUI
# ============================================================================

_print_banner() {
    printf "\n"
    printf "${_C_CYAN}${_C_BOLD}"
    cat <<'BANNER'
    __               _ __
   / _|___  ___ ___ (_) |_ __ ___ _ __   ___
  | |_/ _ \/ __/ __|| | | '__/ _ \ '_ \ / _ \
  |  _| (_) \__ \__ \| | | | |  __/ |_) | (_) |
  |_|  \___/|___/___/|_|_|_|  \___| .__/ \___/
                                   |_|
BANNER
    printf "${_C_RESET}"
    printf "  ${_C_BOLD}Omnibus Installer v${INSTALLER_VERSION}${_C_RESET}\n"
    printf "  Self-hosted Fossil forge -- one command, full stack.\n\n"
}

_prompt() {
    # Usage: _prompt "Prompt text" "default" VARNAME
    local prompt="$1"
    local default="$2"
    local varname="$3"
    local reply

    if [[ -n "$default" ]]; then
        printf "  ${_C_BOLD}%s${_C_RESET} [%s]: " "$prompt" "$default"
    else
        printf "  ${_C_BOLD}%s${_C_RESET}: " "$prompt"
    fi
    read -r reply
    reply="${reply:-$default}"
    eval "$varname=\"\$reply\""
}

_prompt_password() {
    local prompt="$1"
    local varname="$2"
    local reply

    printf "  ${_C_BOLD}%s${_C_RESET} (leave blank to auto-generate): " "$prompt"
    read -rs reply
    printf "\n"
    eval "$varname=\"\$reply\""
}

_prompt_choice() {
    # Usage: _prompt_choice "Prompt" "1" VARNAME "Option 1" "Option 2"
    local prompt="$1"
    local default="$2"
    local varname="$3"
    shift 3
    local -a options=("$@")
    local i reply

    printf "\n  ${_C_BOLD}%s${_C_RESET}\n" "$prompt"
    for i in "${!options[@]}"; do
        printf "    %d) %s\n" "$((i + 1))" "${options[$i]}"
    done
    printf "  Choice [%s]: " "$default"
    read -r reply
    reply="${reply:-$default}"

    if [[ "$reply" =~ ^[0-9]+$ ]] && ((reply >= 1 && reply <= ${#options[@]})); then
        eval "$varname=\"\$reply\""
    else
        eval "$varname=\"\$default\""
    fi
}

_prompt_yesno() {
    local prompt="$1"
    local default="$2"
    local varname="$3"
    local reply hint

    if [[ "$default" == "y" ]]; then hint="Y/n"; else hint="y/N"; fi
    printf "  ${_C_BOLD}%s${_C_RESET} [%s]: " "$prompt" "$hint"
    read -r reply
    reply="${reply:-$default}"
    case "$reply" in
        [yY][eE][sS]|[yY]) eval "$varname=true" ;;
        *)                  eval "$varname=false" ;;
    esac
}

run_interactive() {
    _print_banner

    printf "  ${_C_BLUE}Welcome to the fossilrepo installer.${_C_RESET}\n"
    printf "  This will guide you through setting up a self-hosted Fossil forge.\n\n"

    # Mode
    local mode_choice
    _prompt_choice "Installation mode" "1" mode_choice \
        "Docker (recommended -- everything runs in containers)" \
        "Bare Metal (native install with systemd services)"
    case "$mode_choice" in
        1) OPT_MODE="docker" ;;
        2) OPT_MODE="bare-metal" ;;
    esac

    # Domain
    _prompt "Domain name" "localhost" OPT_DOMAIN

    # SSL -- skip if localhost
    if [[ "$OPT_DOMAIN" != "localhost" && "$OPT_DOMAIN" != "127.0.0.1" ]]; then
        _prompt_yesno "Enable automatic HTTPS (Let's Encrypt)" "y" OPT_SSL
    else
        OPT_SSL="false"
        log_info "SSL skipped for localhost."
    fi

    # S3 backup
    local want_s3
    _prompt_yesno "Configure S3 backup (Litestream replication)" "n" want_s3
    if [[ "$want_s3" == "true" ]]; then
        _prompt "S3 bucket name" "" OPT_S3_BUCKET
        _prompt "S3 region" "us-east-1" OPT_S3_REGION
        _prompt "S3 endpoint (leave blank for AWS)" "" OPT_S3_ENDPOINT
        _prompt "AWS Access Key ID" "" OPT_S3_ACCESS_KEY
        _prompt_password "AWS Secret Access Key" OPT_S3_SECRET_KEY
    fi

    # Admin credentials
    printf "\n  ${_C_BOLD}Admin Account${_C_RESET}\n"
    _prompt "Admin username" "admin" OPT_ADMIN_USER
    _prompt "Admin email" "admin@${OPT_DOMAIN}" OPT_ADMIN_EMAIL
    _prompt_password "Admin password" OPT_ADMIN_PASSWORD

    # Summary
    printf "\n"
    printf "  ${_C_CYAN}${_C_BOLD}Installation Summary${_C_RESET}\n"
    printf "  %-20s %s\n" "Mode:" "$OPT_MODE"
    printf "  %-20s %s\n" "Domain:" "$OPT_DOMAIN"
    printf "  %-20s %s\n" "SSL:" "$OPT_SSL"
    printf "  %-20s %s\n" "Admin user:" "$OPT_ADMIN_USER"
    printf "  %-20s %s\n" "Admin email:" "$OPT_ADMIN_EMAIL"
    printf "  %-20s %s\n" "Admin password:" "$(if [[ -n "$OPT_ADMIN_PASSWORD" ]]; then echo '(set)'; else echo '(auto-generate)'; fi)"
    if [[ -n "$OPT_S3_BUCKET" ]]; then
        printf "  %-20s %s\n" "S3 bucket:" "$OPT_S3_BUCKET"
        printf "  %-20s %s\n" "S3 region:" "${OPT_S3_REGION:-us-east-1}"
    else
        printf "  %-20s %s\n" "S3 backup:" "disabled"
    fi
    printf "  %-20s %s\n" "Install prefix:" "$OPT_PREFIX"
    printf "\n"
}

# ============================================================================
# Section 8: Dependency Management
# ============================================================================

check_docker_deps() {
    log_step "Checking Docker dependencies"

    local missing=()

    if ! command_exists docker; then
        missing+=("docker")
    fi

    # Check for docker compose v2 (plugin)
    if command_exists docker; then
        if ! docker compose version &>/dev/null; then
            missing+=("docker-compose-plugin")
        fi
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_warn "Missing Docker dependencies: ${missing[*]}"
        return 1
    fi

    local compose_ver
    compose_ver="$(docker compose version --short 2>/dev/null || echo "0")"
    log_ok "Docker $(docker --version | awk '{print $3}' | tr -d ',') + Compose $compose_ver"
    return 0
}

check_bare_metal_deps() {
    log_step "Checking bare metal dependencies"

    local missing=()

    command_exists git       || missing+=("git")
    command_exists curl      || missing+=("curl")

    # Python 3.12+
    if command_exists python3; then
        local pyver
        pyver="$(python3 --version 2>&1 | awk '{print $2}')"
        if ! version_gte "$pyver" "$PYTHON_VERSION"; then
            missing+=("python${PYTHON_VERSION}")
        fi
    else
        missing+=("python${PYTHON_VERSION}")
    fi

    # PostgreSQL
    if command_exists psql; then
        local pgver
        pgver="$(psql --version | awk '{print $3}' | cut -d. -f1)"
        if ! version_gte "$pgver" "$POSTGRES_VERSION"; then
            missing+=("postgresql-${POSTGRES_VERSION}")
        fi
    else
        missing+=("postgresql-${POSTGRES_VERSION}")
    fi

    command_exists redis-server || missing+=("redis")

    # These are installed from source/binary -- check separately
    command_exists fossil || missing+=("fossil")
    command_exists caddy  || missing+=("caddy")

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_warn "Missing dependencies: ${missing[*]}"
        return 1
    fi

    log_ok "All bare metal dependencies present"
    return 0
}

install_docker_engine() {
    log_info "Installing Docker Engine..."

    case "$PKG_MANAGER" in
        apt)
            # Docker official GPG key and repo
            apt-get update -qq
            apt-get install -y -qq ca-certificates curl gnupg lsb-release

            install -m 0755 -d /etc/apt/keyrings
            if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
                curl -fsSL "https://download.docker.com/linux/${OS_ID}/gpg" | \
                    gpg --dearmor -o /etc/apt/keyrings/docker.gpg
                chmod a+r /etc/apt/keyrings/docker.gpg
            fi

            local codename
            codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"
            cat > /etc/apt/sources.list.d/docker.list <<EOF
deb [arch=${OS_ARCH} signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${OS_ID} ${codename} stable
EOF

            apt-get update -qq
            apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            ;;
        dnf)
            dnf install -y -q dnf-plugins-core
            dnf config-manager --add-repo "https://download.docker.com/linux/${OS_ID}/docker-ce.repo" 2>/dev/null || \
                dnf config-manager --add-repo "https://download.docker.com/linux/centos/docker-ce.repo"
            dnf install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            ;;
        apk)
            apk add --no-cache docker docker-cli-compose
            ;;
    esac

    systemctl enable --now docker
    log_ok "Docker installed and running"
}

install_deps_debian() {
    log_step "Installing system packages (Debian/Ubuntu)"

    apt-get update -qq

    # Build tools for Fossil compilation + runtime deps
    apt-get install -y -qq \
        build-essential \
        ca-certificates \
        curl \
        git \
        gnupg \
        lsb-release \
        zlib1g-dev \
        libssl-dev \
        tcl \
        openssh-server \
        sudo \
        logrotate

    # Python 3.12
    if ! command_exists python3 || ! version_gte "$(python3 --version 2>&1 | awk '{print $2}')" "$PYTHON_VERSION"; then
        log_info "Installing Python ${PYTHON_VERSION}..."
        apt-get install -y -qq software-properties-common
        add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null || true
        apt-get update -qq
        apt-get install -y -qq "python${PYTHON_VERSION}" "python${PYTHON_VERSION}-venv" "python${PYTHON_VERSION}-dev" || \
            apt-get install -y -qq python3 python3-venv python3-dev
    fi

    # PostgreSQL 16
    if ! command_exists psql || ! version_gte "$(psql --version | awk '{print $3}' | cut -d. -f1)" "$POSTGRES_VERSION"; then
        log_info "Installing PostgreSQL ${POSTGRES_VERSION}..."
        if [[ ! -f /etc/apt/sources.list.d/pgdg.list ]]; then
            curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | \
                gpg --dearmor -o /etc/apt/keyrings/postgresql.gpg
            echo "deb [signed-by=/etc/apt/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
                > /etc/apt/sources.list.d/pgdg.list
            apt-get update -qq
        fi
        apt-get install -y -qq "postgresql-${POSTGRES_VERSION}" "postgresql-client-${POSTGRES_VERSION}"
    fi

    # Redis
    if ! command_exists redis-server; then
        log_info "Installing Redis..."
        apt-get install -y -qq redis-server
    fi

    log_ok "System packages installed"
}

install_deps_rhel() {
    log_step "Installing system packages (RHEL/CentOS/Fedora/Amazon Linux)"

    dnf install -y -q epel-release 2>/dev/null || true
    dnf groupinstall -y -q "Development Tools" 2>/dev/null || \
        dnf install -y -q gcc gcc-c++ make

    # AL2023 ships curl-minimal which conflicts with curl; skip if any curl works
    local curl_pkg=""
    command_exists curl || curl_pkg="curl"

    dnf install -y -q \
        ca-certificates \
        $curl_pkg \
        git \
        zlib-devel \
        openssl-devel \
        tcl \
        openssh-server \
        sudo \
        logrotate

    # Python 3.12
    if ! command_exists python3 || ! version_gte "$(python3 --version 2>&1 | awk '{print $2}')" "$PYTHON_VERSION"; then
        log_info "Installing Python ${PYTHON_VERSION}..."
        dnf install -y -q "python${PYTHON_VERSION//.}" "python${PYTHON_VERSION//.}-devel" 2>/dev/null || \
            dnf install -y -q python3 python3-devel
    fi

    # PostgreSQL
    if ! command_exists psql; then
        log_info "Installing PostgreSQL..."
        # Try PG16 from PGDG repo first, fall back to distro version (PG15 on AL2023)
        if dnf install -y -q "https://download.postgresql.org/pub/repos/yum/reporpms/EL-$(rpm -E %{rhel})-${OS_ARCH}/pgdg-redhat-repo-latest.noarch.rpm" 2>/dev/null; then
            dnf install -y -q "postgresql${POSTGRES_VERSION}-server" "postgresql${POSTGRES_VERSION}" 2>/dev/null || \
                dnf install -y -q postgresql15-server postgresql15
        else
            dnf install -y -q postgresql15-server postgresql15 2>/dev/null || \
                dnf install -y -q postgresql-server postgresql
        fi
    fi

    # Redis
    if ! command_exists redis-server && ! command_exists redis6-server; then
        log_info "Installing Redis..."
        dnf install -y -q redis 2>/dev/null || dnf install -y -q redis6
    fi

    log_ok "System packages installed"
}

install_fossil_from_source() {
    # Matches Dockerfile lines 11-22 exactly
    if command_exists fossil; then
        local current_ver
        current_ver="$(fossil version | grep -oP 'version \K[0-9]+\.[0-9]+' | head -1)"
        if version_gte "${current_ver:-0}" "$FOSSIL_VERSION"; then
            log_ok "Fossil $current_ver already installed (>= $FOSSIL_VERSION)"
            return 0
        fi
    fi

    log_info "Building Fossil ${FOSSIL_VERSION} from source..."
    local build_dir
    build_dir="$(mktemp -d)"

    (
        cd "$build_dir"
        curl -sSL "https://fossil-scm.org/home/tarball/version-${FOSSIL_VERSION}/fossil-src-${FOSSIL_VERSION}.tar.gz" \
            -o fossil.tar.gz
        tar xzf fossil.tar.gz
        cd "fossil-src-${FOSSIL_VERSION}"
        ./configure --prefix=/usr/local --with-openssl=auto --json
        make -j"$(nproc)"
        make install
    )

    rm -rf "$build_dir"

    if ! command_exists fossil; then
        die "Fossil build failed -- binary not found at /usr/local/bin/fossil"
    fi

    log_ok "Fossil $(fossil version | grep -oP 'version \K[0-9]+\.[0-9]+') installed"
}

install_caddy_binary() {
    if command_exists caddy; then
        local current_ver
        current_ver="$(caddy version 2>/dev/null | awk '{print $1}' | tr -d 'v')"
        if version_gte "${current_ver:-0}" "$CADDY_VERSION"; then
            log_ok "Caddy $current_ver already installed (>= $CADDY_VERSION)"
            return 0
        fi
    fi

    log_info "Installing Caddy ${CADDY_VERSION}..."

    local caddy_arch="$OS_ARCH"
    local caddy_url="${CADDY_DOWNLOAD_BASE}?os=linux&arch=${caddy_arch}"

    curl -sSL "$caddy_url" -o /usr/local/bin/caddy
    chmod +x /usr/local/bin/caddy

    if ! /usr/local/bin/caddy version &>/dev/null; then
        die "Caddy binary download failed or is not executable"
    fi

    # Allow Caddy to bind to privileged ports without root
    if command_exists setcap; then
        setcap 'cap_net_bind_service=+ep' /usr/local/bin/caddy 2>/dev/null || true
    fi

    log_ok "Caddy $(/usr/local/bin/caddy version | awk '{print $1}') installed"
}

install_litestream_binary() {
    if [[ -z "$OPT_S3_BUCKET" ]]; then
        verbose "Skipping Litestream install (no S3 bucket configured)"
        return 0
    fi

    if command_exists litestream; then
        local current_ver
        current_ver="$(litestream version 2>/dev/null | tr -d 'v')"
        if version_gte "${current_ver:-0}" "$LITESTREAM_VERSION"; then
            log_ok "Litestream $current_ver already installed (>= $LITESTREAM_VERSION)"
            return 0
        fi
    fi

    log_info "Installing Litestream ${LITESTREAM_VERSION}..."

    local ls_arch
    case "$OS_ARCH" in
        amd64) ls_arch="amd64" ;;
        arm64) ls_arch="arm64" ;;
    esac

    local ls_url="${LITESTREAM_DOWNLOAD_BASE}/v${LITESTREAM_VERSION}/litestream-v${LITESTREAM_VERSION}-linux-${ls_arch}.tar.gz"
    local tmp_dir
    tmp_dir="$(mktemp -d)"

    curl -sSL "$ls_url" -o "${tmp_dir}/litestream.tar.gz"
    tar xzf "${tmp_dir}/litestream.tar.gz" -C "${tmp_dir}"
    install -m 0755 "${tmp_dir}/litestream" /usr/local/bin/litestream
    rm -rf "$tmp_dir"

    if ! command_exists litestream; then
        die "Litestream install failed"
    fi

    log_ok "Litestream $(litestream version) installed"
}

install_uv() {
    if command_exists uv; then
        log_ok "uv already installed"
        return 0
    fi

    log_info "Installing uv (Python package manager)..."
    export HOME="${HOME:-/root}"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Copy to /usr/local/bin so all users can access it (symlink fails because /root is 700)
    cp -f "${HOME}/.local/bin/uv" /usr/local/bin/uv 2>/dev/null || true
    cp -f "${HOME}/.local/bin/uvx" /usr/local/bin/uvx 2>/dev/null || true
    chmod +x /usr/local/bin/uv /usr/local/bin/uvx 2>/dev/null || true
    export PATH="/usr/local/bin:${HOME}/.local/bin:$PATH"

    if ! command_exists uv; then
        # Fallback: pip install
        pip3 install uv 2>/dev/null || pip install uv 2>/dev/null || \
            die "Failed to install uv"
    fi

    log_ok "uv installed"
}

check_and_install_deps() {
    if [[ "$OPT_MODE" == "docker" ]]; then
        if ! check_docker_deps; then
            confirm "Docker not found. Install Docker Engine?"
            install_docker_engine
        fi
    else
        # Bare metal: install OS packages, then build/download remaining deps
        case "$PKG_MANAGER" in
            apt) install_deps_debian ;;
            dnf) install_deps_rhel ;;
            *)   die "Unsupported package manager: $PKG_MANAGER" ;;
        esac

        install_fossil_from_source
        install_caddy_binary
        install_litestream_binary
        install_uv
    fi
}

# ============================================================================
# Section 9: Docker Mode
# ============================================================================

generate_env_file() {
    log_info "Generating .env file..."

    local proto="http"
    [[ "$OPT_SSL" == "true" ]] && proto="https"
    local base_url="${proto}://${OPT_DOMAIN}"

    local db_host="postgres"
    local redis_host="redis"
    local email_host="localhost"

    local use_s3="false"
    [[ -n "$OPT_S3_BUCKET" ]] && use_s3="true"

    local env_content
    env_content="# fossilrepo -- generated by installer on $(date -u +%Y-%m-%dT%H:%M:%SZ)
# Mode: ${OPT_MODE}

# --- Security ---
DJANGO_SECRET_KEY=${GEN_SECRET_KEY}
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=${OPT_DOMAIN},localhost,127.0.0.1

# --- Database ---
POSTGRES_DB=${OPT_DB_NAME}
POSTGRES_USER=${OPT_DB_USER}
POSTGRES_PASSWORD=${GEN_DB_PASSWORD}
POSTGRES_HOST=${db_host}
POSTGRES_PORT=5432

# --- Redis / Celery ---
REDIS_URL=redis://${redis_host}:6379/1
CELERY_BROKER=redis://${redis_host}:6379/0

# --- Email ---
EMAIL_HOST=${email_host}
EMAIL_PORT=587
DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
FROM_EMAIL=no-reply@${OPT_DOMAIN}

# --- S3 / Media ---
USE_S3=${use_s3}
AWS_ACCESS_KEY_ID=${OPT_S3_ACCESS_KEY}
AWS_SECRET_ACCESS_KEY=${OPT_S3_SECRET_KEY}
AWS_STORAGE_BUCKET_NAME=${OPT_S3_BUCKET}
AWS_S3_ENDPOINT_URL=${OPT_S3_ENDPOINT}

# --- CORS / CSRF ---
CORS_ALLOWED_ORIGINS=${base_url}
CSRF_TRUSTED_ORIGINS=${base_url}

# --- Sentry ---
SENTRY_DSN=

# --- Litestream S3 Replication ---
FOSSILREPO_S3_BUCKET=${OPT_S3_BUCKET}
FOSSILREPO_S3_REGION=${OPT_S3_REGION:-us-east-1}
FOSSILREPO_S3_ENDPOINT=${OPT_S3_ENDPOINT}"

    write_file "${OPT_PREFIX}/.env" "$env_content" "0600"
}

generate_docker_compose() {
    log_info "Generating docker-compose.yml..."

    local litestream_service=""
    local litestream_depends=""
    if [[ -n "$OPT_S3_BUCKET" ]]; then
        litestream_depends="
      litestream:
        condition: service_started"
        litestream_service="
  litestream:
    image: litestream/litestream:${LITESTREAM_VERSION}
    volumes:
      - fossil-repos:/data/repos
      - ./litestream.yml:/etc/litestream.yml:ro
    env_file: .env
    command: litestream replicate -config /etc/litestream.yml
    restart: unless-stopped"
    fi

    local compose_content
    compose_content="# fossilrepo -- production docker-compose
# Generated by installer on $(date -u +%Y-%m-%dT%H:%M:%SZ)

services:
  app:
    build:
      context: ./src
      dockerfile: Dockerfile
    env_file: .env
    environment:
      DJANGO_DEBUG: \"false\"
      POSTGRES_HOST: postgres
      REDIS_URL: redis://redis:6379/1
      CELERY_BROKER: redis://redis:6379/0
    ports:
      - \"${OPT_PORT}:8000\"
      - \"2222:2222\"
    volumes:
      - fossil-repos:/data/repos
      - fossil-ssh:/data/ssh
      - static-files:/app/assets
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: [\"CMD-SHELL\", \"curl -sf -o /dev/null -w '%{http_code}' http://localhost:8000/health/ | grep -qE '200|301|302' || exit 1\"]
      interval: 15s
      timeout: 10s
      retries: 5
      start_period: 60s

  celery-worker:
    build:
      context: ./src
      dockerfile: Dockerfile
    command: celery -A config.celery worker -l info -Q celery
    env_file: .env
    environment:
      POSTGRES_HOST: postgres
      REDIS_URL: redis://redis:6379/1
      CELERY_BROKER: redis://redis:6379/0
    volumes:
      - fossil-repos:/data/repos
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped

  celery-beat:
    build:
      context: ./src
      dockerfile: Dockerfile
    command: celery -A config.celery beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
    env_file: .env
    environment:
      POSTGRES_HOST: postgres
      REDIS_URL: redis://redis:6379/1
      CELERY_BROKER: redis://redis:6379/0
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped

  postgres:
    image: postgres:${POSTGRES_VERSION}-alpine
    environment:
      POSTGRES_DB: ${OPT_DB_NAME}
      POSTGRES_USER: ${OPT_DB_USER}
      POSTGRES_PASSWORD: ${GEN_DB_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: [\"CMD-SHELL\", \"pg_isready -U ${OPT_DB_USER} -d ${OPT_DB_NAME}\"]
      interval: 5s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  redis:
    image: redis:${REDIS_VERSION}-alpine
    volumes:
      - redisdata:/data
    command: redis-server --appendonly yes
    healthcheck:
      test: [\"CMD\", \"redis-cli\", \"ping\"]
      interval: 5s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  caddy:
    image: caddy:2-alpine
    ports:
      - \"80:80\"
      - \"443:443\"
      - \"443:443/udp\"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
      - caddy-config:/config
      - static-files:/srv/static:ro
    depends_on:
      app:
        condition: service_started
    restart: unless-stopped
${litestream_service}
volumes:
  pgdata:
  redisdata:
  fossil-repos:
  fossil-ssh:
  static-files:
  caddy-data:
  caddy-config:"

    write_file "${OPT_PREFIX}/docker-compose.yml" "$compose_content"
}

generate_caddyfile() {
    log_info "Generating Caddyfile..."

    local caddy_content

    if [[ "$OPT_SSL" == "true" && "$OPT_DOMAIN" != "localhost" ]]; then
        caddy_content="# fossilrepo Caddy config -- auto HTTPS
# Generated by installer

# Root domain -- Django app
${OPT_DOMAIN} {
    encode gzip

    # Static files served by Caddy
    handle_path /static/* {
        root * /srv/static
        file_server
    }

    # Everything else to Django/gunicorn
    reverse_proxy app:8000
}

# Wildcard subdomain routing -- repo subdomains to Django
*.${OPT_DOMAIN} {
    tls {
        dns
    }

    encode gzip
    reverse_proxy app:8000
}"
    else
        # No SSL / localhost
        local listen_addr
        if [[ "$OPT_DOMAIN" == "localhost" || "$OPT_DOMAIN" == "127.0.0.1" ]]; then
            listen_addr=":80"
        else
            listen_addr="${OPT_DOMAIN}:80"
        fi

        caddy_content="# fossilrepo Caddy config -- HTTP only
# Generated by installer

{
    auto_https off
}

${listen_addr} {
    encode gzip

    handle_path /static/* {
        root * /srv/static
        file_server
    }

    reverse_proxy app:8000
}"
    fi

    write_file "${OPT_PREFIX}/Caddyfile" "$caddy_content"
}

generate_litestream_config() {
    if [[ -z "$OPT_S3_BUCKET" ]]; then
        return 0
    fi

    log_info "Generating litestream.yml..."

    local ls_content
    ls_content="# Litestream replication -- continuous .fossil backup to S3
# Generated by installer

dbs:
  - path: /data/repos/*.fossil
    replicas:
      - type: s3
        bucket: ${OPT_S3_BUCKET}
        endpoint: ${OPT_S3_ENDPOINT}
        region: ${OPT_S3_REGION:-us-east-1}
        access-key-id: \${AWS_ACCESS_KEY_ID}
        secret-access-key: \${AWS_SECRET_ACCESS_KEY}"

    write_file "${OPT_PREFIX}/litestream.yml" "$ls_content"
}

setup_docker_systemd() {
    log_info "Creating systemd service for auto-start..."

    local unit_content
    unit_content="[Unit]
Description=fossilrepo (Docker Compose)
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${OPT_PREFIX}
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target"

    write_file "/etc/systemd/system/fossilrepo.service" "$unit_content"
    systemctl daemon-reload
    systemctl enable fossilrepo.service
    log_ok "systemd service enabled (fossilrepo.service)"
}

install_docker() {
    log_step "Installing fossilrepo (Docker mode)"

    mkdir -p "${OPT_PREFIX}/src"

    # Clone the repo
    if [[ -d "${OPT_PREFIX}/src/.git" ]]; then
        log_info "Updating existing repo..."
        git -C "${OPT_PREFIX}/src" pull --ff-only || true
    else
        log_info "Cloning fossilrepo..."
        git clone "$REPO_URL" "${OPT_PREFIX}/src"
    fi

    # Generate all config files
    generate_env_file
    generate_docker_compose
    generate_caddyfile
    generate_litestream_config

    # Build and start
    log_info "Building Docker images (this may take a few minutes)..."
    cd "$OPT_PREFIX"
    docker compose build

    log_info "Starting services..."
    docker compose up -d

    # Wait for postgres to be healthy
    log_info "Waiting for PostgreSQL to be ready..."
    local attempts=0
    while ! docker compose exec -T postgres pg_isready -U "$OPT_DB_USER" -d "$OPT_DB_NAME" &>/dev/null; do
        ((attempts++))
        if ((attempts > 30)); then
            die "PostgreSQL did not become ready within 150 seconds"
        fi
        sleep 5
    done
    log_ok "PostgreSQL is ready"

    # Run Django setup
    log_info "Running database migrations..."
    docker compose exec -T app python manage.py migrate --noinput

    log_info "Collecting static files..."
    docker compose exec -T app python manage.py collectstatic --noinput

    # Create admin user
    log_info "Creating admin user..."
    docker compose exec -T app python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(username='${OPT_ADMIN_USER}').exists():
    user = User.objects.create_superuser(
        username='${OPT_ADMIN_USER}',
        email='${OPT_ADMIN_EMAIL}',
        password='${GEN_ADMIN_PASSWORD}',
    )
    print(f'Admin user created: {user.username}')
else:
    print('Admin user already exists')
"

    # Create data directories inside the app container
    docker compose exec -T app mkdir -p /data/repos /data/trash /data/ssh

    setup_docker_systemd

    log_ok "Docker installation complete"
}

# ============================================================================
# Section 10: Bare Metal Mode
# ============================================================================

create_system_user() {
    log_info "Creating fossilrepo system user..."

    if id fossilrepo &>/dev/null; then
        verbose "User fossilrepo already exists"
    else
        useradd -r -m -d /home/fossilrepo -s /bin/bash fossilrepo
    fi

    # Data directories
    mkdir -p "${DATA_DIR}/repos" "${DATA_DIR}/trash" "${DATA_DIR}/ssh" "${DATA_DIR}/git-mirrors" "${DATA_DIR}/ssh-keys"
    mkdir -p "$LOG_DIR"
    mkdir -p "${OPT_PREFIX}"
    chown -R fossilrepo:fossilrepo "${DATA_DIR}"
    chown -R fossilrepo:fossilrepo "$LOG_DIR"

    log_ok "System user and directories created"
}

clone_repo() {
    # Configure SSH for GitHub if deploy key exists
    if [[ -f /root/.ssh/deploy_key ]]; then
        export GIT_SSH_COMMAND="ssh -i /root/.ssh/deploy_key -o StrictHostKeyChecking=no"
        # Use SSH URL for private repos
        local repo_url="${REPO_URL/https:\/\/github.com\//git@github.com:}"
    else
        local repo_url="$REPO_URL"
    fi

    if [[ -d "${OPT_PREFIX}/.git" ]]; then
        log_info "Updating existing repo..."
        git config --global --add safe.directory "$OPT_PREFIX" 2>/dev/null || true
        git -C "$OPT_PREFIX" pull --ff-only || true
    elif [[ -d "$OPT_PREFIX" ]]; then
        # Safety: never move a directory that contains user data
        if [[ -d "${OPT_PREFIX}/.venv" ]] || [[ -f "${OPT_PREFIX}/.env" ]]; then
            log_warn "${OPT_PREFIX} exists (previous install). Cloning into subfolder..."
            local src_dir="${OPT_PREFIX}/src"
            rm -rf "$src_dir"
            git clone "$repo_url" "$src_dir"
            # Move source files up, preserving .env and .venv
            find "$src_dir" -maxdepth 1 -not -name src -not -name . -exec mv -n {} "$OPT_PREFIX/" \;
            rm -rf "$src_dir"
        else
            log_warn "${OPT_PREFIX} exists but is not a git repo or fossilrepo install. Backing up..."
            mv "$OPT_PREFIX" "${OPT_PREFIX}.bak.$(date +%s)"
            git clone "$repo_url" "$OPT_PREFIX"
        fi
    else
        log_info "Cloning fossilrepo to ${OPT_PREFIX}..."
        git clone "$repo_url" "$OPT_PREFIX"
    fi
    chown -R fossilrepo:fossilrepo "$OPT_PREFIX"
    log_ok "Repository cloned"
}

setup_python_venv() {
    log_info "Setting up Python virtual environment..."

    local venv_dir="${OPT_PREFIX}/.venv"

    # Resolve uv path (sudo resets PATH)
    local uv_bin
    uv_bin="$(command -v uv 2>/dev/null || echo /usr/local/bin/uv)"

    # Use uv to create venv and install deps
    if [[ -x "$uv_bin" ]]; then
        sudo -u fossilrepo "$uv_bin" venv "$venv_dir" --python "python${PYTHON_VERSION}" 2>/dev/null || \
            sudo -u fossilrepo "$uv_bin" venv "$venv_dir" --clear 2>/dev/null || \
            sudo -u fossilrepo "$uv_bin" venv "$venv_dir"
        sudo -u fossilrepo bash -c "cd '${OPT_PREFIX}' && source '${venv_dir}/bin/activate' && '${uv_bin}' pip install -r pyproject.toml"
    else
        sudo -u fossilrepo "python${PYTHON_VERSION}" -m venv "$venv_dir" 2>/dev/null || \
            sudo -u fossilrepo python3 -m venv "$venv_dir"
        sudo -u fossilrepo bash -c "source '${venv_dir}/bin/activate' && pip install --upgrade pip && pip install -r '${OPT_PREFIX}/pyproject.toml'"
    fi

    log_ok "Python environment configured"
}

setup_postgres() {
    log_info "Configuring PostgreSQL..."

    # Ensure PostgreSQL service is running
    local pg_service
    pg_service="postgresql"
    if systemctl list-unit-files "postgresql-${POSTGRES_VERSION}.service" &>/dev/null; then
        pg_service="postgresql-${POSTGRES_VERSION}"
    fi

    # Initialize cluster if needed (RHEL)
    if [[ "$PKG_MANAGER" == "dnf" ]]; then
        local pg_setup="/usr/pgsql-${POSTGRES_VERSION}/bin/postgresql-${POSTGRES_VERSION}-setup"
        if [[ -x "$pg_setup" ]]; then
            "$pg_setup" initdb 2>/dev/null || true
        fi
    fi

    systemctl enable --now "$pg_service"

    # Wait for PostgreSQL to accept connections
    local attempts=0
    while ! sudo -u postgres pg_isready -q 2>/dev/null; do
        ((attempts++))
        if ((attempts > 20)); then
            die "PostgreSQL did not start within 100 seconds"
        fi
        sleep 5
    done

    # Ensure peer auth for postgres user (previous runs may have broken it)
    local pg_hba_candidates=("/var/lib/pgsql/data/pg_hba.conf" "/etc/postgresql/*/main/pg_hba.conf")
    local pg_hba=""
    for f in ${pg_hba_candidates[@]}; do
        [[ -f "$f" ]] && pg_hba="$f" && break
    done

    if [[ -n "$pg_hba" ]]; then
        # Ensure postgres user can connect via peer (unix socket)
        if ! grep -q "^local.*all.*postgres.*peer" "$pg_hba"; then
            sed -i '1i local   all   postgres   peer' "$pg_hba"
            systemctl reload "$pg_service" 2>/dev/null || true
            sleep 2
        fi
    fi

    # Create user and database (idempotent)
    sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname = '${OPT_DB_USER}'" | \
        grep -q 1 || \
        sudo -u postgres psql -c "CREATE USER ${OPT_DB_USER} WITH PASSWORD '${GEN_DB_PASSWORD}';"

    # Update password in case it changed
    sudo -u postgres psql -c "ALTER USER ${OPT_DB_USER} WITH PASSWORD '${GEN_DB_PASSWORD}';"

    sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = '${OPT_DB_NAME}'" | \
        grep -q 1 || \
        sudo -u postgres psql -c "CREATE DATABASE ${OPT_DB_NAME} OWNER ${OPT_DB_USER};"

    sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE ${OPT_DB_NAME} TO ${OPT_DB_USER};"
    sudo -u postgres psql -d "${OPT_DB_NAME}" -c "GRANT ALL ON SCHEMA public TO ${OPT_DB_USER};"
    sudo -u postgres psql -d "${OPT_DB_NAME}" -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO ${OPT_DB_USER};"

    # Fix pg_hba.conf to allow md5 auth for app connections via TCP (127.0.0.1)
    pg_hba="${pg_hba:-$(sudo -u postgres psql -t -c 'SHOW hba_file' 2>/dev/null | tr -d '[:space:]')}"

    if [[ -f "$pg_hba" ]]; then
        # Ensure md5 auth for 127.0.0.1 connections
        if ! grep -q "host.*${OPT_DB_NAME}.*${OPT_DB_USER}.*127.0.0.1/32.*md5" "$pg_hba" && \
           ! grep -q "host.*${OPT_DB_NAME}.*${OPT_DB_USER}.*127.0.0.1/32.*scram-sha-256" "$pg_hba"; then
            backup_file "$pg_hba"
            # Insert before the first 'host' line to take priority
            sed -i "/^# IPv4 local connections/a host    ${OPT_DB_NAME}    ${OPT_DB_USER}    127.0.0.1/32    md5" "$pg_hba" 2>/dev/null || \
                echo "host    ${OPT_DB_NAME}    ${OPT_DB_USER}    127.0.0.1/32    md5" >> "$pg_hba"
            systemctl reload "$pg_service"
            verbose "Added md5 auth rule to pg_hba.conf"
        fi
    fi

    # Verify connection works
    if ! PGPASSWORD="$GEN_DB_PASSWORD" psql -h 127.0.0.1 -U "$OPT_DB_USER" -d "$OPT_DB_NAME" -c "SELECT 1" &>/dev/null; then
        log_warn "PostgreSQL connection test failed -- you may need to manually adjust pg_hba.conf"
    else
        log_ok "PostgreSQL configured and connection verified"
    fi
}

setup_redis() {
    log_info "Configuring Redis..."
    systemctl enable --now redis-server 2>/dev/null || systemctl enable --now redis6 2>/dev/null || systemctl enable --now redis 2>/dev/null
    # Verify Redis is responding
    local attempts=0
    local redis_cli="redis-cli"
    command_exists redis-cli || redis_cli="redis6-cli"
    while ! $redis_cli ping &>/dev/null; do
        ((attempts++))
        if ((attempts > 10)); then
            die "Redis did not start within 50 seconds"
        fi
        sleep 5
    done
    log_ok "Redis running"
}

generate_bare_metal_env() {
    log_info "Generating .env file..."

    local proto="http"
    [[ "$OPT_SSL" == "true" ]] && proto="https"
    local base_url="${proto}://${OPT_DOMAIN}"

    local use_s3="false"
    [[ -n "$OPT_S3_BUCKET" ]] && use_s3="true"

    local env_content
    env_content="# fossilrepo -- generated by installer on $(date -u +%Y-%m-%dT%H:%M:%SZ)
# Mode: bare-metal

# --- Security ---
DJANGO_SECRET_KEY=${GEN_SECRET_KEY}
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=${OPT_DOMAIN},localhost,127.0.0.1
DJANGO_SETTINGS_MODULE=config.settings

# --- Database ---
POSTGRES_DB=${OPT_DB_NAME}
POSTGRES_USER=${OPT_DB_USER}
POSTGRES_PASSWORD=${GEN_DB_PASSWORD}
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432

# --- Redis / Celery ---
REDIS_URL=redis://127.0.0.1:6379/1
CELERY_BROKER=redis://127.0.0.1:6379/0

# --- Email ---
EMAIL_HOST=localhost
EMAIL_PORT=587
DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
FROM_EMAIL=no-reply@${OPT_DOMAIN}

# --- S3 / Media ---
USE_S3=${use_s3}
AWS_ACCESS_KEY_ID=${OPT_S3_ACCESS_KEY}
AWS_SECRET_ACCESS_KEY=${OPT_S3_SECRET_KEY}
AWS_STORAGE_BUCKET_NAME=${OPT_S3_BUCKET}
AWS_S3_ENDPOINT_URL=${OPT_S3_ENDPOINT}

# --- CORS / CSRF ---
CORS_ALLOWED_ORIGINS=${base_url}
CSRF_TRUSTED_ORIGINS=${base_url}

# --- Sentry ---
SENTRY_DSN=

# --- Litestream S3 Replication ---
FOSSILREPO_S3_BUCKET=${OPT_S3_BUCKET}
FOSSILREPO_S3_REGION=${OPT_S3_REGION:-us-east-1}
FOSSILREPO_S3_ENDPOINT=${OPT_S3_ENDPOINT}"

    write_file "${OPT_PREFIX}/.env" "$env_content" "0600"
    chown fossilrepo:fossilrepo "${OPT_PREFIX}/.env"
}

run_django_setup() {
    log_info "Running Django setup..."

    local venv_activate="${OPT_PREFIX}/.venv/bin/activate"
    local env_file="${OPT_PREFIX}/.env"

    # Migrate
    log_info "Running database migrations..."
    sudo -u fossilrepo bash -c "
        set -a; source '${env_file}'; set +a
        source '${venv_activate}'
        cd '${OPT_PREFIX}'
        python manage.py migrate --noinput
    "

    # Collect static
    log_info "Collecting static files..."
    sudo -u fossilrepo bash -c "
        set -a; source '${env_file}'; set +a
        source '${venv_activate}'
        cd '${OPT_PREFIX}'
        python manage.py collectstatic --noinput
    "

    # Create admin user
    log_info "Creating admin user..."
    sudo -u fossilrepo bash -c "
        set -a; source '${env_file}'; set +a
        source '${venv_activate}'
        cd '${OPT_PREFIX}'
        python manage.py shell -c \"
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(username='${OPT_ADMIN_USER}').exists():
    user = User.objects.create_superuser(
        username='${OPT_ADMIN_USER}',
        email='${OPT_ADMIN_EMAIL}',
        password='${GEN_ADMIN_PASSWORD}',
    )
    print(f'Admin user created: {user.username}')
else:
    print('Admin user already exists')
\"
    "

    log_ok "Django setup complete"
}

setup_caddy_bare_metal() {
    log_info "Configuring Caddy..."

    mkdir -p /etc/caddy
    local caddy_content

    if [[ "$OPT_SSL" == "true" && "$OPT_DOMAIN" != "localhost" ]]; then
        caddy_content="# fossilrepo Caddy config -- auto HTTPS (bare metal)
# Generated by installer

${OPT_DOMAIN} {
    encode gzip

    handle_path /static/* {
        root * ${OPT_PREFIX}/assets
        file_server
    }

    reverse_proxy 127.0.0.1:8000
}

"
    else
        caddy_content="# fossilrepo Caddy config -- HTTP (bare metal)
# Generated by installer

{
    auto_https off
}

:80 {
    encode gzip

    handle_path /static/* {
        root * ${OPT_PREFIX}/assets
        file_server
    }

    reverse_proxy 127.0.0.1:8000
}"
    fi

    write_file "/etc/caddy/Caddyfile" "$caddy_content"

    # Caddy systemd unit
    local caddy_bin
    caddy_bin="$(command -v caddy)"

    local caddy_unit
    caddy_unit="[Unit]
Description=Caddy web server (fossilrepo)
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
User=caddy
Group=caddy
ExecStart=${caddy_bin} run --config /etc/caddy/Caddyfile --adapter caddyfile
ExecReload=${caddy_bin} reload --config /etc/caddy/Caddyfile --adapter caddyfile
TimeoutStopSec=5s
LimitNOFILE=1048576
LimitNPROC=512

[Install]
WantedBy=multi-user.target"

    # Create caddy user if it doesn't exist
    if ! id caddy &>/dev/null; then
        useradd -r -m -d /var/lib/caddy -s /usr/sbin/nologin caddy
    fi
    mkdir -p /var/lib/caddy/.local/share/caddy
    chown -R caddy:caddy /var/lib/caddy

    write_file "/etc/systemd/system/caddy.service" "$caddy_unit"
    log_ok "Caddy configured"
}

create_systemd_services() {
    log_info "Creating systemd service units..."

    local venv_activate="${OPT_PREFIX}/.venv/bin/activate"
    local env_file="${OPT_PREFIX}/.env"

    # --- gunicorn (fossilrepo-web) ---
    local gunicorn_unit
    gunicorn_unit="[Unit]
Description=fossilrepo web (gunicorn)
After=network.target postgresql.service redis.service
Requires=postgresql.service

[Service]
Type=notify
User=fossilrepo
Group=fossilrepo
WorkingDirectory=${OPT_PREFIX}
EnvironmentFile=${env_file}
ExecStart=${OPT_PREFIX}/.venv/bin/gunicorn config.wsgi:application \\
    --bind 127.0.0.1:8000 \\
    --workers 3 \\
    --timeout 120 \\
    --access-logfile ${LOG_DIR}/gunicorn-access.log \\
    --error-logfile ${LOG_DIR}/gunicorn-error.log
ExecReload=/bin/kill -s HUP \$MAINPID
Restart=on-failure
RestartSec=10
KillMode=mixed
StandardOutput=append:${LOG_DIR}/web.log
StandardError=append:${LOG_DIR}/web.log

[Install]
WantedBy=multi-user.target"

    write_file "/etc/systemd/system/fossilrepo-web.service" "$gunicorn_unit"

    # --- celery worker ---
    local celery_worker_unit
    celery_worker_unit="[Unit]
Description=fossilrepo Celery worker
After=network.target postgresql.service redis.service
Requires=redis.service

[Service]
Type=forking
User=fossilrepo
Group=fossilrepo
WorkingDirectory=${OPT_PREFIX}
EnvironmentFile=${env_file}
ExecStart=${OPT_PREFIX}/.venv/bin/celery -A config.celery worker \\
    -l info \\
    -Q celery \\
    --detach \\
    --pidfile=${OPT_PREFIX}/celery-worker.pid \\
    --logfile=${LOG_DIR}/celery-worker.log
ExecStop=/bin/kill -s TERM \$(cat ${OPT_PREFIX}/celery-worker.pid)
PIDFile=${OPT_PREFIX}/celery-worker.pid
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target"

    write_file "/etc/systemd/system/fossilrepo-celery-worker.service" "$celery_worker_unit"

    # --- celery beat ---
    local celery_beat_unit
    celery_beat_unit="[Unit]
Description=fossilrepo Celery beat scheduler
After=network.target postgresql.service redis.service
Requires=redis.service

[Service]
Type=forking
User=fossilrepo
Group=fossilrepo
WorkingDirectory=${OPT_PREFIX}
EnvironmentFile=${env_file}
ExecStart=${OPT_PREFIX}/.venv/bin/celery -A config.celery beat \\
    -l info \\
    --scheduler django_celery_beat.schedulers:DatabaseScheduler \\
    --detach \\
    --pidfile=${OPT_PREFIX}/celery-beat.pid \\
    --logfile=${LOG_DIR}/celery-beat.log
ExecStop=/bin/kill -s TERM \$(cat ${OPT_PREFIX}/celery-beat.pid)
PIDFile=${OPT_PREFIX}/celery-beat.pid
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target"

    write_file "/etc/systemd/system/fossilrepo-celery-beat.service" "$celery_beat_unit"

    # --- litestream (optional) ---
    if [[ -n "$OPT_S3_BUCKET" ]]; then
        local litestream_unit
        litestream_unit="[Unit]
Description=fossilrepo Litestream replication
After=network.target

[Service]
Type=simple
User=fossilrepo
Group=fossilrepo
EnvironmentFile=${env_file}
ExecStart=/usr/local/bin/litestream replicate -config ${OPT_PREFIX}/litestream.yml
Restart=on-failure
RestartSec=10
StandardOutput=append:${LOG_DIR}/litestream.log
StandardError=append:${LOG_DIR}/litestream.log

[Install]
WantedBy=multi-user.target"

        # Generate litestream config for bare metal
        local ls_content
        ls_content="# Litestream replication -- continuous .fossil backup to S3
# Generated by installer

dbs:
  - path: ${DATA_DIR}/repos/*.fossil
    replicas:
      - type: s3
        bucket: ${OPT_S3_BUCKET}
        endpoint: ${OPT_S3_ENDPOINT}
        region: ${OPT_S3_REGION:-us-east-1}
        access-key-id: \${AWS_ACCESS_KEY_ID}
        secret-access-key: \${AWS_SECRET_ACCESS_KEY}"

        write_file "${OPT_PREFIX}/litestream.yml" "$ls_content"
        chown fossilrepo:fossilrepo "${OPT_PREFIX}/litestream.yml"
        write_file "/etc/systemd/system/fossilrepo-litestream.service" "$litestream_unit"
    fi

    # Reload systemd and enable all services
    systemctl daemon-reload
    systemctl enable fossilrepo-web.service
    systemctl enable fossilrepo-celery-worker.service
    systemctl enable fossilrepo-celery-beat.service
    systemctl enable caddy.service
    [[ -n "$OPT_S3_BUCKET" ]] && systemctl enable fossilrepo-litestream.service

    # Start services
    systemctl start caddy.service
    systemctl start fossilrepo-web.service
    systemctl start fossilrepo-celery-worker.service
    systemctl start fossilrepo-celery-beat.service
    [[ -n "$OPT_S3_BUCKET" ]] && systemctl start fossilrepo-litestream.service

    log_ok "All systemd services created and started"
}

setup_logrotate() {
    log_info "Configuring log rotation..."

    local logrotate_content
    logrotate_content="${LOG_DIR}/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 fossilrepo fossilrepo
    sharedscripts
    postrotate
        systemctl reload fossilrepo-web.service 2>/dev/null || true
    endscript
}"

    write_file "/etc/logrotate.d/fossilrepo" "$logrotate_content"
    log_ok "Log rotation configured (14 days, compressed)"
}

install_bare_metal() {
    log_step "Installing fossilrepo (Bare Metal mode)"

    create_system_user
    clone_repo
    setup_python_venv
    setup_postgres
    setup_redis
    generate_bare_metal_env
    run_django_setup
    setup_caddy_bare_metal
    create_systemd_services
    setup_logrotate

    log_ok "Bare metal installation complete"
}

# ============================================================================
# Section 11: Uninstall Generator
# ============================================================================

generate_uninstall_script() {
    log_info "Generating uninstall script..."

    local uninstall_content
    uninstall_content="#!/usr/bin/env bash
# fossilrepo uninstaller
# Generated by installer on $(date -u +%Y-%m-%dT%H:%M:%SZ)
# Mode: ${OPT_MODE}

set -euo pipefail

echo 'fossilrepo uninstaller'
echo '======================'
echo ''
echo 'This will remove all fossilrepo services, files, and data.'
echo 'PostgreSQL data and Fossil repositories will be DELETED.'
echo ''
read -p 'Are you sure? Type YES to confirm: ' confirm
[[ \"\$confirm\" == \"YES\" ]] || { echo 'Aborted.'; exit 1; }"

    if [[ "$OPT_MODE" == "docker" ]]; then
        uninstall_content+="

echo 'Stopping Docker services (preserving volumes)...'
cd '${OPT_PREFIX}'
docker compose down 2>/dev/null || true

echo ''
echo '  NOTE: Docker volumes have been preserved.'
echo '  To remove them (DELETES ALL DATA): docker volume prune'
echo ''

echo 'Removing systemd service...'
systemctl stop fossilrepo.service 2>/dev/null || true
systemctl disable fossilrepo.service 2>/dev/null || true
rm -f /etc/systemd/system/fossilrepo.service
systemctl daemon-reload

echo 'Removing application code (preserving .env backup)...'
cp -f '${OPT_PREFIX}/.env' '/tmp/fossilrepo-env.bak' 2>/dev/null || true
cp -f '${OPT_PREFIX}/.credentials' '/tmp/fossilrepo-creds.bak' 2>/dev/null || true
rm -rf '${OPT_PREFIX}'
echo '  Backup of .env saved to /tmp/fossilrepo-env.bak'

echo 'Done. Docker volumes and images may still be cached.'
echo '  To remove volumes (DELETES DATA): docker volume prune'
echo '  To remove images: docker system prune'"
    else
        uninstall_content+="

echo 'Stopping services...'
systemctl stop fossilrepo-web.service 2>/dev/null || true
systemctl stop fossilrepo-celery-worker.service 2>/dev/null || true
systemctl stop fossilrepo-celery-beat.service 2>/dev/null || true
systemctl stop fossilrepo-litestream.service 2>/dev/null || true
systemctl stop caddy.service 2>/dev/null || true

echo 'Disabling services...'
systemctl disable fossilrepo-web.service 2>/dev/null || true
systemctl disable fossilrepo-celery-worker.service 2>/dev/null || true
systemctl disable fossilrepo-celery-beat.service 2>/dev/null || true
systemctl disable fossilrepo-litestream.service 2>/dev/null || true

echo 'Removing systemd units...'
rm -f /etc/systemd/system/fossilrepo-web.service
rm -f /etc/systemd/system/fossilrepo-celery-worker.service
rm -f /etc/systemd/system/fossilrepo-celery-beat.service
rm -f /etc/systemd/system/fossilrepo-litestream.service
rm -f /etc/systemd/system/caddy.service
systemctl daemon-reload

echo 'Removing Caddy config...'
rm -f /etc/caddy/Caddyfile

echo 'Removing logrotate config...'
rm -f /etc/logrotate.d/fossilrepo

echo 'Removing log files...'
rm -rf '${LOG_DIR}'

echo ''
echo '================================================================'
echo '  DATA PRESERVATION NOTICE'
echo '================================================================'
echo ''
echo '  The following data has been PRESERVED (not deleted):'
echo ''
echo '  Fossil repositories:  ${DATA_DIR}/repos/'
echo '  PostgreSQL database:  ${OPT_DB_NAME} (user: ${OPT_DB_USER})'
echo '  Git mirrors:          ${DATA_DIR}/git-mirrors/'
echo '  SSH keys:             ${DATA_DIR}/ssh/'
echo ''
echo '  To remove the database:'
echo '    sudo -u postgres psql -c \"DROP DATABASE IF EXISTS ${OPT_DB_NAME};\"'
echo '    sudo -u postgres psql -c \"DROP USER IF EXISTS ${OPT_DB_USER};\"'
echo ''
echo '  To remove repo data (IRREVERSIBLE):'
echo '    rm -rf ${DATA_DIR}/repos'
echo '    rm -rf ${DATA_DIR}/git-mirrors'
echo '    rm -rf ${DATA_DIR}/ssh'
echo ''
echo '  These are left intact so you can back them up or migrate.'
echo '================================================================'

echo 'Removing application code (preserving .env backup)...'
cp -f '${OPT_PREFIX}/.env' '/tmp/fossilrepo-env.bak' 2>/dev/null || true
cp -f '${OPT_PREFIX}/.credentials' '/tmp/fossilrepo-creds.bak' 2>/dev/null || true
rm -rf '${OPT_PREFIX}'
echo '  Backup of .env saved to /tmp/fossilrepo-env.bak'

echo 'Removing system user...'
userdel -r fossilrepo 2>/dev/null || true

echo 'Done. System packages (PostgreSQL, Redis, Fossil, Caddy) were NOT removed.'"
    fi

    uninstall_content+="

echo ''
echo 'fossilrepo has been uninstalled.'"

    write_file "${OPT_PREFIX}/uninstall.sh" "$uninstall_content" "0755"
    log_ok "Uninstall script: ${OPT_PREFIX}/uninstall.sh"
}

# ============================================================================
# Section 12: Post-Install Summary
# ============================================================================

show_summary() {
    local proto="http"
    [[ "$OPT_SSL" == "true" ]] && proto="https"
    local base_url="${proto}://${OPT_DOMAIN}"

    if [[ "$OPT_DOMAIN" == "localhost" && "$OPT_PORT" != "80" && "$OPT_PORT" != "443" ]]; then
        base_url="${proto}://localhost:${OPT_PORT}"
    fi

    local box_width=64
    local border
    border="$(printf '%*s' $box_width '' | tr ' ' '=')"

    printf "\n"
    printf "${_C_GREEN}${_C_BOLD}"
    printf "  %s\n" "$border"
    printf "  %-${box_width}s\n" "  fossilrepo installation complete"
    printf "  %s\n" "$border"
    printf "${_C_RESET}"
    printf "\n"
    printf "  ${_C_BOLD}%-24s${_C_RESET} %s\n" "Web UI:" "${base_url}"
    printf "  ${_C_BOLD}%-24s${_C_RESET} %s\n" "Django Admin:" "${base_url}/admin/"
    printf "  ${_C_BOLD}%-24s${_C_RESET} %s\n" "Health Check:" "${base_url}/health/"
    printf "\n"
    printf "  ${_C_BOLD}%-24s${_C_RESET} %s\n" "Admin username:" "$OPT_ADMIN_USER"
    printf "  ${_C_BOLD}%-24s${_C_RESET} %s\n" "Admin email:" "$OPT_ADMIN_EMAIL"
    printf "  ${_C_BOLD}%-24s${_C_RESET} %s\n" "Admin password:" "$GEN_ADMIN_PASSWORD"
    printf "\n"

    if [[ "$OPT_DOMAIN" != "localhost" ]]; then
        printf "  ${_C_BOLD}%-24s${_C_RESET} %s\n" "SSH clone:" "ssh://fossil@${OPT_DOMAIN}:2222/<repo>"
    fi

    printf "  ${_C_BOLD}%-24s${_C_RESET} %s\n" "Install mode:" "$OPT_MODE"
    printf "  ${_C_BOLD}%-24s${_C_RESET} %s\n" "Install prefix:" "$OPT_PREFIX"
    printf "  ${_C_BOLD}%-24s${_C_RESET} %s\n" "Config file:" "${OPT_PREFIX}/.env"
    printf "  ${_C_BOLD}%-24s${_C_RESET} %s\n" "Uninstall:" "${OPT_PREFIX}/uninstall.sh"
    printf "\n"

    if [[ "$OPT_MODE" == "docker" ]]; then
        printf "  ${_C_BOLD}Useful commands:${_C_RESET}\n"
        printf "    cd %s\n" "$OPT_PREFIX"
        printf "    docker compose logs -f          # tail all logs\n"
        printf "    docker compose logs -f app      # tail app logs\n"
        printf "    docker compose exec app bash    # shell into app container\n"
        printf "    docker compose restart           # restart all services\n"
        printf "    docker compose down              # stop all services\n"
    else
        printf "  ${_C_BOLD}Useful commands:${_C_RESET}\n"
        printf "    systemctl status fossilrepo-web          # check web status\n"
        printf "    journalctl -u fossilrepo-web -f          # tail web logs\n"
        printf "    journalctl -u fossilrepo-celery-worker -f # tail worker logs\n"
        printf "    systemctl restart fossilrepo-web          # restart web\n"
        printf "    tail -f %s/*.log                         # tail log files\n" "$LOG_DIR"
    fi

    printf "\n"

    if [[ -n "$OPT_S3_BUCKET" ]]; then
        printf "  ${_C_BOLD}Litestream backup:${_C_RESET} s3://%s\n" "$OPT_S3_BUCKET"
    fi

    printf "${_C_YELLOW}  IMPORTANT: Save the admin password above -- it will not be shown again.${_C_RESET}\n"
    printf "\n"

    # Write credentials to a restricted file for reference
    local creds_content
    creds_content="# fossilrepo credentials -- generated $(date -u +%Y-%m-%dT%H:%M:%SZ)
# KEEP THIS FILE SECURE -- delete after recording credentials elsewhere

ADMIN_USER=${OPT_ADMIN_USER}
ADMIN_EMAIL=${OPT_ADMIN_EMAIL}
ADMIN_PASSWORD=${GEN_ADMIN_PASSWORD}
DB_PASSWORD=${GEN_DB_PASSWORD}
DJANGO_SECRET_KEY=${GEN_SECRET_KEY}"

    write_file "${OPT_PREFIX}/.credentials" "$creds_content" "0600"
    if [[ "$OPT_MODE" == "bare-metal" ]]; then
        chown fossilrepo:fossilrepo "${OPT_PREFIX}/.credentials"
    fi
    printf "  Credentials also saved to: ${_C_BOLD}%s/.credentials${_C_RESET} (mode 0600)\n" "$OPT_PREFIX"
    printf "\n"
}

# ============================================================================
# Section 13: Validation + Secret Generation + Main Dispatcher
# ============================================================================

validate_options() {
    # Validate mode
    case "$OPT_MODE" in
        docker|bare-metal) ;;
        *) die "Invalid mode: '$OPT_MODE'. Must be 'docker' or 'bare-metal'." ;;
    esac

    # Validate port
    if ! [[ "$OPT_PORT" =~ ^[0-9]+$ ]] || ((OPT_PORT < 1 || OPT_PORT > 65535)); then
        die "Invalid port: $OPT_PORT"
    fi

    # Validate domain (basic check)
    if [[ -z "$OPT_DOMAIN" ]]; then
        die "Domain must not be empty"
    fi

    # Default admin email
    if [[ -z "$OPT_ADMIN_EMAIL" ]]; then
        OPT_ADMIN_EMAIL="${OPT_ADMIN_USER}@${OPT_DOMAIN}"
    fi

    # S3: if bucket is set, region should be too
    if [[ -n "$OPT_S3_BUCKET" && -z "$OPT_S3_REGION" ]]; then
        OPT_S3_REGION="us-east-1"
    fi

    # Warn about SSL on localhost
    if [[ "$OPT_SSL" == "true" && ( "$OPT_DOMAIN" == "localhost" || "$OPT_DOMAIN" == "127.0.0.1" ) ]]; then
        log_warn "SSL is enabled but domain is '$OPT_DOMAIN' -- Let's Encrypt will not work. Disabling SSL."
        OPT_SSL="false"
    fi
}

auto_generate_secrets() {
    # Generate any secrets not provided by the user
    GEN_SECRET_KEY="$(generate_secret_key)"
    verbose "Generated Django secret key"

    if [[ -n "$OPT_DB_PASSWORD" ]]; then
        GEN_DB_PASSWORD="$OPT_DB_PASSWORD"
    else
        GEN_DB_PASSWORD="$(generate_password 32)"
        verbose "Generated database password"
    fi

    if [[ -n "$OPT_ADMIN_PASSWORD" ]]; then
        GEN_ADMIN_PASSWORD="$OPT_ADMIN_PASSWORD"
    else
        GEN_ADMIN_PASSWORD="$(generate_password 24)"
        verbose "Generated admin password"
    fi
}

show_config_summary() {
    printf "\n"
    log_step "Configuration"
    printf "  %-24s %s\n" "Mode:" "$OPT_MODE"
    printf "  %-24s %s\n" "Domain:" "$OPT_DOMAIN"
    printf "  %-24s %s\n" "SSL:" "$OPT_SSL"
    printf "  %-24s %s\n" "Port:" "$OPT_PORT"
    printf "  %-24s %s\n" "Install prefix:" "$OPT_PREFIX"
    printf "  %-24s %s\n" "Database:" "${OPT_DB_NAME} (user: ${OPT_DB_USER})"
    printf "  %-24s %s\n" "Admin:" "${OPT_ADMIN_USER} <${OPT_ADMIN_EMAIL}>"
    if [[ -n "$OPT_S3_BUCKET" ]]; then
        printf "  %-24s %s (region: %s)\n" "S3 backup:" "$OPT_S3_BUCKET" "${OPT_S3_REGION:-us-east-1}"
    else
        printf "  %-24s %s\n" "S3 backup:" "disabled"
    fi
    printf "\n"
}

main() {
    _color_init
    parse_args "$@"
    require_root
    detect_os

    # Run interactive TUI if mode is not set
    if [[ -z "$OPT_MODE" ]]; then
        run_interactive
    fi

    validate_options
    auto_generate_secrets
    show_config_summary
    confirm "Begin installation?"
    check_and_install_deps

    if [[ "$OPT_MODE" == "docker" ]]; then
        install_docker
    else
        install_bare_metal
    fi

    generate_uninstall_script
    show_summary
}

main "$@"
