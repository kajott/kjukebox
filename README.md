# Video Jukebox

`kjukebox` plays videos in a specified directory in fullscreen using some standard media player application (MPV, VLC, MPC-HC, or OMXPlayer on Raspberry Pi). Its main purpose is automated playback in random order, but playlists can also be specified manuelly using the built-in web interface. A text-based announcement screen is shown between videos.


## Raspberry Pi Installation

kjukebox can be used as a fully-automated jukebox system on Raspberry Pi devices (any generation), including a web interface and even (optional) Samba access to the data directory. To set this up, do the following:

1. Use the rpi_sdcard_setup.sh script (on a Linux machine) to prepare an SD card for the RasPi. This will download and install the latest Raspbian Lite, but it will not just `dd` the image onto the card, but split the partitions in a way that the FAT32 boot partition is larger than usual. The reason is that this is where the video files will be stored; this has been done so that it's possible to manage the videos on the jukebox using any old Windows box.

2. Boot the RasPi from the SD card. You may want to use `sudo raspi-config` to configure the keyboard, timezone and primary user's password before doing anything else.

3. Copy `kjukebox.py` and `kjukebox_install.sh` onto the card in some way (e.g. by installing `git` and cloning this repo) and run `kjukebox_install.sh` which will guide you through the remaining installation process.

4. Shut down the RasPi and copy video files into the `JukeboxContent` directory in the FAT32 partition on the SD card. The files must use H.264 Baseline, Main or High Profile up to 1080p resolution. Some slight (and safe) overclocking is applied to make 1080p60 work to some extent, but perfect playback is only guaranteed up to 1080p30 or 720p60.

5. Boot the RasPi again. It will automatically start the video show after a few seconds of delay. To inhibit this, press Ctrl+C during booting when the "Jukebox will start in 10 seconds" prompt appears and log in normally on another console (by pressing Alt+F2, for example).

6. Control the video show remotely using the HTTP URL that is shown between videos.
