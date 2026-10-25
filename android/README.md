# MediaLibrary Android

A native Kotlin Android client for the existing Windows `MediaLibrary` server. Open this `android/` folder in Android Studio (Giraffe or newer, Android SDK 35) and let Gradle sync. Connect the Galaxy S21 and PC to the same Wi‑Fi, start `MediaLibrary/run.bat` on the PC, then enter its LAN URL such as `http://192.168.1.25:8420` in the app. Windows Firewall must allow Python on the private network.

The client browses the existing library and season/episode grouping, streams through the PC server (which keeps first-generation Chromecast compatibility), chooses neighboring `.srt`, `.vtt`, `.ass`, and `.ssa` subtitles, sends playback to discovered Chromecast devices, and searches/adds torrents through configured Prowlarr/Jackett and qBittorrent settings. Configure qBittorrent Web UI, TMDB, and indexers in the PC web app first. The phone does not access the PC's disk directly.

For first-generation Chromecast, use the PC server's Cast button: it transcodes to fragmented MP4/H.264/AAC for that device. Android playback uses Media3 and can consume the server's browser HLS/direct streams.
