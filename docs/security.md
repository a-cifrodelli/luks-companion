# Security Architecture & Key Management Guide 🛡️

This document covers the **threat model**, **LUKS2 key management**, **steganographic key embedding**, and **zero-persistence RAM hygiene** implemented in `luks-companion`.

---

## 🎯 Threat Model & Zero-Standby Architecture

| Threat Scenario | Protection Mechanism | Result |
| :--- | :--- | :--- |
| **Server/MicroSD Theft** | No keyfiles or passphrases stored on Raspberry Pi storage. | Storage remains an opaque encrypted block device (LUKS2 Argon2id). |
| **Physical Disk Interception** | 220V relay cutoff + SCSI spindown + landing ramp park. | Heads are parked safely; disk contains encrypted ciphertext. |
| **Malicious Network Scan** | WebDAV / WebApp / LVM / LUKS are completely stopped when idle. | No active listening services or open mount points during standby. |
| **Web Server Breach** | Web backend runs as unprivileged user communicating over UNIX socket. | Attacker cannot access root shell or steal master encryption keys. |

---

## 🔑 LUKS Keyfile Management

LUKS2 supports up to 32 independent **keyslots**. You can keep your existing interactive passphrase in Keyslot 0 and add an ephemeral high-entropy binary **Keyfile** in Keyslot 1.

### 1. Generating a Cryptographically Secure Keyfile (512 bytes)
On your client workstation (Linux, macOS, or WSL):

```bash
# Generate 512 bytes (4096 bits) of pure CSPRNG random entropy
dd if=/dev/urandom of=vault.key bs=512 count=1
chmod 600 vault.key
```

---

### 2. Adding the Keyfile to your LUKS Volume

To install the keyfile onto your LVM logical volume (e.g. `/dev/vg_nas/lv_crypto`):

```bash
# 1. Activate Volume Group (if not already active)
sudo vgchange -ay vg_nas

# 2. Add the keyfile to an available keyslot (prompt will ask for existing passphrase to authorize)
sudo cryptsetup luksAddKey /dev/vg_nas/lv_crypto vault.key

# 3. Verify keyslots
sudo cryptsetup luksDump /dev/vg_nas/lv_crypto
```

> [!TIP]
> **Multiple Keyslots**: Both your original passphrase and `vault.key` will work simultaneously to unlock the volume. If one is ever lost, you can use the other to recover or revoke the lost key.

---

### 3. Testing Keyfile Decryption

From CLI on the server:
```bash
# Direct CLI flag test
sudo ./luks-manager.sh unlock --keyfile /path/to/vault.key

# Or via stdin pipe (100% in-memory)
cat vault.key | sudo ./luks-manager.sh unlock
```

---

## 🖼️ Steganographic Keyfile Embedding

To protect your keyfile from plain filesystem discovery on your client device, you can hide the 512-byte keyfile **inside a normal image** (e.g., photo, wallpaper, album art).

---

### Method A: Steghide (JPEG / BMP)
`steghide` embeds encrypted data within the transform coefficients of JPEG images without noticeable visual degradation.

#### 1. Install `steghide` (on client)
* **Arch Linux**: `sudo pacman -S steghide`
* **Debian / Ubuntu**: `sudo apt install steghide`
* **macOS (Homebrew)**: `brew install steghide`

#### 2. Embed Keyfile into an Image
```bash
# Embeds vault.key into cover_photo.jpg (prompts for an optional stego passphrase)
steghide embed -cf cover_photo.jpg -ef vault.key -sf holiday_photo.jpg
```

#### 3. Extract Keyfile from Image on Demand
```bash
# Extract the hidden vault.key from holiday_photo.jpg
steghide extract -sf holiday_photo.jpg
```

#### 4. Direct Unlock One-Liner (Zero-Disk Extraction)
```bash
# Extracts key strictly to stdout pipe into luks-manager
steghide extract -sf holiday_photo.jpg -p "stego_passphrase" -xf - | ./luks-manager.sh unlock
```

---

### Method B: Native PNG/JPEG Appending (Pure Linux/macOS/Windows)
Because image viewers parse headers from the beginning and stop at the image EOF marker, appending data to the end of an image does not corrupt image display in image viewers:

#### 1. Hide Key at End of Image
```bash
# Concatenate image + marker + keyfile
cat wallpaper.jpg vault.key > vacation_photo.jpg
```

#### 2. Extract Last 512 Bytes Directly to LUKS Unlock
```bash
# Reads the exact last 512 bytes and pipes directly to luks-manager
tail -c 512 vacation_photo.jpg | ./luks-manager.sh unlock
```

---

## 🔒 WebApp & Socket RAM Hygiene

1. **Ephemeral Key Handling**:
   * When uploading a `.key` file or stego-extracted file in the WebApp, the browser reads the file as an `ArrayBuffer` in memory.
   * The binary bytes are sent over TLS / IPC to `/run/luks-manager.sock`.
   * The daemon pipes the bytes directly into `cryptsetup open ... --key-file -`.
   * The variable is immediately deallocated (`del key_payload`) and garbage-collected from RAM.
   * **Zero bytes are written to `/tmp`, swap, or log files**.

2. **Revoking a Compromised Keyfile**:
   If a keyfile or stego image is ever compromised:
   ```bash
   # Remove a keyfile from LUKS keyslot using your master passphrase
   sudo cryptsetup luksRemoveKey /dev/vg_nas/lv_crypto vault.key
   
   # Or kill specific keyslot (e.g., keyslot 1)
   sudo cryptsetup luksKillSlot /dev/vg_nas/lv_crypto 1
   ```
