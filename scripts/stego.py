#!/usr/bin/env python3
# ===================================================================
# LUKS COMPANION STEGANOGRAPHY TOOL (Zero-Dependency Python Utility)
# Embeds and extracts LUKS keyfiles into/from PNG, JPEG, and WebP images
# Works on Arch Linux ARM, Debian, macOS, and Windows without AUR or external packages.
# ===================================================================
import sys
import os
import argparse
import hashlib
import hmac
import struct

MAGIC_HEADER = b"__LUKS_KEY_STEGO_V1__"

def derive_xor_keystream(passphrase: str, length: int, salt: bytes) -> bytes:
    """Derives a keystream of specified length using PBKDF2-HMAC-SHA256."""
    if not passphrase:
        return b"\x00" * length
    
    # Generate sufficient key blocks using PBKDF2
    derived = hashlib.pbkdf2_hmac(
        'sha256',
        passphrase.encode('utf-8'),
        salt,
        iterations=100000,
        dklen=length
    )
    return derived

def xor_bytes(data: bytes, key: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, key))

def embed_key(cover_image_path: str, keyfile_path: str, output_image_path: str, passphrase: str = ""):
    if not os.path.exists(cover_image_path):
        print(f"[!] ERRORE: Immagine di copertina '{cover_image_path}' non trovata!", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(keyfile_path):
        print(f"[!] ERRORE: File chiave '{keyfile_path}' non trovato!", file=sys.stderr)
        sys.exit(1)

    with open(cover_image_path, "rb") as f:
        cover_data = f.read()

    with open(keyfile_path, "rb") as f:
        key_data = f.read()

    key_len = len(key_data)
    if key_len == 0 or key_len > 65535:
        print("[!] ERRORE: Dimensione keyfile non valida (deve essere tra 1 e 65535 byte)!", file=sys.stderr)
        sys.exit(1)

    salt = os.urandom(16)
    keystream = derive_xor_keystream(passphrase, key_len, salt)
    encrypted_key = xor_bytes(key_data, keystream)

    # Compute HMAC tag for integrity
    mac_key = hashlib.sha256(salt + (passphrase.encode('utf-8') if passphrase else b"")).digest()
    auth_tag = hmac.new(mac_key, encrypted_key, hashlib.sha256).digest()[:16]

    # Payload format: MAGIC (21B) + Salt (16B) + AuthTag (16B) + KeyLength (4B) + EncryptedKey (N bytes)
    payload = MAGIC_HEADER + salt + auth_tag + struct.pack(">I", key_len) + encrypted_key

    with open(output_image_path, "wb") as f:
        f.write(cover_data + payload)

    print(f"[✓] Keyfile ({key_len} bytes) incorporato con successo in: {output_image_path}")
    print(f"    - Dimensione immagine finale: {len(cover_data) + len(payload)} bytes")
    if passphrase:
        print("    - Protezione: Cifrato con PBKDF2-HMAC-SHA256 (100.000 iterazioni)")
    else:
        print("    - Protezione: Incorporamento raw (senza passphrase aggiuntiva)")

def extract_key(image_path: str, passphrase: str = "", output_file: str = None):
    if not os.path.exists(image_path):
        print(f"[!] ERRORE: Immagine '{image_path}' non trovata!", file=sys.stderr)
        sys.exit(1)

    with open(image_path, "rb") as f:
        data = f.read()

    idx = data.rfind(MAGIC_HEADER)
    if idx == -1:
        print(f"[!] ERRORE: Nessun payload keyfile trovato nell'immagine '{image_path}'!", file=sys.stderr)
        sys.exit(1)

    payload = data[idx + len(MAGIC_HEADER):]
    if len(payload) < 36: # 16 (salt) + 16 (tag) + 4 (length)
        print("[!] ERRORE: Payload stenografico corrotto o incompleto!", file=sys.stderr)
        sys.exit(1)

    salt = payload[:16]
    auth_tag = payload[16:32]
    key_len = struct.unpack(">I", payload[32:36])[0]
    encrypted_key = payload[36:36 + key_len]

    if len(encrypted_key) != key_len:
        print("[!] ERRORE: Lunghezza dati non corrispondente!", file=sys.stderr)
        sys.exit(1)

    # Verify HMAC tag
    mac_key = hashlib.sha256(salt + (passphrase.encode('utf-8') if passphrase else b"")).digest()
    expected_tag = hmac.new(mac_key, encrypted_key, hashlib.sha256).digest()[:16]

    if not hmac.compare_digest(auth_tag, expected_tag):
        print("[!] ERRORE: Autenticazione fallita! Passphrase errata o payload manomesso.", file=sys.stderr)
        sys.exit(1)

    keystream = derive_xor_keystream(passphrase, key_len, salt)
    original_key = xor_bytes(encrypted_key, keystream)

    if output_file:
        with open(output_file, "wb") as f:
            f.write(original_key)
        print(f"[✓] Keyfile estratto con successo in: {output_file} ({key_len} bytes)")
    else:
        # Write binary key directly to stdout (for pipe into luks-manager)
        sys.stdout.buffer.write(original_key)
        sys.stdout.buffer.flush()

def main():
    parser = argparse.ArgumentParser(
        description="LUKS Companion Zero-Dependency Steganography Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Esempi di utilizzo:
  1. Embedda un keyfile dentro un'immagine:
     python3 scripts/stego.py embed -c photo.jpg -k vault.key -o secret_photo.jpg -p "mia_password"

  2. Estrai la chiave su file:
     python3 scripts/stego.py extract -i secret_photo.jpg -p "mia_password" -o vault.key

  3. Sblocco LUKS diretto via Pipe in RAM (Zero scrittura su disco):
     python3 scripts/stego.py extract -i secret_photo.jpg -p "mia_password" | ./luks-manager.sh unlock
"""
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: embed
    embed_parser = subparsers.add_parser("embed", help="Embedda un keyfile dentro un'immagine")
    embed_parser.add_argument("-c", "--cover", required=True, help="Percorso dell'immagine originale di copertina")
    embed_parser.add_argument("-k", "--keyfile", required=True, help="Percorso del keyfile da nascondere")
    embed_parser.add_argument("-o", "--output", required=True, help="Percorso del file immagine di output")
    embed_parser.add_argument("-p", "--passphrase", default="", help="Passphrase opzionale di cifratura aggiuntiva")

    # Subcommand: extract
    extract_parser = subparsers.add_parser("extract", help="Estrae un keyfile da un'immagine")
    extract_parser.add_argument("-i", "--image", required=True, help="Percorso dell'immagine contenente la chiave")
    extract_parser.add_argument("-p", "--passphrase", default="", help="Passphrase usata in fase di embedding")
    extract_parser.add_argument("-o", "--output", default=None, help="Percorso file output (se omesso invia i byte su stdout)")

    args = parser.parse_args()

    if args.command == "embed":
        embed_key(args.cover, args.keyfile, args.output, args.passphrase)
    elif args.command == "extract":
        extract_key(args.image, args.passphrase, args.output)

if __name__ == "__main__":
    main()
