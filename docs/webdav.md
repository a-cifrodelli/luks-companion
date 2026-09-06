# WebDAV Subsystem Setup Guide 🌐

This document provides instructions for setting up, configuring, and integrating the standalone **WebDAV server** (`hacdias/webdav`) with `luks-manager` on Arch Linux ARM / Raspberry Pi 5.

---

## 🎯 Overview

Instead of running a heavy Samba (SMB) daemon, `luks-manager` supports serving decrypted storage using a **lightweight, standalone WebDAV server** written in Go.

### Advantages:
- 🚀 **Ultra-lightweight**: Uses less than 10MB RAM.
- 🔒 **Secure**: Native TLS 1.3 / HTTPS encryption.
- 🖼️ **Full Thumbnail Support**: Supports HTTP Range headers and WebDAV locking (`PROPFIND`, `LOCK`, `UNLOCK`), rendering image and video thumbnails natively on **Windows Explorer**, **Mac Finder**, and **Linux File Managers**.
- ⚡ **Zero-Exposure**: Started when the LUKS volume is unlocked (`systemctl start webdav`) and killed prior to teardown (`systemctl stop webdav`).

---

## 📥 Binary Installation

### Automated Installation (Recommended)
Run the built-in installer script included in this repository:

```bash
sudo ./scripts/install-webdav.sh
```

### Manual Installation
Download the official static pre-compiled binary for `linux-arm64`:

```bash
# 1. Download and extract binary
curl -fsSL https://github.com/hacdias/webdav/releases/latest/download/linux-arm64-webdav.tar.gz | tar -xz

# 2. Install executable
sudo mv webdav /usr/local/bin/webdav
sudo chmod +x /usr/local/bin/webdav

# 3. Verify installation
webdav -h
```

---

## ⚙️ Configuration Template (`config/webdav.yaml.template`)

The repository includes a clean configuration template in `config/webdav.yaml.template`.

When running `./scripts/install-webdav.sh`, the installer automatically reads `MOUNT_CRYPTO` from your `.env` file and populates `/etc/webdav/config.yaml`:

```yaml
# ===================================================================
# WEBDAV SERVER CONFIGURATION FOR LUKS MANAGER
# ===================================================================
address: 0.0.0.0
port: 8443
cert: "" # Optional: Path to TLS certificate (/etc/ssl/certs/server.crt)
key: ""  # Optional: Path to TLS private key (/etc/ssl/certs/server.key)
auth: true

users:
  - username: "admin"
    # Generate bcrypt hash via: python3 -c 'import bcrypt; print(bcrypt.hashpw(b"your_password", bcrypt.gensalt()).decode())'
    password: "$2a$10$e83B1...YourBcryptHashHere..."
    scope: "/mnt/crypto_data"
    modify: true
```

---

## ⚙️ Systemd Service (`/etc/systemd/system/webdav.service`)

Create the systemd unit file to allow `luks-manager` to start and stop the server:

```ini
[Unit]
Description=WebDAV Server Daemon for LUKS Manager
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/webdav --config /etc/webdav/config.yaml
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Reload systemd daemon:
```bash
sudo systemctl daemon-reload
```

---

## 💻 Client Connection Guide

### 🪟 Windows (File Explorer)
1. Open **File Explorer** $\rightarrow$ **This PC**.
2. Click **Map Network Drive**.
3. Choose drive letter (e.g. `Z:`).
4. Enter Folder URL: `https://your-rpi-ip:8443` or `http://your-rpi-ip:8443`.
5. Enter credentials when prompted. Photo thumbnails will render automatically in Extra Large Icons mode.

### 🍎 macOS (Finder)
1. Open **Finder** $\rightarrow$ Press `Cmd + K` (Connect to Server).
2. Enter Server Address: `https://your-rpi-ip:8443`.
3. Click **Connect** and enter your username/password.

### 🐧 Linux (Nautilus / Dolphin)
1. Open File Manager $\rightarrow$ **Other Locations** $\rightarrow$ **Connect to Server**.
2. Address: `davs://your-rpi-ip:8443` (or `dav://...` if HTTP).

---

## 🔄 LUKS Manager Integration

In your `.env` file, set:

```env
RELOAD_SAMBA=false
ENABLE_WEBDAV=true
```

When `luks-manager.sh` executes:
1. LUKS volume is decrypted and mounted to `/mnt/crypto_data`.
2. Script runs `sudo systemctl start webdav`.
3. WebDAV is accessible across your network.
4. On teardown, script runs `sudo systemctl stop webdav` before drive unmount and 220V power cutoff.
