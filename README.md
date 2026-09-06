# LUKS Manager 🔒⚡

[![Bash Shell](https://img.shields.io/badge/Shell-Bash-4EAA25.svg?logo=gnu-bash&logoColor=white)](https://www.gnu.org/software/bash/)
[![Linux Compatible](https://img.shields.io/badge/Linux-Kernel_6.x-FCC624.svg?logo=linux&logoColor=black)](https://kernel.org/)
[![Arch Linux ARM](https://img.shields.io/badge/OS-Arch_Linux_ARM-1793D1.svg?logo=arch-linux&logoColor=white)](https://archlinuxarm.org/)
[![Home Assistant](https://img.shields.io/badge/Home_Assistant-API-41BDF5.svg?logo=home-assistant&logoColor=white)](https://www.home-assistant.io/)
[![Security LUKS2](https://img.shields.io/badge/Security-LUKS2_Argon2id-red.svg?logo=shield&logoColor=white)](https://gitlab.com/cryptsetup/cryptsetup)
[![License MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

An on-demand, zero-standby-power LUKS2 & LVM storage subsystem orchestrator designed for 24/7 headless Linux servers (such as Raspberry Pi 5).

It combines **Home Assistant REST API smart plug control**, **LVM Volume Group activation**, **RAM-only LUKS2 passphrase decryption (`stdin`)**, **lightweight WebDAV sharing**, and **clean SCSI spindown (`udisksctl power-off`)** to achieve true **0 Watt cold storage standby** with safe physical head parking.

---

## 📐 Architecture Overview

```mermaid
flowchart TD
    subgraph CLIENTS ["💻 Client Tier (Web App, CLI, WebDAV)"]
        CLI["Interactive CLI<br/><code>./luks-manager.sh</code>"]
        WebApp["Web App / API Client<br/><i>JSON over UNIX Socket</i>"]
    end

    subgraph DAEMON ["⚙️ Daemon Subsystem (daemon/)"]
        SockDaemon["<b>luks-managerd</b><br/><code>/run/luks-manager.sock</code>"]
    end

    subgraph RPI ["🌐 Host System (Arch Linux ARM / Raspberry Pi 5)"]
        Orchestrator["<b>LUKS Manager Core</b><br/><code>luks-manager.sh [start|stop|status]</code>"]
        
        subgraph SEC ["🔒 Security & RAM Layer"]
            LVM["<b>LVM2 Kernel Module</b><br/><code>vgchange -ay</code>"]
            LUKS["<b>LUKS2 / dm-crypt</b><br/><i>Argon2id Decryption via Stdin</i>"]
            Mounts["<b>Mounted Filesystems</b><br/><code>/mnt/crypto_data</code> & <code>/mnt/backup_data</code>"]
            WebDAV["<b>WebDAV Server</b><br/><code>/srv/webdav</code> <i>(Isolated Bind-Mount)</i>"]
        end
        
        subgraph TEARDOWN ["⚡ Teardown & Active Verification"]
            Sync["<b>RAM Flush & Unmount</b><br/><code>sync -> umount -l</code>"]
            Purge["<b>Key Erasure & LVM Deactivate</b><br/><code>cryptsetup close -> vgchange -an</code>"]
            Spindown["<b>SCSI Spindown & Ramp Park</b><br/><code>udisksctl power-off</code>"]
            Verify["<b>Active Kernel Un-enumeration Check</b><br/><code>[ ! -b /dev/sdX ] && [ ! -d /sys/block/sdX ]</code>"]
        end
    end

    subgraph EXTERNAL ["🔌 Hardware & Home Assistant Infrastructure"]
        HA["<b>Home Assistant REST API</b><br/><code>https://homeassistant.local:8123</code>"]
        Tapo["<b>Smart Plug (Tapo P105)</b><br/><i>220V Relays</i>"]
        Drive[("<b>WD My Book 3.5 Drive</b><br/><i>SCSI / SES Bridge</i>")]
    end

    %% Flow Relationships
    CLI --> Orchestrator
    WebApp --> SockDaemon
    SockDaemon --> Orchestrator

    Orchestrator -->|1. POST /turn_on| HA
    HA -->|Power ON| Tapo
    Tapo -.->|220V Feed| Drive
    Drive -.->|2. USB Kernel Enumeration| Orchestrator
    
    Orchestrator -->|3. Activate VG| LVM
    LVM -->|4. Decrypt via stdin| LUKS
    LUKS -->|5. Mount| Mounts
    Mounts -->|6. Bind-Mount & Start| WebDAV
    
    Orchestrator -->|7. Teardown Trigger (stop)| Sync
    Sync --> Purge
    Purge --> Spindown
    Spindown --> Verify
    Verify -->|8. SCSI STOP UNIT Confirmed| Drive
    Verify -->|9. Verified Disconnect -> POST /turn_off| HA
    HA -->|0W Standby Cutoff| Tapo
```

> [!IMPORTANT]
> **Active Safety Verification**: Before sending the 220V power cutoff command to Home Assistant, `luks-manager` actively queries the Linux kernel `/sys/block/` tree and device node table until kernel un-enumeration is 100% confirmed. This guarantees that SCSI head parking and USB bus ejection are complete before turning off the outlet.

---

## ✨ Features

- 🔋 **0 Watt Standby Consumption**: Cuts 220V power via Home Assistant smart plug automation when not in use.
- 🛡️ **Hardware Preservation**: Uses SCSI `START STOP UNIT` (`udisksctl power-off`) and active kernel un-enumeration polling to safely park heads on landing ramps before 220V power cut.
- 🔑 **Strict RAM Hygiene**: Passphrase is read via `stdin` (`--key-file -`) directly into kernel memory (`dm-crypt`). Never written to disk, CLI args, or shell history.
- 📦 **LVM2 + LUKS2 Support**: Handles complex multi-volume LVM setups containing both encrypted and plain partitions.
- 🌐 **Dedicated Socket Daemon (`daemon/`)**: Provides a non-root UNIX domain socket (`/run/luks-manager.sock`) for seamless Web App integration.
- 📁 **Lightweight WebDAV Subsystem**: Native image/video thumbnail support with isolated bind-mount directory scoping (`/srv/webdav`).
- ⚙️ **Fully Atomic Subcommands**: Standalone `start` (with watchdog), `stop` (immediate safe teardown), and `status`.

---

## 🛠️ Prerequisites

Ensure your Linux host has the required utilities installed:

### On Arch Linux ARM
```bash
sudo pacman -S --needed cryptsetup lvm2 udisks2 psmisc curl socat python
```

### On Debian / Raspberry Pi OS
```bash
sudo apt update && sudo apt install -y cryptsetup lvm2 udisks2 psmisc curl socat python3
```

---

## 🚀 Installation & Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/your-username/luks-companion.git
   cd luks-companion
   ```

2. **Configure your environment**:
   Copy `.env.example` to `.env` and fill in your Home Assistant URL, token, entity ID, and LVM names:
   ```bash
   cp .env.example .env
   nano .env
   chmod 600 .env
   ```

3. **Make scripts executable**:
   ```bash
   chmod +x luks-manager.sh test_ha_tapo.sh daemon/install.sh scripts/install-webdav.sh
   ```

---

## 💻 CLI Usage

The core script `luks-manager.sh` supports atomic subcommands:

### 1. Avvio Interattivo (Start / Unlock)
Powers on the plug, activates LVM, decrypts LUKS via interactive passphrase prompt, mounts filesystems, starts WebDAV, and enters the idle watchdog loop:
```bash
./luks-manager.sh
# oppure: ./luks-manager.sh start
```
*Press `[ENTER]` at any time to initiate safe teardown and power cutoff.*

### 2. Arresto Immediato (Stop / Lock)
Safely stops WebDAV, unmounts filesystems, seals LUKS, deactivates LVM, parks drive heads, and cuts 220V power:
```bash
./luks-manager.sh stop
```

### 3. Verifica Stato (Status)
Inspects live power, LVM, LUKS, mount, and WebDAV state:
```bash
./luks-manager.sh status
```

---

## 🌐 Socket Daemon (`daemon/`)

To allow unprivileged local Web Apps to query status, unlock, or lock storage without needing `sudo` or SSH, install the background socket daemon:

```bash
sudo ./daemon/install.sh
```

### Testing the Socket API
The socket listens at `/run/luks-manager.sock` (`chmod 0666`):

- **Query Status**:
  ```bash
  echo '{"action": "status"}' | socat - UNIX-CONNECT:/run/luks-manager.sock
  ```
- **Unlock Volume**:
  ```bash
  echo '{"action": "unlock", "passphrase": "your_passphrase"}' | socat - UNIX-CONNECT:/run/luks-manager.sock
  ```
- **Stop / Safe Teardown**:
  ```bash
  echo '{"action": "stop"}' | socat - UNIX-CONNECT:/run/luks-manager.sock
  ```

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for details.
