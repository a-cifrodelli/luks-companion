"""
Tests for safe_teardown() atomicity, neutrality, idempotency, and error rollback.
"""
import pytest
from luks_companion.core.engine import StorageEngine, StorageEngineError


def test_teardown_when_ha_fails_at_start(mock_config, mock_runner, mock_ha):
    mock_ha.should_fail = True
    engine = StorageEngine(mock_config, mock_runner, mock_ha)

    with pytest.raises(StorageEngineError) as exc_info:
        engine.start(passphrase="secret")

    assert exc_info.value.phase == "HA_POWER_ON"

    # Verify that teardown ran safely and neutrally:
    executed = [" ".join(cmd) for cmd in mock_runner.commands_history]

    # No unmount should have been attempted
    assert not any("umount" in cmd for cmd in executed)
    # No cryptsetup close should have been attempted
    assert not any("cryptsetup close" in cmd for cmd in executed)
    # No SCSI power off should have been attempted
    assert not any("udisksctl power-off" in cmd for cmd in executed)


def test_teardown_when_usb_detection_fails(mock_config, mock_runner, mock_ha):
    # HA turns on successfully, but mock_runner has no disk
    mock_ha.current_state = "on"
    mock_config.usb_wait_max_sec = 1
    engine = StorageEngine(mock_config, mock_runner, mock_ha)

    with pytest.raises(StorageEngineError) as exc_info:
        engine.start(passphrase="secret")

    assert exc_info.value.phase == "USB_DETECTION"

    # Verify teardown turned off the smart plug so it's not left powered
    assert mock_ha.turn_off_calls >= 1

    executed = [" ".join(cmd) for cmd in mock_runner.commands_history]
    # No unmount or close attempted
    assert not any("umount" in cmd for cmd in executed)
    assert not any("cryptsetup close" in cmd for cmd in executed)


def test_teardown_when_luks_password_fails(mock_config, mock_runner, mock_ha):
    mock_ha.current_state = "on"
    mock_config.usb_wait_max_sec = 1
    disk_path = "/dev/sdc"
    lv_path = mock_config.lv_crypto_path

    # Set up presence of disk and LVM LV
    mock_runner.existing_paths.add(disk_path)
    mock_runner.existing_paths.add(lv_path)
    mock_runner.set_command_response("pvs --noheadings -o pv_name -S vg_name=mybook", stdout=disk_path)
    mock_runner.set_command_response("lsblk -s -rno PATH,TYPE " + disk_path, stdout=f"{disk_path} disk")

    # Fail cryptsetup open
    mock_runner.set_command_response("cryptsetup open", returncode=2, stderr="No key available with this passphrase.")

    engine = StorageEngine(mock_config, mock_runner, mock_ha)

    with pytest.raises(StorageEngineError) as exc_info:
        engine.start(passphrase="wrong_password")

    assert exc_info.value.phase == "LUKS_UNLOCK"

    executed = [" ".join(cmd) for cmd in mock_runner.commands_history]

    # No unmount attempted (never mounted)
    assert not any("umount" in cmd for cmd in executed)
    # LVM was deactivated
    assert any("vgchange -an mybook" in cmd for cmd in executed)
    # SCSI power-off was issued
    assert any("udisksctl power-off -b /dev/sdc" in cmd for cmd in executed)
    # Plug was turned off
    assert mock_ha.turn_off_calls >= 1


def test_teardown_after_full_mount(mock_config, mock_runner, mock_ha):
    mock_ha.current_state = "on"
    disk_path = "/dev/sdc"
    lv_crypto = mock_config.lv_crypto_path
    mapper_path = mock_config.mapper_path
    mount_crypto = mock_config.mount_crypto

    # Simulate fully active system
    mock_runner.existing_paths.update([disk_path, lv_crypto, mapper_path, mount_crypto])
    mock_runner.mountpoints.add(mount_crypto)
    mock_runner.set_command_response("pvs --noheadings -o pv_name -S vg_name=mybook", stdout=disk_path)
    mock_runner.set_command_response("lsblk -s -rno PATH,TYPE " + disk_path, stdout=f"{disk_path} disk")

    engine = StorageEngine(mock_config, mock_runner, mock_ha)
    engine.power_is_on = True
    engine.volume_is_unlocked = True
    engine.filesystem_is_mounted = True

    # Execute teardown
    res = engine.safe_teardown()
    assert res is True

    executed = [" ".join(cmd) for cmd in mock_runner.commands_history]

    # 1. WebDAV stopped
    assert any("systemctl stop webdav" in cmd for cmd in executed)
    # 2. Sync called
    assert mock_runner.sync_calls >= 1
    # 3. fuser and umount called
    assert any(f"fuser -km -9 {mount_crypto}" in cmd for cmd in executed)
    assert any(f"umount {mount_crypto}" in cmd for cmd in executed)
    # 4. cryptsetup close called
    assert any("cryptsetup close cryptovault" in cmd for cmd in executed)
    # 5. vgchange -an called
    assert any("vgchange -an mybook" in cmd for cmd in executed)
    # 6. SCSI power-off called
    assert any(f"udisksctl power-off -b {disk_path}" in cmd for cmd in executed)
    # 7. HA plug turned off
    assert mock_ha.turn_off_calls >= 1


def test_teardown_idempotency(mock_config, mock_runner, mock_ha):
    engine = StorageEngine(mock_config, mock_runner, mock_ha)

    # First call
    assert engine.safe_teardown() is True
    history_len_1 = len(mock_runner.commands_history)

    # Second call must be immediate no-op
    assert engine.safe_teardown() is True
    history_len_2 = len(mock_runner.commands_history)

    assert history_len_1 == history_len_2
