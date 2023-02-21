#!/bin/bash
set -e
set -o noglob


GITHUB_URL=https://github.com/mlrun/mlrun-setup/releases
DOWNLOADER=

# --- helper functions for logs ---
input()
{
    echo '[INPUT] ' "$@"
}

info()
{
    echo '[INFO] ' "$@"
}
warn()
{
    echo '[WARN] ' "$@" >&2
}
fatal()
{
    echo '[ERROR] ' "$@" >&2
    exit 1
}

# --- add quotes to command arguments ---
quote() {
    for arg in "$@"; do
        printf '%s\n' "$arg" | sed "s/'/'\\\\''/g;1s/^/'/;\$s/\$/'/"
    done
}

# --- add indentation and trailing slash to quoted args ---
quote_indent() {
    printf ' \\\n'
    for arg in "$@"; do
        printf '\t%s \\\n' "$(quote "$arg")"
    done
}

# --- escape most punctuation characters, except quotes, forward slash, and space ---
escape() {
    printf '%s' "$@" | sed -e 's/\([][!#$%&()*;<=>?\_`{|}]\)/\\\1/g;'
}

# --- escape double quotes ---
escape_dq() {
    printf '%s' "$@" | sed -e 's/"/\\"/g'
}

# --- ensures $MCONF_URL is empty or begins with https://, exiting fatally otherwise ---
verify_mlsetup_url() {
    case "${MCONF_URL}" in
        "")
            ;;
        https://*)
            ;;
        *)
            fatal "Only https:// URLs are supported for MCONF_URL (have ${MCONF_URL})"
            ;;
    esac
}

# --- get hashes of the current k3s bin and service files
get_installed_hashes() {
    $SUDO sha256sum ${BIN_DIR}/mlsetup ${FILE_MCONF_SERVICE} ${FILE_MCONF_ENV} 2>&1 || true
}

# --- define needed environment variables ---
setup_env() {
    verify_mlsetup_url
    CMD_MCONF_EXEC="${CMD_MCONF}$(quote_indent "$@")"

    # --- use sudo if we are not already root ---
    SUDO=sudo
    if [ $(id -u) -eq 0 ]; then
        SUDO=
    fi

    # --- use binary install directory if defined or create default ---
    if [ -n "${INSTALL_MCONF_BIN_DIR}" ]; then
        BIN_DIR=${INSTALL_MCONF_BIN_DIR}
    else
        # --- use /usr/local/bin if root can write to it, otherwise use /opt/bin if it exists
        BIN_DIR=/usr/local/bin
        if ! $SUDO sh -c "touch ${BIN_DIR}/mlsetup-ro-test && rm -rf ${BIN_DIR}/mlsetup-ro-test"; then
            if [ -d /opt/bin ]; then
                BIN_DIR=/opt/bin
            fi
        fi
    fi

    # --- use systemd directory if defined or create default ---
    if [ -n "${INSTALL_MCONF_SYSTEMD_DIR}" ]; then
        SYSTEMD_DIR="${INSTALL_MCONF_SYSTEMD_DIR}"
    else
        SYSTEMD_DIR=/etc/systemd/system
    fi


    # --- get hash of config & exec for currently installed mlsetup ---
    PRE_INSTALL_HASHES=$(get_installed_hashes)

    # --- if bin directory is read only skip download ---
    if [ "${INSTALL_MCONF_BIN_DIR_READ_ONLY}" = true ]; then
        INSTALL_MCONF_SKIP_DOWNLOAD=true
    fi

}

# --- check if skip download environment variable set ---
can_skip_download_binary() {
    if [ "${INSTALL_MCONF_SKIP_DOWNLOAD}" != true ] && [ "${INSTALL_MCONF_SKIP_DOWNLOAD}" != binary ]; then
        return 1
    fi
}

can_skip_download_selinux() {
    if [ "${INSTALL_MCONF_SKIP_DOWNLOAD}" != true ] && [ "${INSTALL_MCONF_SKIP_DOWNLOAD}" != selinux ]; then
        return 1
    fi
}

# --- verify an executable mlsetup binary is installed ---
verify_mlsetup_is_executable() {
    if [ ! -x ${BIN_DIR}/mlsetup ]; then
        fatal "Executable mlsetup binary not found at ${BIN_DIR}/mlsetup"
    fi
}

# --- set arch and suffix, fatal if architecture not supported ---
setup_verify_arch() {
    OSTYPE=$(uname -s | tr '[:upper:]' '[:lower:]')
    if [ -z "$ARCH" ]; then
        ARCH=$(uname -m)
    fi
    case $ARCH in
        amd64)
            ARCH=amd64
            SUFFIX="-${OSTYPE}-amd64"
            ;;
        x86_64)
            ARCH=amd64
            SUFFIX="-${OSTYPE}-amd64"
            ;;
        arm64)
            ARCH=arm64
            SUFFIX=-${ARCH}
            ;;
        s390x)
            ARCH=s390x
            SUFFIX=-${ARCH}
            ;;
        aarch64)
            ARCH=arm64
            SUFFIX=-${ARCH}
            ;;
        arm*)
            ARCH=arm
            SUFFIX=-${ARCH}hf
            ;;
        *)
            fatal "Unsupported architecture $ARCH"
    esac
}

# --- verify existence of network downloader executable ---
verify_downloader() {
    # Return failure if it doesn't exist or is no executable
    [ -x "$(command -v $1)" ] || return 1

    # Set verified executable as our downloader program and return success
    DOWNLOADER=$1
    return 0
}

# --- create temporary directory and cleanup when done ---
setup_tmp() {
    TMP_DIR=$(mktemp -d -t mlsetup-install.XXXXXXXXXX)
    TMP_HASH=${TMP_DIR}/mlsetup.hash
    TMP_BIN=${TMP_DIR}/mlsetup.bin
    cleanup() {
        code=$?
        set +e
        trap - EXIT
        rm -rf ${TMP_DIR}
        exit $code
    }
    trap cleanup INT EXIT
}

# --- use desired mlsetup version if defined or find version from channel ---
get_release_version() {
    VERSION_MCONF=$(curl -L -s -H 'Accept: application/json' ${GITHUB_URL}/latest | awk -F, '{print $2}'| tr -d '"'| awk -F: '{print $2}')
    info "Using ${VERSION_MCONF} as release"
}

# --- download from github url ---
download() {
    [ $# -eq 2 ] || fatal 'download needs exactly 2 arguments'

    case $DOWNLOADER in
        curl)
            curl -o $1 -sfL $2
            ;;
        wget)
            wget -qO $1 $2
            ;;
        *)
            fatal "Incorrect executable '$DOWNLOADER'"
            ;;
    esac

    # Abort if download command failed
    [ $? -eq 0 ] || fatal 'Download failed'
}

# --- download hash from github url ---
download_hash() {
    HASH_URL=${GITHUB_URL}/download/${VERSION_MCONF}/sha256sum${SUFFIX}.txt
    info "Downloading hash ${HASH_URL}"
    download ${TMP_HASH} ${HASH_URL}
    HASH_EXPECTED=$( cat ${TMP_HASH})
    HASH_EXPECTED=${HASH_EXPECTED%%[[:blank:]]*}
}

# --- check hash against installed version ---
installed_hash_matches() {
    if [ -x ${BIN_DIR}/mlsetup ]; then
        HASH_INSTALLED=$(sha256sum ${BIN_DIR}/mlsetup)
        HASH_INSTALLED=${HASH_INSTALLED%%[[:blank:]]*}
        if [ "${HASH_EXPECTED}" = "${HASH_INSTALLED}" ]; then
            return
        fi
    fi
    return 1
}

# --- download binary from github url ---
download_binary() {
    BIN_URL=${GITHUB_URL}/download/${VERSION_MCONF}/mlsetup${SUFFIX}
    info "Downloading binary ${BIN_URL}"
    download ${TMP_BIN} ${BIN_URL}
}

# --- verify downloaded binary hash ---
verify_binary() {
    info "Verifying binary download"
    HASH_BIN=$(sha256sum ${TMP_BIN})
    HASH_BIN=${HASH_BIN%%[[:blank:]]*}
    if [ "${HASH_EXPECTED}" != "${HASH_BIN}" ]; then
        fatal "Download sha256 does not match ${HASH_EXPECTED}, got ${HASH_BIN}"
    fi
}

# --- setup permissions and move binary to system directory ---
setup_binary() {
    info "Installing mlsetup to ${BIN_DIR}/mlsetup"
    $SUDO  chmod 755 ${TMP_BIN}/mlsetup
    $SUDO chown root:root ${TMP_BIN}
    $SUDO mv -f ${TMP_BIN} ${BIN_DIR}/mlsetup
}


# --- download and verify mlsetup ---
download_and_verify() {
    if can_skip_download_binary; then
       info 'Skipping mlsetup download and verify'
       verify_mlsetup_is_executable
       return
    fi

    setup_verify_arch
    verify_downloader curl || verify_downloader wget || fatal 'Can not find curl or wget for downloading files'
    setup_tmp
    get_release_version
    download_hash

    if installed_hash_matches; then
        info 'Skipping binary downloaded, installed mlsetup matches hash'
        return
    fi

    download_binary
    verify_binary
    setup_binary
}








main() {
echo ""
mlsetup
echo ""
cat << EOF
Exmaples:
* mlsetup local -> install mlrunce as process
* mlsetup docker -> install mlrunce with docker-compose
* mlsetup kubernetes -r local -> install mlrunce on kubernetes and create local docker registry
EOF


}

# --- run the install process --
{
    setup_env "$@"
    download_and_verify
    main
}
