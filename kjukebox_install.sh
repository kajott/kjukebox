#!/bin/bash
echo "This script turns a normal Raspbian Lite installation into a video jukebox"
echo "based on kjukebox that will start playing videos automatically after booting,"
echo "or changes options of an existing installation."
echo

UPSTREAM_URL="https://raw.githubusercontent.com/kajott/kjukebox/master/kjukebox.py"

if [ ! -c /dev/vchiq ] ; then
    echo "FATAL: This script must be run on a Raspberry Pi." >&2
    exit 1
fi

if [ $(id -u) != 0 ] ; then
    echo "FATAL: This script must be run as root." >&2
    exit 1
fi

distro=$(echo $(lsb_release -ics))
if [ "$distro" != "Raspbian stretch" ] ; then
    echo "WARNING: This script has been developed for and tested on Raspbian stretch,"
    echo "but this system uses $distro. Things may not work as expected."
    echo
fi

echo -n "Continue? [y/N] "
read cont
if [ "$cont" != "y" ] ; then
    echo "Aborted by user."
    exit 1
fi
echo

set -e

###############################################################################

echo "##### installing /usr/local/bin/kjukebox"
loc="$(dirname $0)/kjukebox.py"
if [ -x /usr/local/bin/kjukebox ] ; then
    echo -n "kjukebox is already installed, upgrade to latest version? [y/N] "
    read dl
    if [ "$dl" == "y" ] ; then
        dl="dl"
    elif [ -f "$loc" -a "$loc" -nt /usr/local/bin/kjukebox ] ; then
        echo -n "$(readlink -f "$loc") is newer than the installed version; upgrade to that? [Y/n] "
        read dl
        if [ "$dl" != "n" ] ; then
            dl="local"
        else
            dl="keep"
        fi
    else
        dl="keep"
    fi
elif [ -e "$loc" ] ; then
    echo -n "Install latest version instead of the one in $(readlink -f "$loc")? [y/N] "
    read dl
    if [ "$dl" == "y" ] ; then
        dl="dl"
    else
        dl="local"
    fi
else
    # no binary found at all, need to download a new one anyway
    dl="dl"
fi
if [ "$dl" == "keep" ] ; then
    echo "Keeping installed version."
elif [ "$dl" == "local" ] ; then
    echo "Installing local copy."
    ( set -x ; install -m 755 "$loc" /usr/local/bin/kjukebox )
elif [ "$dl" == "dl" ] ; then
    echo "Downloading latest upstream version of kjukebox ..."
    ( set -x ; wget -nv -O /usr/local/bin/kjukebox $UPSTREAM_URL )
    ( set -x ; chmod +x /usr/local/bin/kjukebox )
fi
echo

###############################################################################

echo "##### configure hostname"
echo -n "Set the new hostname of the device [default: $HOSTNAME] => "
read newhost
if [ -n "$newhost" ] ; then
    ( set -x ; echo $newhost >/etc/hostname )
    ( set -x ; sed -ri "s:\b$HOSTNAME\b:$newhost:" /etc/hosts )
else
    echo "Host name not changed."
fi
echo

###############################################################################

echo "##### configure CIFS file access"
echo "A Samba server can be installed so that not only remote control of the"
echo "jukebox is possible, but also access to the audio/video files."
echo "Please select the type of Samba installation you want:"
echo " - N = no Samba at all"
echo " - R = public read-only access to jukebox contents"
echo " - W = public read and write access to jukebox contents and configuration"
echo " - I = public read-only access to jukebox contents,"
echo "       and read/write access to an additional \"incoming\" directory"

smbdefault="n"
smbsel="N/r/w/i"
if [ -f /etc/systemd/system/*.wants/smbd.service ] ; then
    if grep -E '^\[incoming\]$' /etc/samba/smb.conf &>/dev/null ; then
        smbdefault="i"
        smbsel="n/r/w/I"
    elif grep -E '^writable\s=\syes$' /etc/samba/smb.conf &>/dev/null ; then
        smbdefault="w"
        smbsel"n/r/W/i"
    else
        smbdefault="r"
        smbsel"n/R/w/i"
    fi
fi
echo -n "Your selection? [$smbsel] "
read smbmode
[ -z "$smbmode" ] && smbmode=$smbdefault

case "$(echo "$smbmode" | tr [[:upper:]] [[:lower:]])" in
    r*)
        echo "Selected mode: read-only"
        smb_en="enable"
        smb_content_wr="no"
        smb_config=""
        smb_incoming=""
        ;;
    w*)
        echo "Selected mode: read-write"
        smb_en="enable"
        smb_content_wr="yes"
        smb_config="yes"
        smb_incoming=""
        ;;
    i*)
        echo "Selected mode: separate incoming"
        smb_en="enable"
        smb_content_wr="no"
        smb_config=""
        smb_incoming="yes"
        ;;
    *)
        echo "Selected mode: no Samba"
        smb_en="disable"
        ;;
esac
echo

###############################################################################

echo "##### package installation"
pkgfail=""
which omxplayer.bin >/dev/null || pkgfail="$pkgfail omxplayer"
if [ "$smb_en" == "enable" ] ; then
    which samba >/dev/null || pkgfail="$pkgfail samba"
fi
if [ -n "$pkgfail" ] ; then
    echo "Installing required packages:$pkgfail"
    ( set -x ; apt update )
    ( set -x ; apt -y install $pkgfail )
else
    echo "All required packages are already installed."
fi
echo

###############################################################################

echo "##### creating jukebox user"
if getent passwd jukebox >/dev/null ; then
    echo "User already exists."
else
    ( set -x ; useradd -U -G audio,video -d /srv/jukebox/config -M -s /usr/local/bin/autostart_jukebox jukebox )
fi
echo

###############################################################################

echo "##### creating directories and mount points"
bootspace=$(df --output=avail /boot | grep -E '^[0-9]+$')
rootspace=$(df --output=avail / | grep -E '^[0-9]+$')
if [ $bootspace -gt $rootspace ] ; then
    data="boot"
    echo "Boot (FAT) partition is larger than root (ext4) partition,"
    echo "installing directories for video and configuration data on boot (FAT)."
    ( set -x ; mkdir -p /boot/{JukeboxContent,JukeboxConfig,incoming} /srv/jukebox/{content,config,incoming} )
    ( set -x ; sudo sed -ri "s:(/boot.*defaults)[^ \t]*:\1,gid=$(id -g jukebox),umask=002,iocharset=utf8:" /etc/fstab )
    grep /srv/jukebox/content /etc/fstab &>/dev/null || ( set -x ; echo -e "/boot/JukeboxContent\t/srv/jukebox/content\tnone\tbind\t0\t3" >>/etc/fstab )
    grep /srv/jukebox/config /etc/fstab &>/dev/null || ( set -x ; echo -e "/boot/JukeboxConfig\t/srv/jukebox/config\tnone\tbind\t0\t3" >>/etc/fstab )
    grep /srv/jukebox/incoming /etc/fstab &>/dev/null || ( set -x ; echo -e "/boot/incoming\t/srv/jukebox/incoming\tnone\tbind\t0\t3" >>/etc/fstab )
    ( set -x ; mount -a )
else
    data="root"
    echo "Root (ext4) partition is larger than boot (FAT) partition,"
    echo "installing directories for video and configuration data on root (ext4)."
    umount /srv/jukebox/{content,config,incoming} 2>/dev/null || true
    sed -ri 's:.*/srv/jukebox/.*::' /etc/fstab
    ( set -x ; mkdir -p /srv/jukebox/{content,config,incoming} )
    ( set -x ; chown jukebox:jukebox /srv/jukebox/{content,config,incoming} || true )
fi
echo

###############################################################################

echo "##### installing configuration file and autostart script"
if [ -f /srv/jukebox/config/config.txt ] ; then
    echo "Configuration file exists already, not overwriting it."
else
    echo "Installing new configuration file."
    sed 's:$:$:' <<EOF | tr '$' '\r' >/srv/jukebox/config/config.txt
# This is the configuration file for kjukebox.

# This file contains the command-line parameters for kjukebox,
# with which many useful things can be configured.
# Empty lines and lines beginning with '#' are ignored.

# Set the port for the web interface to 80 (normal HTTP).
--port 80

# Set the name of a log file; mostly useful for debugging.
#--logfile kjukebox.log

# Set the name of the playlist, history and play counts file.
--statefile state.txt

# Enable automatic saving of the state file after each played track.
# This may make the system a little more robust against random power loss,
# but it slightly increases wear on the SD card.
--autosave

# Automatically rescan the jukebox content directory for new or deleted files
# after each played track. Only really useful when content is actually added
# or removed while the system is running (e.g. via Samba), but doesn't do any
# harm if the content is perfectly static either.
--autoscan

# Start playback automatically when the system starts up.
# If disabled, playback needs to be initiated manually in the web interface.
--autoplay

# Set how many tracks shall be kept in the history across reboots.
--maxhist 100

# Display a text file instead of the jukebox's IP address between videos.
# Can also be used to disable the IP address display: just specify an invalid
# file name, e.g. '--logo -'.
#--logo logo.txt

# Set (possibly secret) commands for shutting down or rebooting the system.
# They can be invoked using http://<IP_address>/<command>.
# Shutdown uses code 99, reboot uses code 77.
--quitcmd shutdown=99
--quitcmd reboot=77

# Other options (host name, Samba options) can't be configured in this file;
# please re-run kjukebox_install.sh to change these options.
EOF
    chown jukebox:jukebox /srv/jukebox/config/config.txt 2>/dev/null || true
fi

cat >/usr/local/bin/autostart_jukebox <<EOF
#!/bin/bash
cd /srv/jukebox/config

setfont /usr/share/consolefonts/Uni2-TerminusBold28x14.psf
setterm --blank 0 --powersave off --powerdown 0

echo
echo
echo
echo "          ====================================="
echo "          Starting Jukebox in 10 seconds."
echo "          If you don't want that, press ^C now."
echo "          ====================================="
echo
echo
echo

function killterm {
    echo
    echo "This console is now disabled."
    echo "You can still use another TTY to login as another user."
    echo
    while true ; do sleep 3600 ; done
    exit 7
}

trap "killterm" SIGINT
sleep 10

clear
echo
echo
echo "           ---------------------"
echo "           Starting Jukebox now."
echo "           ---------------------"
echo
echo

options="\$(tr -d '\r' </srv/jukebox/config/config.txt | sed 's:^#.*::')"
set -x
kjukebox \$options /srv/jukebox/content
code=\$?
set +x
[ \$code == 99 ] && sudo shutdown -P now
[ \$code == 77 ] && sudo shutdown -r now

sync
killterm
EOF
( set -x ; chmod 755 /usr/local/bin/autostart_jukebox )
echo

###############################################################################

echo "##### enabling autostart, port 80 access and shutdown"
( set -x ; echo -e "[Service]\nExecStart=\nExecStart=-/sbin/agetty --autologin jukebox --noclear %I 38400 linux" >>/etc/systemd/system/getty@tty1.service.d/autologin.conf )
( set -x ; setcap CAP_NET_BIND_SERVICE=+eip /usr/bin/python2.7 )
( set -x ; echo "jukebox ALL=(ALL) NOPASSWD: /sbin/shutdown" > /etc/sudoers.d/020_jukebox-shutdown )
( set -x ; chmod 440 /etc/sudoers.d/020_jukebox-shutdown )
echo

###############################################################################

function set_config {
    echo "- # $3"
    echo "  $1=$2"
    # comment all existing assignments first
    sed -ri "s:^($1=):#\1:" /boot/config.txt 2>/dev/null
    # uncomment correct assignment
    if grep -E "^#$1=$2\s*$" /boot/config.txt >/dev/null ; then
        sed -ri "s:^#($1=$2)[ \t]*$:\1:" /boot/config.txt 2>/dev/null
        return
    fi
    # add new assignment with proper value
    echo -e "\n# $3\n$1=$2" >>/boot/config.txt
}

echo "##### modifying firmware configuration (config.txt)"
set_config disable_overscan 1 "avoid black borders"
set_config gpu_freq 400 "overclock GPU to allow (somewhat) smooth 1080p60 playback"
set_config force_turbo 1 "ensure that GPU overclocking is actually applied"
set_config gpu_mem 96 "allocate enough GPU memory to decode 1080p video, but not much more"
echo

###############################################################################

echo "##### configuring Samba"
if [ "$smb_en" == "enable" ] ; then
    echo "Generating /etc/samba/smb.conf ..."
    cat >/etc/samba/smb.conf <<EOF
[global]
workgroup = WORKGROUP
realm = WORKGROUP
server string = Raspbery Pi Jukebox
dns proxy = no
log file = /var/log/samba/log.global
max log size = 1000
syslog = 0
server role = standalone server
unix password sync = no
pam password change = no
map to guest = bad user
load printers = no
printing = bsd
printcap name = /dev/null
disable spoolss = yes
null passwords = yes
guest account = jukebox
unix extensions = no
create mask = 0775
directory mask = 0775

[content]
path = /srv/jukebox/content
comment = Jukebox Content
public = yes
browseable = yes
writable = $smb_content_wr
guest ok = yes
map read only = no
EOF
    if [ -n "$smb_config" ] ; then
        cat >>/etc/samba/smb.conf <<EOF

[config]
path = /srv/jukebox/config
comment = Jukebox Configuration and State
public = yes
browseable = yes
writable = yes
guest ok = yes
map read only = no
EOF
    fi
    if [ -n "$smb_incoming" ] ; then
        cat >>/etc/samba/smb.conf <<EOF

[incoming]
path = /srv/jukebox/incoming
comment = Jukebox Incoming Data
public = yes
browseable = yes
writable = yes
guest ok = yes
map read only = no
EOF
    fi
fi
( set -x ; systemctl $smb_en smbd.service 2>/dev/null || true )
( set -x ; systemctl $smb_en nmbd.service 2>/dev/null || true )
if [ "$smb_en" == "enable" -a "$smb_content_wr" == "yes" ] ; then
    ( set -x ; systemctl start smbd.service 2>/dev/null || true )
    ( set -x ; systemctl start nmbd.service 2>/dev/null || true )
fi
echo

###############################################################################

echo "##### everything done"
sync
echo
echo "Now review the configuration file in /srv/jukebox/config/config.txt,"
echo "copy some video files to /srv/jukebox/content"
if [ "$smb_en" == "enable" -a "$smb_content_wr" == "yes" ] ; then
    echo "(also possible over the network, via Samba)"
elif [ "$data" == "boot" ] ; then
    echo "(or the JukeboxContent directory on the FAT boot partition)"
fi
echo "and finally restart the system."
echo
