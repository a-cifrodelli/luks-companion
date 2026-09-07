"""
POSIX Privilege Dropping utility for security hardening.
Safely drops root privileges to an unprivileged user and group.
"""
import os
import sys
from typing import Tuple, Optional


def resolve_user_group(user_name: str, group_name: Optional[str] = None) -> Tuple[int, int]:
    """
    Resolves user and group names to numeric UID and GID.
    """
    try:
        import pwd
        import grp
    except ImportError:
        # Non-POSIX systems (e.g. Windows during development)
        return (1000, 1000)

    try:
        user_entry = pwd.getpwnam(user_name)
        target_uid = user_entry.pw_uid
        target_gid = user_entry.pw_gid
    except KeyError as exc:
        raise ValueError(f"Utente '{user_name}' non trovato nel sistema.") from exc

    if group_name:
        try:
            group_entry = grp.getgrnam(group_name)
            target_gid = group_entry.gr_gid
        except KeyError as exc:
            raise ValueError(f"Gruppo '{group_name}' non trovato nel sistema.") from exc

    return (target_uid, target_gid)


def drop_privileges(user_name: str, group_name: Optional[str] = None) -> bool:
    """
    Executes the standard POSIX privilege reduction sequence:
    1. Check if running as root (EUID/UID == 0)
    2. Reset supplementary groups: os.setgroups([])
    3. Switch GID: os.setgid(target_gid)
    4. Switch UID: os.setuid(target_uid)
    5. Verify UID/GID transition
    Returns True if privileges were successfully dropped, False if not root (e.g. already dropped).
    """
    if not hasattr(os, "getuid") or not hasattr(os, "setuid"):
        # Non-POSIX environment
        return False

    if os.getuid() != 0:
        # Already running as unprivileged user
        return False

    target_uid, target_gid = resolve_user_group(user_name, group_name)

    # 1. Reset auxiliary groups
    if hasattr(os, "setgroups"):
        try:
            os.setgroups([])
        except PermissionError as e:
            raise RuntimeError(f"Impossibile reimpostare i gruppi supplementari: {e}") from e

    # 2. Set primary Group ID first
    try:
        os.setgid(target_gid)
    except PermissionError as e:
        raise RuntimeError(f"Impossibile impostare il GID a {target_gid}: {e}") from e

    # 3. Set primary User ID
    try:
        os.setuid(target_uid)
    except PermissionError as e:
        raise RuntimeError(f"Impossibile impostare l'UID a {target_uid}: {e}") from e

    # 4. Verify privilege transition
    if os.getuid() != target_uid or os.getgid() != target_gid:
        raise RuntimeError(
            f"Verifica privilege drop fallita: UID={os.getuid()} (atteso {target_uid}), GID={os.getgid()} (atteso {target_gid})"
        )

    return True
