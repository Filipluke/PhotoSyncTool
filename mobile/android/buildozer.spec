[app]
title = Photo Manager Mobile
package.name = photomanagermobile
package.domain = org.filipluke
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json
version = 0.2.0
requirements = python3,kivy,plyer
orientation = portrait
fullscreen = 0
android.api = 33
android.minapi = 24
android.ndk = 28c
android.archs = arm64-v8a, armeabi-v7a
android.permissions = android.permission.READ_EXTERNAL_STORAGE, android.permission.WRITE_EXTERNAL_STORAGE, android.permission.READ_MEDIA_IMAGES, android.permission.READ_MEDIA_VIDEO, android.permission.INTERNET, android.permission.ACCESS_NETWORK_STATE, android.permission.ACCESS_WIFI_STATE
android.manifest.application_attributes = android:usesCleartextTraffic="true"
android.release_artifact = apk
android.debug_artifact = apk

[buildozer]
log_level = 2
warn_on_root = 0
