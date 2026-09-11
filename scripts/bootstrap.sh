#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
readonly REPO_ROOT
readonly ENV_FILE="${REPO_ROOT}/.env"
readonly PROJECTS_FILE="${REPO_ROOT}/config/projects.json"
readonly PLUGIN_VERSION="5.1.0"
readonly PLUGIN_ID="obsidian-local-rest-api"
readonly PLUGIN_BASE_URL="https://github.com/coddingtonbear/obsidian-local-rest-api/releases/download/${PLUGIN_VERSION}"
readonly DEFAULT_TIMEOUT_SECONDS="${BOOTSTRAP_TIMEOUT_SECONDS:-240}"
readonly ACTIVATION_TIMEOUT_SECONDS="${OBSIDIAN_ACTIVATION_TIMEOUT_SECONDS:-${DEFAULT_TIMEOUT_SECONDS}}"

declare -a TEMP_PATHS=()
CURRENT_STAGE="initialization"
OS_FAMILY=""

cleanup() {
    local path
    for path in "${TEMP_PATHS[@]}"; do
        [[ -n "${path}" ]] && rm -rf -- "${path}"
    done
    return 0
}

on_error() {
    local exit_code=$?
    local line_number="${1:-unknown}"
    printf '\nERROR: bootstrap failed during "%s" (line %s, exit %s).\n' \
        "${CURRENT_STAGE}" "${line_number}" "${exit_code}" >&2
    printf 'Diagnostics: docker compose ps; docker compose logs --tail=200 obsidian facade\n' >&2
    printf 'No vault, note, or Docker volume was deleted.\n' >&2
    exit "${exit_code}"
}

trap cleanup EXIT
trap 'on_error "$LINENO"' ERR

log() {
    printf '==> %s\n' "$*"
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

confirm() {
    local prompt="$1"
    local answer
    read -r -p "${prompt} [y/N]: " answer
    [[ "${answer}" =~ ^[Yy]([Ee][Ss])?$ ]]
}

require_supported_platform() {
    [[ "${BASH_VERSINFO[0]}" -ge 3 ]] || die "Bash 3 or newer is required."
    case "$(uname -s)" in
        Darwin)
            OS_FAMILY="macos"
            ;;
        Linux)
            OS_FAMILY="linux"
            [[ -r /etc/os-release ]] ||
                die "Cannot identify Linux distribution (/etc/os-release is missing)."

            # shellcheck disable=SC1091
            source /etc/os-release
            case "${ID:-}" in
                ubuntu|debian)
                    OS_ID="${ID}"
                    OS_CODENAME="${VERSION_CODENAME:-}"
                    ;;
                *)
                    if [[ " ${ID_LIKE:-} " == *" ubuntu "* &&
                        -n "${UBUNTU_CODENAME:-}" ]]; then
                        OS_ID="ubuntu"
                        OS_CODENAME="${UBUNTU_CODENAME}"
                    elif [[ " ${ID_LIKE:-} " == *" debian "* ]]; then
                        OS_ID="debian"
                        OS_CODENAME="${DEBIAN_CODENAME:-${VERSION_CODENAME:-}}"
                    else
                        die "Unsupported Linux distribution '${ID:-unknown}'; expected Ubuntu or Debian (including WSL2)."
                    fi
                    ;;
            esac
            [[ -n "${OS_CODENAME}" ]] ||
                die "VERSION_CODENAME is missing from /etc/os-release."
            ;;
        *)
            die "Only macOS, Ubuntu, Debian, and WSL2 based on them are supported."
            ;;
    esac
}

as_root() {
    if [[ "${EUID}" -eq 0 ]]; then
        "$@"
    else
        command -v sudo >/dev/null 2>&1 || die "sudo is required to install system dependencies."
        sudo "$@"
    fi
}

install_dependencies() {
    local need_docker=false
    local need_curl=false
    local need_checksum=false

    command -v docker >/dev/null 2>&1 || need_docker=true
    docker compose version >/dev/null 2>&1 || need_docker=true
    command -v curl >/dev/null 2>&1 || need_curl=true
    has_checksum_tool || need_checksum=true

    if [[ "${need_docker}" == false && "${need_curl}" == false &&
        "${need_checksum}" == false ]]; then
        return
    fi

    printf 'Missing dependencies:'
    [[ "${need_docker}" == true ]] && printf ' Docker Engine/Compose'
    [[ "${need_curl}" == true ]] && printf ' curl'
    [[ "${need_checksum}" == true ]] && printf ' SHA-256 utility'
    printf '\n'
    confirm "Install missing system dependencies" ||
        die "Installation declined; no system packages were changed."

    CURRENT_STAGE="system dependency installation"
    if [[ "${OS_FAMILY}" == "macos" ]]; then
        if ! command -v brew >/dev/null 2>&1; then
            [[ "${need_curl}" == false ]] ||
                die "macOS curl is required to install Homebrew."
            confirm "Homebrew is missing; install it from brew.sh" ||
                die "Homebrew installation declined."
            NONINTERACTIVE=1 /bin/bash -c \
                "$(curl --fail --silent --show-error --location https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
            if [[ -x /opt/homebrew/bin/brew ]]; then
                eval "$(/opt/homebrew/bin/brew shellenv)"
            elif [[ -x /usr/local/bin/brew ]]; then
                eval "$(/usr/local/bin/brew shellenv)"
            else
                die "Homebrew was installed but cannot be found in a standard location."
            fi
        fi
        [[ "${need_curl}" == false ]] || brew install curl
        [[ "${need_checksum}" == false ]] || brew install coreutils
        [[ "${need_docker}" == false ]] || brew install --cask docker
        return
    fi

    as_root apt-get update
    as_root apt-get install -y ca-certificates curl gnupg
    [[ "${need_checksum}" == true ]] && as_root apt-get install -y coreutils

    if [[ "${need_docker}" == true ]]; then
        as_root install -m 0755 -d /etc/apt/keyrings
        curl --fail --silent --show-error --location \
            https://download.docker.com/linux/"${OS_ID}"/gpg |
            as_root tee /etc/apt/keyrings/docker.asc >/dev/null
        as_root chmod a+r /etc/apt/keyrings/docker.asc
        local architecture
        architecture="$(dpkg --print-architecture)"
        printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' \
            "${architecture}" "${OS_ID}" "${OS_CODENAME}" |
            as_root tee /etc/apt/sources.list.d/docker.list >/dev/null
        as_root apt-get update
        as_root apt-get install -y docker-ce docker-ce-cli containerd.io \
            docker-buildx-plugin docker-compose-plugin
        if command -v systemctl >/dev/null 2>&1; then
            as_root systemctl enable --now docker || true
        else
            as_root service docker start || true
        fi
    fi
}

ensure_docker_access() {
    docker compose version >/dev/null 2>&1 ||
        die "Docker Compose v2 is unavailable after dependency checks."
    if docker info >/dev/null 2>&1; then
        return
    fi
    if [[ "${OS_FAMILY}" == "macos" ]]; then
        log "Starting Docker Desktop for macOS."
        if docker desktop start >/dev/null 2>&1; then
            :
        elif command -v open >/dev/null 2>&1; then
            open -a Docker
        else
            die "Docker Desktop is installed but cannot be started."
        fi
        local deadline=$((SECONDS + DEFAULT_TIMEOUT_SECONDS))
        while ((SECONDS < deadline)); do
            docker info >/dev/null 2>&1 && return
            sleep 3
        done
        die "Docker Desktop did not become ready after ${DEFAULT_TIMEOUT_SECONDS}s. Complete its first-launch prompts and rerun bootstrap."
    fi
    if [[ "${EUID}" -ne 0 ]] && command -v sudo >/dev/null 2>&1 &&
        sudo docker info >/dev/null 2>&1; then
        confirm "Add user '${USER}' to the docker group (requires a new login)" ||
            die "Docker daemon access is required; group change was declined."
        as_root usermod -aG docker "${USER}"
        die "Docker group updated. Log out and back in (or run 'newgrp docker'), then rerun bootstrap."
    fi
    die "Docker daemon is unavailable. Start Docker and verify that 'docker info' succeeds."
}

dotenv_decode() {
    local value="$1"
    if [[ "${value}" == \'*\' && "${value}" == *\' ]]; then
        value="${value:1:${#value}-2}"
        value="${value//\\\'/\'}"
    elif [[ "${value}" == \"*\" && "${value}" == *\" ]]; then
        value="${value:1:${#value}-2}"
    fi
    printf '%s' "${value}"
}

load_env_file() {
    local line key raw
    [[ -f "${ENV_FILE}" ]] || return
    while IFS= read -r line || [[ -n "${line}" ]]; do
        [[ "${line}" =~ ^[[:space:]]*# || ! "${line}" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] && continue
        key="${line%%=*}"
        raw="${line#*=}"
        case "${key}" in
            COMPOSE_PROJECT_NAME|PROJECT_NAME|PROJECT_FOLDER|PROJECT_VAULT_PATH|OBSIDIAN_VAULT_ID|\
            OBSIDIAN_API_KEY|OBSIDIAN_API_URL|OBSIDIAN_CONNECT_TIMEOUT_SECONDS|\
            OBSIDIAN_READ_TIMEOUT_SECONDS|OBSIDIAN_WRITE_TIMEOUT_SECONDS|\
            OBSIDIAN_POOL_TIMEOUT_SECONDS|PROJECTS_CONFIG_PATH|VAULT_RO_PATH|\
            REVIEW_INBOX_DAYS|REVIEW_STALE_ACTIVE_DAYS|TZ|PUID|PGID|KASM_PASSWORD|\
            OBSIDIAN_HTTP_HOST_PORT|OBSIDIAN_HTTPS_HOST_PORT|FACADE_HOST_PORT)
                printf -v "${key}" '%s' "$(dotenv_decode "${raw}")"
                ;;
        esac
    done <"${ENV_FILE}"
}

dotenv_quote() {
    local value="$1"
    value="${value//\'/\\\'}"
    printf "'%s'" "${value}"
}

atomic_write() {
    local destination="$1"
    local mode="$2"
    local temporary
    mkdir -p -- "$(dirname -- "${destination}")"
    temporary="$(mktemp "$(dirname -- "${destination}")/.bootstrap.XXXXXX")"
    TEMP_PATHS+=("${temporary}")
    cat >"${temporary}"
    chmod "${mode}" "${temporary}"
    mv -f -- "${temporary}" "${destination}"
}

append_missing_env() {
    local temporary
    temporary="$(mktemp "${REPO_ROOT}/.env.bootstrap.XXXXXX")"
    TEMP_PATHS+=("${temporary}")
    cat "${ENV_FILE}" >"${temporary}"
    [[ -s "${temporary}" && "$(tail -c 1 "${temporary}" || true)" != "" ]] && printf '\n' >>"${temporary}"
    while (($#)); do
        local key="$1"
        local value="$2"
        shift 2
        if ! grep -qE "^${key}=" "${ENV_FILE}"; then
            printf '%s=%s\n' "${key}" "$(dotenv_quote "${value}")" >>"${temporary}"
        fi
    done
    chmod 600 "${temporary}"
    mv -f -- "${temporary}" "${ENV_FILE}"
}

random_hex() {
    local bytes="$1"
    od -An -N "${bytes}" -tx1 /dev/urandom | tr -d ' \n'
}

generate_uuid() {
    if [[ -r /proc/sys/kernel/random/uuid ]]; then
        cat /proc/sys/kernel/random/uuid
    elif command -v uuidgen >/dev/null 2>&1; then
        uuidgen | tr '[:upper:]' '[:lower:]'
    else
        die "Cannot generate UUID: /proc UUID source and uuidgen are unavailable."
    fi
}

has_checksum_tool() {
    command -v sha256sum >/dev/null 2>&1 ||
        command -v gsha256sum >/dev/null 2>&1 ||
        command -v shasum >/dev/null 2>&1
}

checksum_file() {
    local file="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "${file}" | awk '{print $1}'
    elif command -v gsha256sum >/dev/null 2>&1; then
        gsha256sum "${file}" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "${file}" | awk '{print $1}'
    else
        die "No SHA-256 utility is available."
    fi
}

validate_slug() {
    [[ "$1" =~ ^[a-z0-9][a-z0-9_-]{0,62}$ ]]
}

validate_project_name() {
    [[ -n "$1" && "${#1}" -le 100 && "$1" != *$'\n'* && "$1" != *$'\r'* ]]
}

validate_vault_path() {
    [[ "$1" == /* && "$1" != "/" && "$1" != *$'\n'* && "$1" != *$'\r'* ]] &&
        [[ "/$1/" != *"/../"* ]]
}

validate_numeric_id() {
    [[ "$1" =~ ^[0-9]+$ && "$1" -le 2147483647 ]]
}

validate_timezone() {
    [[ "$1" =~ ^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)+$ ]] &&
        [[ -f "/usr/share/zoneinfo/$1" ]]
}

validate_password() {
    [[ "${#1}" -ge 8 && "${#1}" -le 128 && "$1" != *$'\n'* && "$1" != *$'\r'* ]]
}

validate_port() {
    [[ "$1" =~ ^[0-9]+$ && "$1" -ge 1 && "$1" -le 65535 ]]
}

json_escape() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//$'\t'/\\t}"
    printf '%s' "${value}"
}

read_project_id() {
    [[ -r "${PROJECTS_FILE}" ]] || return 1
    grep -m1 -E '"id"[[:space:]]*:' "${PROJECTS_FILE}" |
        sed -E 's/.*"id"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/'
}

read_project_field() {
    local field="$1"
    [[ -r "${PROJECTS_FILE}" ]] || return 1
    grep -m1 -E "\"${field}\"[[:space:]]*:" "${PROJECTS_FILE}" |
        sed -E "s/.*\"${field}\"[[:space:]]*:[[:space:]]*\"([^\"]+)\".*/\\1/"
}

prompt_new_configuration() {
    local default_vault_path password_confirmation

    read -r -p "Initial project name [personal]: " PROJECT_NAME
    PROJECT_NAME="${PROJECT_NAME:-personal}"
    validate_project_name "${PROJECT_NAME}" || die "Project name must be non-empty and at most 100 characters."

    read -r -p "Project slug [personal]: " PROJECT_FOLDER
    PROJECT_FOLDER="${PROJECT_FOLDER:-personal}"
    validate_slug "${PROJECT_FOLDER}" ||
        die "Slug must match [a-z0-9][a-z0-9_-]{0,62}."

    default_vault_path="${HOME}/Vaults/${PROJECT_FOLDER}"
    read -r -p "Absolute project vault path [${default_vault_path}]: " PROJECT_VAULT_PATH
    PROJECT_VAULT_PATH="${PROJECT_VAULT_PATH:-${default_vault_path}}"
    validate_vault_path "${PROJECT_VAULT_PATH}" ||
        die "Vault path must be an absolute non-root path."
    while [[ "${PROJECT_VAULT_PATH}" != "/" && "${PROJECT_VAULT_PATH}" == */ ]]; do
        PROJECT_VAULT_PATH="${PROJECT_VAULT_PATH%/}"
    done

    read -r -p "Timezone [${TZ:-Europe/Moscow}]: " TZ
    TZ="${TZ:-Europe/Moscow}"
    validate_timezone "${TZ}" || die "Timezone is invalid or absent from /usr/share/zoneinfo."

    read -r -p "UID [$(id -u)]: " PUID
    PUID="${PUID:-$(id -u)}"
    validate_numeric_id "${PUID}" || die "UID must be an integer from 0 to 2147483647."

    read -r -p "GID [$(id -g)]: " PGID
    PGID="${PGID:-$(id -g)}"
    validate_numeric_id "${PGID}" || die "GID must be an integer from 0 to 2147483647."

    read -r -s -p "Obsidian UI password (8-128 characters): " KASM_PASSWORD
    printf '\n'
    validate_password "${KASM_PASSWORD}" || die "Password must contain 8-128 characters."
    read -r -s -p "Repeat Obsidian UI password: " password_confirmation
    printf '\n'
    [[ "${KASM_PASSWORD}" == "${password_confirmation}" ]] || die "Passwords do not match."

    COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-notesfacade}"
    OBSIDIAN_VAULT_ID="$(random_hex 16)"
    OBSIDIAN_API_KEY="$(random_hex 32)"
    OBSIDIAN_API_URL="http://obsidian:27123"
    OBSIDIAN_CONNECT_TIMEOUT_SECONDS="5"
    OBSIDIAN_READ_TIMEOUT_SECONDS="20"
    OBSIDIAN_WRITE_TIMEOUT_SECONDS="20"
    OBSIDIAN_POOL_TIMEOUT_SECONDS="5"
    PROJECTS_CONFIG_PATH="/app/config/projects.json"
    VAULT_RO_PATH="/vault"
    REVIEW_INBOX_DAYS="7"
    REVIEW_STALE_ACTIVE_DAYS="30"
    OBSIDIAN_HTTP_HOST_PORT="${OBSIDIAN_HTTP_HOST_PORT:-3000}"
    OBSIDIAN_HTTPS_HOST_PORT="${OBSIDIAN_HTTPS_HOST_PORT:-3001}"
    FACADE_HOST_PORT="${FACADE_HOST_PORT:-8000}"
    validate_port "${OBSIDIAN_HTTP_HOST_PORT}" || die "OBSIDIAN_HTTP_HOST_PORT is invalid."
    validate_port "${OBSIDIAN_HTTPS_HOST_PORT}" || die "OBSIDIAN_HTTPS_HOST_PORT is invalid."
    validate_port "${FACADE_HOST_PORT}" || die "FACADE_HOST_PORT is invalid."
    PROJECT_ID="$(generate_uuid)"

    printf '\nConfiguration summary (secrets hidden):\n'
    printf '  Project: %s (%s)\n' "${PROJECT_NAME}" "${PROJECT_FOLDER}"
    printf '  Vault: %s\n' "${PROJECT_VAULT_PATH}"
    printf '  Timezone: %s; UID:GID: %s:%s\n' "${TZ}" "${PUID}" "${PGID}"
    printf '  Local ports: UI %s/%s; MCP %s\n' \
        "${OBSIDIAN_HTTP_HOST_PORT}" "${OBSIDIAN_HTTPS_HOST_PORT}" "${FACADE_HOST_PORT}"
    confirm "Create configuration, adjust ownership, and start containers" ||
        die "Bootstrap cancelled; no project configuration was created."
}

write_initial_configuration() {
    CURRENT_STAGE="configuration creation"
    umask 077
    atomic_write "${ENV_FILE}" 600 <<EOF
COMPOSE_PROJECT_NAME=$(dotenv_quote "${COMPOSE_PROJECT_NAME}")
PROJECT_NAME=$(dotenv_quote "${PROJECT_NAME}")
PROJECT_FOLDER=$(dotenv_quote "${PROJECT_FOLDER}")
PROJECT_VAULT_PATH=$(dotenv_quote "${PROJECT_VAULT_PATH}")
OBSIDIAN_VAULT_ID=$(dotenv_quote "${OBSIDIAN_VAULT_ID}")
OBSIDIAN_API_KEY=$(dotenv_quote "${OBSIDIAN_API_KEY}")
OBSIDIAN_API_URL=$(dotenv_quote "${OBSIDIAN_API_URL}")
OBSIDIAN_CONNECT_TIMEOUT_SECONDS=${OBSIDIAN_CONNECT_TIMEOUT_SECONDS}
OBSIDIAN_READ_TIMEOUT_SECONDS=${OBSIDIAN_READ_TIMEOUT_SECONDS}
OBSIDIAN_WRITE_TIMEOUT_SECONDS=${OBSIDIAN_WRITE_TIMEOUT_SECONDS}
OBSIDIAN_POOL_TIMEOUT_SECONDS=${OBSIDIAN_POOL_TIMEOUT_SECONDS}
PROJECTS_CONFIG_PATH=$(dotenv_quote "${PROJECTS_CONFIG_PATH}")
VAULT_RO_PATH=$(dotenv_quote "${VAULT_RO_PATH}")
REVIEW_INBOX_DAYS=${REVIEW_INBOX_DAYS}
REVIEW_STALE_ACTIVE_DAYS=${REVIEW_STALE_ACTIVE_DAYS}
TZ=$(dotenv_quote "${TZ}")
PUID=${PUID}
PGID=${PGID}
KASM_PASSWORD=$(dotenv_quote "${KASM_PASSWORD}")
OBSIDIAN_HTTP_HOST_PORT=${OBSIDIAN_HTTP_HOST_PORT}
OBSIDIAN_HTTPS_HOST_PORT=${OBSIDIAN_HTTPS_HOST_PORT}
FACADE_HOST_PORT=${FACADE_HOST_PORT}
EOF

    atomic_write "${PROJECTS_FILE}" 600 <<EOF
{
  "projects": [
    {
      "id": "$(json_escape "${PROJECT_ID}")",
      "name": "$(json_escape "${PROJECT_NAME}")",
      "folder": "$(json_escape "${PROJECT_FOLDER}")"
    }
  ]
}
EOF
}

populate_missing_env_defaults() {
    PROJECT_NAME="${PROJECT_NAME:-$(read_project_field name || true)}"
    PROJECT_NAME="${PROJECT_NAME:-personal}"
    PROJECT_FOLDER="${PROJECT_FOLDER:-$(read_project_field folder || true)}"
    PROJECT_FOLDER="${PROJECT_FOLDER:-personal}"
    PROJECT_VAULT_PATH="${PROJECT_VAULT_PATH:-${HOME}/Vaults/${PROJECT_FOLDER}}"
    COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-notesfacade}"
    OBSIDIAN_VAULT_ID="${OBSIDIAN_VAULT_ID:-$(random_hex 16)}"
    OBSIDIAN_HTTP_HOST_PORT="${OBSIDIAN_HTTP_HOST_PORT:-3000}"
    OBSIDIAN_HTTPS_HOST_PORT="${OBSIDIAN_HTTPS_HOST_PORT:-3001}"
    FACADE_HOST_PORT="${FACADE_HOST_PORT:-8000}"
}

validate_loaded_configuration() {
    validate_project_name "${PROJECT_NAME:-}" || die "PROJECT_NAME in .env is invalid."
    validate_slug "${PROJECT_FOLDER:-}" || die "PROJECT_FOLDER in .env is invalid."
    validate_vault_path "${PROJECT_VAULT_PATH:-}" || die "PROJECT_VAULT_PATH in .env must be absolute."
    validate_timezone "${TZ:-}" || die "TZ in .env is invalid."
    validate_numeric_id "${PUID:-}" || die "PUID in .env is invalid."
    validate_numeric_id "${PGID:-}" || die "PGID in .env is invalid."
    validate_password "${KASM_PASSWORD:-}" || die "KASM_PASSWORD in .env must contain 8-128 characters."
    [[ "${OBSIDIAN_API_KEY:-}" =~ ^[a-fA-F0-9]{64}$ ]] ||
        die "OBSIDIAN_API_KEY in .env must be a 256-bit hexadecimal value."
    [[ "${OBSIDIAN_VAULT_ID:-}" =~ ^[a-f0-9]{16,64}$ ]] ||
        die "OBSIDIAN_VAULT_ID in .env is invalid."
    validate_port "${OBSIDIAN_HTTP_HOST_PORT:-}" || die "OBSIDIAN_HTTP_HOST_PORT is invalid."
    validate_port "${OBSIDIAN_HTTPS_HOST_PORT:-}" || die "OBSIDIAN_HTTPS_HOST_PORT is invalid."
    validate_port "${FACADE_HOST_PORT:-}" || die "FACADE_HOST_PORT is invalid."
}

ensure_project_registry() {
    if [[ -f "${PROJECTS_FILE}" ]]; then
        PROJECT_ID="$(read_project_id)" || die "Cannot read project_id from config/projects.json."
        [[ "${PROJECT_ID}" =~ ^[0-9a-fA-F-]{36}$ ]] ||
            die "The existing project_id is not a UUID."
        return
    fi
    PROJECT_ID="$(generate_uuid)"
    atomic_write "${PROJECTS_FILE}" 600 <<EOF
{
  "projects": [
    {
      "id": "$(json_escape "${PROJECT_ID}")",
      "name": "$(json_escape "${PROJECT_NAME}")",
      "folder": "$(json_escape "${PROJECT_FOLDER}")"
    }
  ]
}
EOF
}

ensure_json_array_member() {
    local file="$1"
    local member="$2"
    if [[ ! -f "${file}" ]]; then
        printf '["%s"]\n' "${member}" | atomic_write "${file}" 600
        return
    fi
    grep -q "\"${member}\"" "${file}" && return
    local compact content
    content="$(tr -d '\n\r' <"${file}")"
    compact="${content//[[:space:]]/}"
    [[ "${compact}" == \[*\] ]] ||
        die "Refusing to modify malformed JSON array: ${file}"
    if [[ "${compact}" == "[]" ]]; then
        printf '["%s"]\n' "${member}" | atomic_write "${file}" 600
    else
        compact="${compact%]}"
        printf '%s,"%s"]\n' "${compact}" "${member}" | atomic_write "${file}" 600
    fi
}

ensure_core_plugins_config() {
    local file="$1"
    if [[ ! -f "${file}" ]]; then
        printf '{"file-recovery":true,"sync":false}\n' | atomic_write "${file}" 600
        return
    fi

    local compact
    compact="$(tr -d '[:space:]' <"${file}")"
    [[ "${compact}" == \{*\} ]] ||
        die "Existing core-plugins.json is not a JSON object; preserved without overwrite."
    if ! grep -qE '"file-recovery"[[:space:]]*:[[:space:]]*true' "${file}" ||
        ! grep -qE '"sync"[[:space:]]*:[[:space:]]*false' "${file}"; then
        die "Existing core-plugins.json must set file-recovery=true and sync=false; preserved without overwrite."
    fi
}

path_needs_ownership_repair() {
    local path="$1"
    [[ -d "${path}" ]] || die "Required directory is unavailable: ${path}"
    if [[ "${OS_FAMILY}" == "macos" ]]; then
        [[ -w "${path}" ]] ||
            die "Directory is not writable on macOS: ${path}"
        return 1
    fi
    [[ -n "$(find -P "${path}" -xdev \
        \( ! -uid "${PUID}" -o ! -gid "${PGID}" \) -print -quit)" ]]
}

repair_vault_ownership() {
    local path
    local needs_repair=false
    local -a paths=("${REPO_ROOT}/vault-root" "${PROJECT_VAULT_PATH}")

    for path in "${paths[@]}"; do
        if path_needs_ownership_repair "${path}"; then
            needs_repair=true
            break
        fi
    done
    [[ "${needs_repair}" == true ]] || return 0

    [[ "${OS_FAMILY}" == "linux" ]] ||
        die "Automatic ownership repair is supported only on Linux."
    log "Repairing mismatched ownership inside vault-root and the selected project vault."
    for path in "${paths[@]}"; do
        as_root find -P "${path}" -xdev \
            \( ! -uid "${PUID}" -o ! -gid "${PGID}" \) \
            -exec chown --no-dereference "${PUID}:${PGID}" {} +
    done
}

download_plugin_assets() {
    local plugin_dir="${REPO_ROOT}/vault-root/.obsidian/plugins/${PLUGIN_ID}"
    local asset expected destination temporary actual
    mkdir -p -- "${plugin_dir}"
    for asset in main.js manifest.json styles.css; do
        case "${asset}" in
            main.js)
                expected="c3bf3ef644c5ade946c4ab64821a5969a92124e00afb4dc9bac4034d482ce131"
                ;;
            manifest.json)
                expected="6c0d8390e6aa3f3515c834e2b4174545cf0cd20c5dbb116216e5d36b72af8437"
                ;;
            styles.css)
                expected="a8b5c52e4974bd356225a17d64e6ea25502206ad860641da260fdc14430426f8"
                ;;
            *)
                die "Unexpected plugin asset: ${asset}"
                ;;
        esac
        destination="${plugin_dir}/${asset}"
        if [[ -f "${destination}" ]] &&
            [[ "$(checksum_file "${destination}")" == "${expected}" ]]; then
            continue
        fi
        temporary="$(mktemp "${plugin_dir}/.${asset}.XXXXXX")"
        TEMP_PATHS+=("${temporary}")
        curl --fail --silent --show-error --location --connect-timeout 10 --max-time 180 \
            --retry 3 --retry-delay 2 -o "${temporary}" "${PLUGIN_BASE_URL}/${asset}"
        actual="$(checksum_file "${temporary}")"
        [[ "${actual}" == "${expected}" ]] ||
            die "SHA-256 mismatch for Local REST API ${PLUGIN_VERSION} asset ${asset}."
        chmod 600 "${temporary}"
        mv -f -- "${temporary}" "${destination}"
    done
}

prepare_vault() {
    CURRENT_STAGE="headless Obsidian configuration"
    mkdir -p -- "${REPO_ROOT}/vault-root" "${PROJECT_VAULT_PATH}"
    repair_vault_ownership
    mkdir -p -- "${REPO_ROOT}/vault-root/.obsidian/plugins/${PLUGIN_ID}"
    ensure_json_array_member "${REPO_ROOT}/vault-root/.obsidian/community-plugins.json" \
        "${PLUGIN_ID}"
    ensure_core_plugins_config "${REPO_ROOT}/vault-root/.obsidian/core-plugins.json"

    local app_file="${REPO_ROOT}/vault-root/.obsidian/app.json"
    if [[ ! -f "${app_file}" ]]; then
        printf '{"trashOption":"none"}\n' | atomic_write "${app_file}" 600
    elif ! grep -qE '"trashOption"[[:space:]]*:[[:space:]]*"none"' "${app_file}"; then
        die "Existing app.json does not set trashOption to none; preserved without overwrite."
    fi

    local data_file="${REPO_ROOT}/vault-root/.obsidian/plugins/${PLUGIN_ID}/data.json"
    if [[ ! -f "${data_file}" ]]; then
        atomic_write "${data_file}" 600 <<EOF
{
  "apiKey": "$(json_escape "${OBSIDIAN_API_KEY}")",
  "enableInsecureServer": true,
  "bindingHost": "0.0.0.0",
  "insecurePort": 27123
}
EOF
    else
        if ! grep -qE '"enableInsecureServer"[[:space:]]*:[[:space:]]*true' "${data_file}" ||
            ! grep -qE '"bindingHost"[[:space:]]*:[[:space:]]*"0\.0\.0\.0"' "${data_file}" ||
            ! grep -qE '"insecurePort"[[:space:]]*:[[:space:]]*27123' "${data_file}" ||
            ! grep -q '"apiKey"' "${data_file}"; then
            die "Existing plugin data.json is incompatible; preserved without exposing or overwriting it."
        fi
    fi
    download_plugin_assets
}

compose() {
    docker compose --project-directory "${REPO_ROOT}" --env-file "${ENV_FILE}" "$@"
}

wait_for_obsidian_rest() {
    local deadline=$((SECONDS + DEFAULT_TIMEOUT_SECONDS))
    while ((SECONDS < deadline)); do
        # shellcheck disable=SC2016  # CHECK_API_KEY expands inside the container.
        if compose exec -T -e CHECK_API_KEY="${OBSIDIAN_API_KEY}" obsidian sh -c \
            'curl --fail --silent --show-error --connect-timeout 2 --max-time 5 \
            -H "Authorization: Bearer ${CHECK_API_KEY}" http://127.0.0.1:27123/vault/ >/dev/null' \
            2>/dev/null; then
            return
        fi
        sleep 3
    done
    die "Timed out waiting for authenticated Obsidian REST API after ${DEFAULT_TIMEOUT_SECONDS}s."
}

wait_for_health() {
    local deadline=$((SECONDS + DEFAULT_TIMEOUT_SECONDS))
    local response
    while ((SECONDS < deadline)); do
        response="$(curl --fail --silent --show-error --connect-timeout 2 --max-time 5 \
            "http://127.0.0.1:${FACADE_HOST_PORT}/health" 2>/dev/null || true)"
        if grep -qE '"status"[[:space:]]*:[[:space:]]*"ok"' <<<"${response}"; then
            return
        fi
        sleep 3
    done
    die "Timed out waiting for facade health JSON status=ok after ${DEFAULT_TIMEOUT_SECONDS}s."
}

run_runtime_checks() {
    CURRENT_STAGE="runtime verification"
    local service
    for service in obsidian facade; do
        [[ "$(compose ps --status running --services | grep -xc "${service}")" -eq 1 ]] ||
            die "Container service '${service}' is not running."
    done
    wait_for_health
    compose exec -T facade python - --project-id "${PROJECT_ID}" \
        <"${REPO_ROOT}/scripts/check_runtime.py"
}

bootstrap() {
    cd "${REPO_ROOT}"
    CURRENT_STAGE="platform and dependency checks"
    require_supported_platform
    install_dependencies
    ensure_docker_access

    if [[ -f "${ENV_FILE}" ]]; then
        log "Existing .env detected; preserving secrets and configuration."
        load_env_file
        populate_missing_env_defaults
        validate_loaded_configuration
        confirm "Repair missing runtime components and verify the existing installation" ||
            die "Bootstrap cancelled; existing configuration was not changed."
        append_missing_env \
            COMPOSE_PROJECT_NAME "${COMPOSE_PROJECT_NAME}" \
            PROJECT_NAME "${PROJECT_NAME}" \
            PROJECT_FOLDER "${PROJECT_FOLDER}" \
            PROJECT_VAULT_PATH "${PROJECT_VAULT_PATH}" \
            OBSIDIAN_VAULT_ID "${OBSIDIAN_VAULT_ID}" \
            OBSIDIAN_HTTP_HOST_PORT "${OBSIDIAN_HTTP_HOST_PORT}" \
            OBSIDIAN_HTTPS_HOST_PORT "${OBSIDIAN_HTTPS_HOST_PORT}" \
            FACADE_HOST_PORT "${FACADE_HOST_PORT}"
        chmod 600 "${ENV_FILE}"
        load_env_file
    else
        prompt_new_configuration
        write_initial_configuration
    fi

    ensure_project_registry
    prepare_vault

    CURRENT_STAGE="Obsidian startup"
    log "Starting Obsidian and waiting for authenticated REST API."
    compose up -d obsidian
    compose exec -T obsidian python3 /opt/bootstrap/enable-community-plugin.py \
        --plugin-id "${PLUGIN_ID}" --timeout "${ACTIVATION_TIMEOUT_SECONDS}"
    wait_for_obsidian_rest

    CURRENT_STAGE="facade build and startup"
    log "Building and starting Notes Facade."
    compose up -d --build facade
    run_runtime_checks

    printf '\nBootstrap complete.\n'
    printf '  Obsidian UI: http://127.0.0.1:%s (login: abc)\n' "${OBSIDIAN_HTTP_HOST_PORT}"
    printf '  Vault: %s\n' "${PROJECT_VAULT_PATH}"
    printf '  project_id: %s\n' "${PROJECT_ID}"
    printf '  MCP endpoint: http://127.0.0.1:%s/mcp\n' "${FACADE_HOST_PORT}"
    printf '  Commands: make logs | make check-runtime | make down\n'
    printf '  API key and UI password were not printed.\n'
}

check_only() {
    cd "${REPO_ROOT}"
    CURRENT_STAGE="read-only runtime verification"
    require_supported_platform
    command -v docker >/dev/null 2>&1 || die "Docker is required."
    command -v curl >/dev/null 2>&1 || die "curl is required."
    has_checksum_tool || die "A SHA-256 utility is required."
    ensure_docker_access
    [[ -f "${ENV_FILE}" ]] || die ".env is missing; run bootstrap first."
    load_env_file
    OBSIDIAN_VAULT_ID="${OBSIDIAN_VAULT_ID:-0000000000000000}"
    populate_missing_env_defaults
    validate_loaded_configuration
    PROJECT_ID="$(read_project_id)" || die "Cannot read project_id from config/projects.json."
    run_runtime_checks
    printf 'Runtime check passed: health=ok, REST authenticated, 7 MCP tools, validate=0 errors.\n'
}

usage() {
    cat <<'EOF'
Usage:
  ./scripts/bootstrap.sh                  Configure, start, and verify the stack
  ./scripts/bootstrap.sh --check-runtime  Verify without changing configuration or notes
  ./scripts/bootstrap.sh --help
EOF
}

case "${1:-}" in
    "") bootstrap ;;
    --check-runtime) check_only ;;
    --help|-h) usage ;;
    *) usage >&2; exit 2 ;;
esac
