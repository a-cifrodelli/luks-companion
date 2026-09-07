"""
Tests for StorageEngine full startup and unlock lifecycle.
"""
import pytest
from luks_companion.core.engine import StorageEngine


def test_full_start_and_mount_sequence(mock_config, mock_runner, mock_ha):
    mock_ha.current_state = "on"
    disk_path = "/dev/sdc"
    lv_crypto = mock_config.lv_crypto_path
    lv_backup = mock_config.lv_backup_path
    mapper_path = mock_config.mapper_path
    mount_crypto = mock_config.mount_crypto
    mount_backup = mock_config.mount_backup

    # Register block device and LVM paths
    mock_runner.existing_paths.update([disk_path, lv_crypto, lv_backup])
    mock_runner.set_command_response("pvs --noheadings -o pv_name -S vg_name=mybook", stdout=disk_path)
    mock_runner.set_command_response("lsblk -s -rno PATH,TYPE " + disk_path, stdout=f"{disk_path} disk")

    engine = StorageEngine(mock_config, mock_runner, mock_ha)

    # Simulate cryptsetup creating mapper node upon successful open
    def on_cryptsetup_open(*args, **kwargs):
        mock_runner.existing_paths.add(mapper_path)

    res = engine.start(passphrase="my_secret_pass")
    assert res is True

    executed = [" ".join(cmd) for cmd in mock_runner.commands_history]

    # 1. HA power on checked
    assert mock_ha.turn_on_calls >= 1
    # 2. LVM scanned & activated
    assert any("vgscan --mknodes" in cmd for cmd in executed)
    assert any("vgchange -ay mybook" in cmd for cmd in executed)
    # 3. Cryptsetup open called
    assert any("cryptsetup open" in cmd for cmd in executed)
    # 4. Filesystem mounted
    assert any(f"mount {mapper_path} {mount_crypto}" in cmd for cmd in executed)
    assert any(f"chmod 2775 {mount_crypto}" in cmd for cmd in executed)
    # 5. Backup LV mounted
    assert any(f"mount {lv_backup} {mount_backup}" in cmd for cmd in executed)
    # 6. WebDAV restarted
    assert any("systemctl restart webdav" in cmd for cmd in executed)


def test_start_with_keyfile_bytes(mock_config, mock_runner, mock_ha):
    mock_ha.current_state = "on"
    disk_path = "/dev/sdc"
    lv_crypto = mock_config.lv_crypto_path
    mapper_path = mock_config.mapper_path

    mock_runner.existing_paths.update([disk_path, lv_crypto])
    mock_runner.set_command_response("pvs --noheadings -o pv_name -S vg_name=mybook", stdout=disk_path)
    mock_runner.set_command_response("lsblk -s -rno PATH,TYPE " + disk_path, stdout=f"{disk_path} disk")

    engine = StorageEngine(mock_config, mock_runner, mock_ha)

    raw_key = b"raw_binary_key_512_bytes_test"
    res = engine.start(keyfile_bytes=raw_key)
    assert res is True

    executed = [" ".join(cmd) for cmd in mock_runner.commands_history]

    # Check that cryptsetup open was called with --key-file
    assert any("cryptsetup open" in cmd and "--key-file" in cmd for cmd in executed)
    # Check that shred was executed to wipe the RAM buffer
    assert any("shred -u" in cmd for cmd in executed)


def test_start_when_already_unlocked(mock_config, mock_runner, mock_ha):
    mock_ha.current_state = "on"
    disk_path = "/dev/sdc"
    lv_crypto = mock_config.lv_crypto_path
    mapper_path = mock_config.mapper_path

    # Simulate mapper already open in /dev/mapper/cryptovault
    mock_runner.existing_paths.update([disk_path, lv_crypto, mapper_path])
    mock_runner.set_command_response("pvs --noheadings -o pv_name -S vg_name=mybook", stdout=disk_path)
    mock_runner.set_command_response("lsblk -s -rno PATH,TYPE " + disk_path, stdout=f"{disk_path} disk")

    engine = StorageEngine(mock_config, mock_runner, mock_ha)
    res = engine.start()
    assert res is True

    executed = [" ".join(cmd) for cmd in mock_runner.commands_history]

    # Cryptsetup open should NOT be called since mapper already exists
    assert not any("cryptsetup open" in cmd for cmd in executed)
