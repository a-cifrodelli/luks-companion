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

```
[ Your Devices (iOS / Android / Mac / PC) ]
                     │
                     │ On-Demand CLI / API Trigger
                     ▼
  ┌─────────────────────────────────────────────────────────┐
  │ Raspberry Pi 5 / Arch Linux ARM (24/7 Headless Host)   │
  │                                                         │
  │  1. HA REST API -> Turn ON Smart Plug (Tapo P105)       │
  │  2. Kernel udev -> Wait for USB device (/dev/sdb)      │
  │  3. LVM2 -> vgchange -ay <VG_NAME>                      │
  │  4. LUKS2 -> cryptsetup open via stdin (RAM-only)       │
  │  5. Mount -> /mnt/crypto_data & /mnt/backup_data        │
  │  6. Refresh File Shares (Samba / FileBrowser)           │
  │                                                         │
  │  ... ACTIVE SESSION ...                                 │
  │                                                         │
  │  7. Teardown -> sync -> umount -> cryptsetup close      │
  │  8. LVM2 -> vgchange -an <VG_NAME>                      │
  │  9. SCSI -> udisksctl power-off (Head Parking)          │
  │ 10. HA REST API -> Turn OFF Smart Plug (0W Standby)     │
  └──────────────────────────┬──────────────────────────────┘
                             │
                             ▼
  ┌─────────────────────────────────────────────────────────┐
  │ External 3.5" WD My Book (SCSI / SES Bridge)            │
  └─────────────────────────────────────────────────────────┘
```

---

## ✨ Features

- 🔋 **0 Watt Standby Consumption**: Cuts 220V power via Home Assistant smart plug automation when not in use.
- 🛡️ **Hardware Preservation**: Uses SCSI `START STOP UNIT` (`udisksctl power-off`) to safely park heads on landing ramps before 220V power cut.
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
7. **Safe Teardown**: Flushes RAM buffers (`sync`), unmounts filesystems, closes LUKS, deactivates LVM VGs, parks SCSI heads (`udisksctl power-off`), and powers OFF the 220V smart plug via Home Assistant (0W).

---

## 🔒 Security Best Practices

- Never commit your `.env` file! It is ignored by `.gitignore`.
- Set restrictive file permissions on `.env`:
  ```bash
  chmod 600 .env
  ```
- Use a Home Assistant **Long-Lived Access Token** restricted to the required entity scope if possible.

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for details.
