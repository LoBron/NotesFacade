#!/usr/bin/env bash
set -Eeuo pipefail

config_dir="/config/.config/obsidian"
config_file="${config_dir}/obsidian.json"
autostart_file="/config/.config/labwc/autostart"
vault_id="${OBSIDIAN_VAULT_ID:?OBSIDIAN_VAULT_ID is required}"
puid="${PUID:?PUID is required}"
pgid="${PGID:?PGID is required}"

if [[ ! "${vault_id}" =~ ^[a-f0-9]{16,64}$ ]]; then
    echo "Invalid OBSIDIAN_VAULT_ID" >&2
    exit 1
fi

install -d -m 700 -o "${puid}" -g "${pgid}" "${config_dir}"

if [[ ! -e "${config_file}" ]]; then
    timestamp_ms="$(date +%s%3N)"
    temporary_file="$(mktemp "${config_dir}/obsidian.json.XXXXXX")"
    jq -n \
        --arg vault_id "${vault_id}" \
        --argjson timestamp_ms "${timestamp_ms}" \
        '{vaults: {($vault_id): {path: "/vault", ts: $timestamp_ms, open: true}}}' \
        >"${temporary_file}"
    chown "${puid}:${pgid}" "${temporary_file}"
    chmod 644 "${temporary_file}"
    mv -f "${temporary_file}" "${config_file}"
elif ! jq -e '.vaults | type == "object"' "${config_file}" >/dev/null 2>&1; then
    echo "Existing Obsidian vault registry is invalid; refusing to overwrite it" >&2
    exit 1
elif ! jq -e '.vaults | to_entries | any(.value.path == "/vault")' \
    "${config_file}" >/dev/null; then
    while jq -e --arg vault_id "${vault_id}" '.vaults | has($vault_id)' \
        "${config_file}" >/dev/null; do
        vault_id="$(tr -d '-' </proc/sys/kernel/random/uuid)"
    done
    timestamp_ms="$(date +%s%3N)"
    temporary_file="$(mktemp "${config_dir}/obsidian.json.XXXXXX")"
    jq \
        --arg vault_id "${vault_id}" \
        --argjson timestamp_ms "${timestamp_ms}" \
        '.vaults[$vault_id] = {path: "/vault", ts: $timestamp_ms, open: true}' \
        "${config_file}" >"${temporary_file}"
    chown "${puid}:${pgid}" "${temporary_file}"
    chmod 644 "${temporary_file}"
    mv -f "${temporary_file}" "${config_file}"
elif ! jq -e '.vaults | to_entries | any(.value.path == "/vault" and .value.open == true)' \
    "${config_file}" >/dev/null; then
    timestamp_ms="$(date +%s%3N)"
    temporary_file="$(mktemp "${config_dir}/obsidian.json.XXXXXX")"
    jq \
        --argjson timestamp_ms "${timestamp_ms}" \
        '.vaults |= with_entries(
            if .value.path == "/vault"
            then .value.open = true | .value.ts = $timestamp_ms
            else .
            end
        )' \
        "${config_file}" >"${temporary_file}"
    chown "${puid}:${pgid}" "${temporary_file}"
    chmod 644 "${temporary_file}"
    mv -f "${temporary_file}" "${config_file}"
fi

chown "${puid}:${pgid}" "${config_file}"
chmod 644 "${config_file}"

if [[ ! -f "${autostart_file}" ]]; then
    install -d -m 700 -o "${puid}" -g "${pgid}" "$(dirname "${autostart_file}")"
    install -m 644 -o "${puid}" -g "${pgid}" /defaults/autostart "${autostart_file}"
fi

if ! grep -q -- '--remote-debugging-port=9222' "${autostart_file}"; then
    temporary_file="$(mktemp "$(dirname "${autostart_file}")/autostart.XXXXXX")"
    awk '
        /^obsidian([[:space:]]|$)/ {
            sub(/^obsidian/, "obsidian --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222")
        }
        { print }
    ' "${autostart_file}" >"${temporary_file}"
    chown "${puid}:${pgid}" "${temporary_file}"
    chmod 644 "${temporary_file}"
    mv -f "${temporary_file}" "${autostart_file}"
fi
