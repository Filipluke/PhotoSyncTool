from pathlib import Path

import photo_manager_service


def test_systemd_user_unit_dir_uses_xdg_config_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    assert photo_manager_service.systemd_user_unit_dir() == tmp_path / "xdg" / "systemd" / "user"
    assert photo_manager_service.systemd_unit_path().name == "photo-manager-pro.service"


def test_build_systemd_unit_uses_current_python_and_config(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "Config Dir" / "photo_manager_config.json"
    log_dir = tmp_path / "App Config"

    monkeypatch.setattr(photo_manager_service.sys, "executable", "/opt/photo manager/bin/python")
    monkeypatch.setattr(photo_manager_service, "user_config_dir", lambda: log_dir)

    unit = photo_manager_service.build_systemd_unit(config_path)

    assert "Description=Photo Manager Pro Background Service" in unit
    assert '"/opt/photo manager/bin/python" "' in unit
    assert '"run" "--config"' in unit
    assert photo_manager_service.systemd_quote(config_path) in unit
    assert "Restart=on-failure" in unit
    assert f"# {log_dir / photo_manager_service.SERVICE_LOG_NAME}" in unit
