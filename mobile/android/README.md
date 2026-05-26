# Photo Manager Mobile

Python/Kivy Android companion app for Photo Manager Pro.

The app lets a phone user pick photos/videos, copy them into a local phone inbox, or send them directly to the desktop app through Phone Connect.

Local inbox path:

```text
/sdcard/PhotoManagerPro/Incoming
```

LAN transfer uses the desktop `Phone Connect` tab:

1. Start the receiver in the desktop app.
2. Enter the shown URL and pairing code in the Android app.
3. Send selected media.
4. In desktop, use `Sort Inbox` to organize received files into the library.

The URL field can also point at your own server, VPN, or reverse proxy if it forwards to the same receiver endpoint.

## Build Debug APK

```bash
python -m pip install buildozer cython
buildozer android debug
```

The debug APK is written under:

```text
mobile/android/bin/
```

## Install

```bash
adb install -r mobile/android/bin/*.apk
```

This is a debug APK. A public release APK/AAB should be signed with a release keystore.
