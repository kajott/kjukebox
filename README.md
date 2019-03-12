# Video Jukebox

`kjukebox` plays videos in a specified directory in fullscreen using some standard media player application (MPV, VLC, MPC-HC, or OMXPlayer on Raspberry Pi). Its main purpose is automated playback in random order, but playlists can also be specified manually using the built-in web interface. A text-based announcement screen is shown between videos.





## Raspberry Pi installation

kjukebox can be used as a fully-automated jukebox system on Raspberry Pi devices (any generation), including a web interface and even (optional) Samba access to the data directory. To set this up, do the following:


#### SD card preparation

##### Variant 1: Use a standard SD card image

Download [the latest Raspbian Lite image from the official Website](https://downloads.raspberrypi.org/raspbian_lite_latest) and write it to an SD card as usual, using [Etcher](https://etcher.io/), [Rufus](https://rufus.akeo.ie/), or if you have a Linux machine, just
```
wget -qO- https://downloads.raspberrypi.org/raspbian_lite_latest | funzip | dd status=progress of=/dev/mmcblkX/or/sdX/depends_on_your_system
```

If you prepare the SD card in this way, the videos will be stored on the ext4-formatted root partition of the card.

##### Variant 2: Custom formatting with a large FAT partition

Alternatively, you can use the `rpi_sdcard_setup.sh` script from this repository on a Linux machine. It also installs an official Raspbian Lite image onto an SD card, but it uses a custom partition layout that makes the FAT boot partition larger than the ext4 root partition. Videos and configuration data will then be stored on the FAT partition as well. This has the benefit that video data on the SD card can be managed from a Windows or OS X machine.


#### First boot and jukebox installation

Boot the RasPi from the SD card. You may want to use `sudo raspi-config` to configure the keyboard, timezone and primary user's password, and perhaps enable SSH access before doing anything else.

Then, copy `kjukebox_install.sh` onto the RasPi and run it (as root or via `sudo`). This will guide you through the remaining installation process. Or just fetch and run the most recent version directly from GitHub:
```
wget https://raw.githubusercontent.com/kajott/kjukebox/master/kjukebox_install.sh && sudo bash kjukebox_install.sh
```


#### Configuration and content installation

Review the configuration file (`/srv/jukebox/config/config.txt`, or `JukeboxConfig/config.txt` on the FAT partition if the SD card was set up appropriately), and copy video files into `/srv/jukebox/content` or the `JukeboxContent` directory of the SD card's FAT partition (if used). If you enabled Samba write access during jukebox installation, videos can also be copied to the card over the network.

Video files must use H.264 Baseline, Main or High Profile up to 1080p resolution. Some slight (and safe) overclocking is applied to make 1080p60 work to some extent, but perfect playback is only guaranteed up to 1080p30 or 720p60.

Finally, reboot the RasPi. It will automatically start the video show after a few seconds of delay. To inhibit this, press Ctrl+C during booting when the "Jukebox will start in 10 seconds" prompt appears and log in normally on another console (by pressing Alt+F2, for example).

You can control the video show remotely using the HTTP URL that is shown between videos.





## Downloading videos

If you want to download video files from YouTube or other streaming sites to be used with a Raspberry Pi-based video jukebox, here's an appropriate configuration for [youtube-dl](http://rg3.github.io/youtube-dl/):

```
youtube-dl -i -w -f "bestvideo[vcodec^=avc1][width<=1920]+bestaudio[acodec^=mp4a]/best[ext=mp4][width<=1920]/mp4"
```
