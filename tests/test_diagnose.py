"""
Tests for system diagnosis and actionable error reporting.
"""
from luks_companion.core.diagnose import run_full_diagnosis, format_diagnosis_cli


def test_diagnose_all_nominal(mock_config, mock_runner, mock_ha):
    # Setup nominal environment
    disk_path = "/dev/sdc"
    lv_crypto = mock_config.lv_crypto_path
    mapper_path = mock_config.mapper_path
    mount_crypto = mock_config.mount_crypto
    env_path = "/etc/luks-companion/.env"
    mock_config.env_file_path = env_path

    mock_runner.existing_paths.update([disk_path, lv_crypto, mapper_path, mount_crypto, f"/dev/{mock_config.vg_name}", env_path])
    mock_runner.mountpoints.add(mount_crypto)
    mock_runner.directories.add(f"/dev/{mock_config.vg_name}")

    mock_runner.set_command_response("pvs --noheadings -o pv_name -S vg_name=mybook", stdout=disk_path)
    mock_runner.set_command_response("lsblk -s -rno PATH,TYPE " + disk_path, stdout=f"{disk_path} disk")
    mock_runner.set_command_response("vgs --noheadings -o vg_name mybook", stdout="mybook")

    report = run_full_diagnosis(mock_config, mock_runner, mock_ha)
    assert "checks" in report
    assert report["checks"]["hardware"]["disk_detected"] is True
    assert report["checks"]["lvm"]["vg_active"] is True
    assert report["checks"]["luks"]["is_open"] is True

    cli_text = format_diagnosis_cli(report)
    assert "REPORT DIAGNOSTICO" in cli_text
    assert "TUTTI I CONTROLLI DI SISTEMA SONO NOMINALI" in cli_text


def test_diagnose_missing_disk_flags_issue(mock_config, mock_runner, mock_ha):
    # No disk attached
    mock_config.target_dev = "/dev/sdc"
    env_path = "/etc/luks-companion/.env"
    mock_config.env_file_path = env_path
    mock_runner.existing_paths.add(env_path)

    report = run_full_diagnosis(mock_config, mock_runner, mock_ha)

    assert len(report["issues"]) > 0
    assert any("Disco fisico non rilevato" in issue for issue in report["issues"])
    assert len(report["suggestions"]) > 0

    cli_text = format_diagnosis_cli(report)
    assert "ANOMALIE RILEVATE" in cli_text
    assert "AZIONI SUGGERITE" in cli_text

