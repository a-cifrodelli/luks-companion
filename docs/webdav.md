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

When running `./scripts/install-webdav.sh`, the installer automatically reads your configured mount points from `.env` and populates `/etc/webdav/config.yaml`:

```yaml
# ===================================================================
# WEBDAV SERVER CONFIGURATION FOR LUKS MANAGER
# ===================================================================
address: 0.0.0.0
port: 9088
directory: "/srv/storage"
prefix: /
permissions: CRUD

users:
  - username: "admin"
    password: "{bcrypt}$2a$10$e83B1...YourBcryptHashHere..."
    permissions: CRUD
```

---

## ⚙️ Systemd Service Template (`config/webdav.service.template`)

The systemd unit definition resides in `config/webdav.service.template`. The installer copies it to `/etc/systemd/system/webdav.service`:

```ini
[Unit]
Description=WebDAV Server Daemon for LUKS Manager
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/webdav -c /etc/webdav/config.yaml
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
4. Enter Folder URL: `https://webdav.your-domain.lan` or `http://your-rpi-ip:9088`.
5. Enter credentials when prompted. Photo thumbnails will render automatically in Extra Large Icons mode.

---

## ⚠️ Critical Fix: Windows WebDAV 50MB File Size Limit

By default, the Windows **WebClient** native service enforces an arbitrary maximum download/copy limit of **50 MB** (52,428,800 bytes). If you attempt to copy or open a file larger than 50MB over a mapped WebDAV drive on Windows, Windows Explorer will fail with error `0x800700DF`: *"The file size exceeds the limit allowed and cannot be saved."*

### How to Increase the Limit to 4GB (Maximum Allowed by Windows):

#### Option 1: Automated PowerShell Command (Run as Administrator)
Open **PowerShell** as Administrator on your Windows machine and execute:

```powershell
Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\WebClient\Parameters" -Name "FileSizeLimitInBytes" -Value 4294967295 -Type DWord
Restart-Service WebClient
```

#### Option 2: Manual Registry Edit (`regedit.exe`)
1. Press `Win + R`, type `regedit`, and press **Enter**.
2. Navigate to:
   `HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\WebClient\Parameters`
3. Double-click **`FileSizeLimitInBytes`**.
4. Change **Base** to **Decimal**.
5. Change value from `52428800` (50MB) to `4294967295` (4 GB, the maximum supported limit).
6. Click **OK**.
7. Restart the Windows WebClient service by opening **Command Prompt (Admin)** and running:
   ```cmd
   net stop WebClient
   net start WebClient
   ```

---

### 🍎 macOS (Finder)
1. Open **Finder** $\rightarrow$ Press `Cmd + K` (Connect to Server).
2. Enter Server Address: `https://your-rpi-ip:9088` (or your Traefik URL `https://webdav.your-domain.lan`).
3. Click **Connect** and enter your username/password.

### 🐧 Linux (Nautilus / Dolphin)
1. Open File Manager $\rightarrow$ **Other Locations** $\rightarrow$ **Connect to Server**.
2. Address: `davs://your-rpi-ip:9088` (or `dav://...` if HTTP).

---

## 🔄 LUKS Manager Integration & Group Permissions

In your `.env` file, set:

```env
ENABLE_WEBDAV=true
WEBDAV_PORT=9088
STORAGE_BASE="/srv/storage"
MAPPER_NAME="crypto_data"
LV_BACKUP="backup_data" # Optional secondary volume
STORAGE_GROUP="storage"
STORAGE_PERMS="2775"
RELOAD_SAMBA=false
```

### Clean Permission & Multi-Volume Model (No `chmod 777`):
1. The installer automatically creates a shared system group (`storage`) and adds your local user (`$SUDO_USER`) to it.
2. The base directory (`/srv/storage`) and all dynamically calculated mount points (`${STORAGE_BASE}/${MAPPER_NAME}`, `${STORAGE_BASE}/${LV_BACKUP}`) are mounted directly and protected with `STORAGE_GROUP` ownership and the **SGID bit** (`chmod 2775`).
3. WebDAV serves the unified storage root (`directory: "/srv/storage"`, `scope: "/"`), so all mounted volumes (`crypto_data/`, `backup_data/`, etc.) are immediately accessible and writable from the root URL.
4. On teardown, the script cleanly stops `webdav.service` before unmounting filesystems and initiating physical drive spindown.
