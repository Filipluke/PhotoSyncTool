# Linux systemd Background Sync

Photo Manager Pro can run background synchronization on Linux through a user-level systemd service named `photo-manager-pro.service`.

## Setup

Install the package and create/save app settings first:

```bash
python3 -m pip install --user photosync-tool
photo-manager-pro
```

Run one sync to verify the config:

```bash
photo-manager-service once
```

Install and start the user service:

```bash
photo-manager-service install
photo-manager-service start
photo-manager-service status
```

The install command writes:

```text
~/.config/systemd/user/photo-manager-pro.service
```

The app log is written to:

```text
~/.config/PhotoManagerPro/photo_manager_service.log
```

## systemctl Commands

You can use the wrapper commands:

```bash
photo-manager-service start
photo-manager-service stop
photo-manager-service restart
photo-manager-service status
photo-manager-service uninstall
```

Or systemd directly:

```bash
systemctl --user start photo-manager-pro.service
systemctl --user stop photo-manager-pro.service
systemctl --user restart photo-manager-pro.service
systemctl --user status photo-manager-pro.service
journalctl --user -u photo-manager-pro.service -f
```

If the service should keep running after logout on the target machine, enable user lingering:

```bash
loginctl enable-linger "$USER"
```

## Notes

- This is a user service, not a root/system service.
- It uses the same config file as the GUI.
- File operations still follow the app's configured source, root, schedule, verification, and delete settings.
- Test with a disposable demo library before pointing it at a real photo archive.
