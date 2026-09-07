# Security Architecture & Key Management Guide 🛡️

This document covers the **threat model**, **LUKS2 key management**, **steganographic key embedding**, and **zero-persistence RAM hygiene** implemented in `luks-companion`.

---

## 🎯 Threat Model & Zero-Standby Architecture

| Threat Scenario | Protection Mechanism | Result |
| :--- | :--- | :--- |
| **Server/MicroSD Theft** | No keyfiles or passphrases stored on Raspberry Pi storage. | Storage remains an opaque encrypted block device (LUKS2 Argon2id). |
| **Physical Disk Interception** | 220V relay cutoff + SCSI spindown + landing ramp park. | Heads are parked safely; disk contains encrypted ciphertext. |
| **Malicious Network Scan** | WebDAV / WebApp / LVM / LUKS are completely stopped when idle. | No active listening services or open mount points during standby. |
| **Web Server Breach** | Web backend runs as unprivileged `luks-web` user communicating over UNIX socket (`0660`). | Attacker cannot access root shell or steal master encryption keys. |

---

## 🛡️ Privilege Dropping & Socket IPC Isolation

To comply with the **Principle of Least Privilege**:
1. **Separation of Concerns**:
   - The **Master Daemon** runs as `root` because Linux storage management (`cryptsetup`, `vgchange`, `mount`, `udisksctl`) requires kernel block device privileges (`CAP_SYS_ADMIN`).
   - The **Web Gateway** (`luks-web.service`) runs as an isolated system user `luks-web` (`/usr/bin/nologin`).
2. **Restricted UNIX Socket Permissions (`0660`)**:
   - The IPC socket `/run/luks-manager.sock` is owned by `root:luks-web` with file permissions `0660` (`srw-rw----`).
   - Ordinary non-system users on the host cannot read from or write to the socket.
   - Even if an attacker gains arbitrary remote code execution within the Web gateway, they are constrained within the unprivileged `luks-web` account and can only submit structured JSON API requests (`status`, `unlock`, `stop`, `diagnose`).

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

## 🖼️ Steganographic Keyfile Embedding (Zero-AUR / Built-in Tool)

To avoid compiling tools from AUR or installing heavy external dependencies, the repository includes a standalone Python steganography utility: **`scripts/stego.py`**.

It works natively using only the standard Python library on **Arch Linux ARM**, **Ubuntu/Debian**, **macOS**, and **Windows**.

---

### 1. Embed Keyfile into Any Image (PNG, JPG, WebP)
Embeds `vault.key` into `photo.jpg`, optionally protected with a secondary PBKDF2-HMAC-SHA256 passphrase:

```bash
# Semplice:
python3 scripts/stego.py embed -c photo.jpg -k vault.key -o secret_photo.jpg

# Con ulteriore cifratura a passphrase:
python3 scripts/stego.py embed -c photo.jpg -k vault.key -o secret_photo.jpg -p "tua_password_segreta"
```
*L'immagine `secret_photo.jpg` appare e si apre normalmente in qualsiasi visualizzatore di immagini (anteprima, galleria, browser).*

---

### 2. Extract Keyfile from Image

#### Option A: Direct Unlock One-Liner via RAM Pipe (Zero Disk Trace)
Estrae la chiave direttamente nei byte di input di `luks-manager.sh` senza mai salvare il file `.key` su disco:

```bash
python3 scripts/stego.py extract -i secret_photo.jpg -p "tua_password_segreta" | sudo ./luks-manager.sh unlock
```

#### Option B: Extract to a Local File
```bash
python3 scripts/stego.py extract -i secret_photo.jpg -p "tua_password_segreta" -o vault.key
```

---

### 3. Alternative: Native Append Method (No tools required)
```bash
# Embedding via cat:
cat wallpaper.jpg vault.key > secret_wallpaper.jpg

# Extraction via tail:
tail -c 512 secret_wallpaper.jpg | sudo ./luks-manager.sh unlock
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
