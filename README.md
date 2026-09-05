# LUKS Manager 🔒⚡

[![Bash Shell](https://img.shields.io/badge/Shell-Bash-4EAA25.svg?logo=gnu-bash&logoColor=white)](https://www.gnu.org/software/bash/)
[![Linux Compatible](https://img.shields.io/badge/Linux-Kernel_6.x-FCC624.svg?logo=linux&logoColor=black)](https://kernel.org/)
[![Arch Linux ARM](https://img.shields.io/badge/OS-Arch_Linux_ARM-1793D1.svg?logo=arch-linux&logoColor=white)](https://archlinuxarm.org/)
[![Home Assistant](https://img.shields.io/badge/Home_Assistant-API-41BDF5.svg?logo=home-assistant&logoColor=white)](https://www.home-assistant.io/)
[![Security LUKS2](https://img.shields.io/badge/Security-LUKS2_Argon2id-red.svg?logo=shield&logoColor=white)](https://gitlab.com/cryptsetup/cryptsetup)
[![License MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

An on-demand, zero-standby-power LUKS2 & LVM storage subsystem orchestrator designed for 24/7 headless Linux servers (such as Raspberry Pi 5).

It combines **Home Assistant REST API smart plug control**, **LVM Volume Group activation**, **RAM-only LUKS2 passphrase decryption (`stdin`)**, and **clean SCSI spindown (`udisksctl power-off`)** to achieve true **0 Watt cold storage standby** with safe physical head parking.

---

## 📐 Architecture Overview

```mermaid
flowchart TD
    subgraph CLIENTS ["📱 / 💻 Client Tier (iOS, Android, Mac, PC)"]
        UserTrigger["User Trigger<br/><i>(On-Demand CLI / Web API)</i>"]
    end

    subgraph RPI ["🌐 Raspberry Pi 5 Host (Arch Linux ARM)"]
        Orchestrator["<b>LUKS Manager Orchestrator</b><br/><code>luks-manager.sh</code>"]
        
        subgraph SEC ["🔒 Security & RAM Layer"]
            LVM["<b>LVM2 Kernel Module</b><br/><code>vgchange -ay</code>"]
            LUKS["<b>LUKS2 / dm-crypt</b><br/><i>Argon2id Decryption via Stdin</i>"]
            Mounts["<b>Mounted Filesystems</b><br/><code>/mnt/crypto_data</code> & <code>/mnt/backup_data</code>"]
        end
        
        subgraph TEARDOWN ["⚡ Teardown & Active Verification"]
            Sync["<b>RAM Flush & Unmount</b><br/><code>sync -> umount -l</code>"]
            Purge["<b>Key Erasure & LVM Deactivate</b><br/><code>cryptsetup close -> vgchange -an</code>"]
            Spindown["<b>SCSI Spindown & Ramp Park</b><br/><code>udisksctl power-off</code>"]
            Verify["<b>Active Kernel Un-enumeration Check</b><br/><code>[ ! -b /dev/sdb ] && [ ! -d /sys/block/sdb ]</code>"]
        end
    end

    subgraph EXTERNAL ["🔌 Hardware & Home Assistant Infrastructure"]
        HA["<b>Home Assistant REST API</b><br/><code>https://homeassistant.local:8123</code>"]
        Tapo["<b>Smart Plug (Tapo P105)</b><br/><i>220V Relays</i>"]
        Drive[("<b>WD My Book 3.5 Drive</b><br/><i>SCSI / SES Bridge</i>")]
    end

    %% Flow Relationships
    UserTrigger --> Orchestrator
    Orchestrator -->|1. POST /turn_on| HA
    HA -->|Power ON| Tapo
    Tapo -.->|220V Feed| Drive
    Drive -.->|2. USB Kernel Enumeration| Orchestrator
    
    Orchestrator -->|3. Activate VG| LVM
    LVM -->|4. Decrypt via stdin| LUKS
    LUKS -->|5. Mount| Mounts
    
    Mounts -->|6. User Session Active| Orchestrator
    
    Orchestrator -->|7. Teardown Trigger| Sync
    Sync --> Purge
    Purge --> Spindown
    Spindown --> Verify
    Verify -->|8. SCSI STOP UNIT Confirmed| Drive
    Verify -->|9. Verified Disconnect -> POST /turn_off| HA
    HA -->|0W Standby Cutoff| Tapo

    %% Styling
    classDef default font-family:sans-serif;
    style RPI fill:#1a1c23,stroke:#3b82f6,stroke-width:2px,color:#fff
    style EXTERNAL fill:#131b26,stroke:#10b981,stroke-width:2px,color:#fff
    style SEC fill:#1e293b,stroke:#f59e0b,stroke-width:1px,color:#fff
    style TEARDOWN fill:#1e293b,stroke:#ef4444,stroke-width:1px,color:#fff
    style CLIENTS fill:#0f172a,stroke:#6366f1,stroke-width:2px,color:#fff
```

> [!IMPORTANT]
> **Active Safety Verification**: Before sending the 220V power cutoff command to Home Assistant, `luks-manager` actively queries the Linux kernel `/sys/block/` tree and device node table until kernel un-enumeration is 100% confirmed. This guarantees that SCSI head parking and USB bus ejection are complete before turning off the outlet.

---

## ✨ Features

- 🔋 **0 Watt Standby Consumption**: Cuts 220V power via Home Assistant smart plug automation when not in use.
- 🛡️ **Hardware Preservation**: Uses SCSI `START STOP UNIT` (`udisksctl power-off`) and active kernel un-enumeration polling to safely park heads on landing ramps before 220V power cut.
- 🔑 **Strict RAM Hygiene**: Passphrase is read via `stdin` (`--key-file -`) directly into kernel memory (`dm-crypt`). Never written to disk, CLI args, or shell history.
- 📦 **LVM2 + LUKS2 Support**: Handles complex multi-volume LVM setups containing both encrypted and plain partitions.
- 🌐 **Decoupled Home Assistant Integration**: Communicates via standard HTTPS REST API using Long-Lived Access Tokens.
- ⚙️ **Fully Configurable**: All credentials, paths, entity IDs, and timeouts are stripped into `.env`.

---

## 🛠️ Prerequisites

Ensure your Linux host has the required utilities installed:

### On Arch Linux ARM
```bash
sudo pacman -S --needed cryptsetup lvm2 udisks2 psmisc curl
```

### On Debian / Raspberry Pi OS
```bash
sudo apt update && sudo apt install -y cryptsetup lvm2 udisks2 psmisc curl
```

---

## 🚀 Installation & Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/your-username/luks-manager.git
   cd luks-manager
   ```

2. **Configure your environment**:
   Copy `.env.example` to `.env` and fill in your Home Assistant URL, token, entity ID, and LVM names:
   ```bash
   cp .env.example .env
   nano .env
   ```

3. **Make scripts executable**:
   ```bash
   chmod +x luks-manager.sh test_ha_tapo.sh
   ```

---

## 🧪 Unit Testing Home Assistant Integration

Before mounting drives, verify your Home Assistant connection and smart plug entity ID:

```bash
./test_ha_tapo.sh
```

---

## 💻 Usage

Run the main orchestrator script:

```bash
./luks-manager.sh
```

### Workflow Execution Steps:
1. **Power-ON**: Calls HA REST API to power on the smart plug.
2. **Device Detection**: Waits for Linux kernel udev to detect the USB storage device.
3. **LVM Activation**: Runs `vgchange -ay <VG_NAME>`.
4. **LUKS Decryption**: Prompts for your passphrase securely without echoing.
5. **Mount**: Mounts encrypted and plain volumes to `/mnt/...`.
6. **Active Session**: Keeps volume available for file sharing. Press `[ENTER]` when done.
7. **Safe Teardown**: Flushes RAM buffers (`sync`), unmounts filesystems, closes LUKS, deactivates LVM VGs, parks SCSI heads (`udisksctl power-off`), verifies device un-enumeration in `/sys/block/`, and powers OFF the 220V smart plug via Home Assistant (0W).

---

## 🔒 Security Best Practices

> [!TIP]
> Always restrict permissions on your `.env` file to prevent local user reading:
> ```bash
> chmod 600 .env
> ```

- Never commit your `.env` file! It is ignored by `.gitignore`.
- Use a Home Assistant **Long-Lived Access Token** restricted to the required entity scope if possible.

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for details.
