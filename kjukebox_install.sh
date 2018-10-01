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
    if [ "$dl" != "y" ] ; then
        echo "Keeping currently installed version."
        dl=""
    fi
elif [ -e "$loc" ] ; then
    echo -n "Install latest version instead of the one in $(readlink -f "$loc")? [y/N] "
    read dl
    if [ "$dl" != "y" ] ; then
        echo "Installing local copy."
        ( set -x ; install -m 755 "$loc" /usr/local/bin/kjukebox )
        dl=""
    fi
else
    # no binary found at all, need to download a new one anyway
    dl="y"
fi
if [ -n "$dl" ] ; then
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
echo " - W = public read and write access to jukebox contents"
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
        smb_incoming=""
        ;;
    w*)
        echo "Selected mode: read-write"
        smb_en="enable"
        smb_content_wr="yes"
        smb_incoming=""
        ;;
    i*)
        echo "Selected mode: separate incoming"
        smb_en="enable"
        smb_content_wr="no"
        smb_incoming="yes"
        ;;
    *)
        echo "Selected mode: no Samba"
        smb_en="disable"
        ;;
esac
echo

###############################################################################

echo "##### configuring shutdown command"
echo "A (possibly secret) web interface command can be defined that shuts down"
echo "the system in a clean manner. Its URL is http://<deviceIP>/<command>."
echo
quitdefault=$(grep '^QUITCMD=' /usr/local/bin/autostart_jukebox 2>/dev/null | cut -d= -f2)
[ -z "$quitdefault" ] && quitdefault=shutdown
echo -n "Shutdown command? [default: $quitdefault] "
read quitcmd
[ -z "$quitcmd" ] && quitcmd=$quitdefault
echo

echo "##### installing autostart script in /usr/local/bin/autostart_jukebox"
cat >/usr/local/bin/autostart_jukebox <<EOF
#!/bin/bash

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

QUITCMD=$quitcmd
kjukebox -p 80 -a -s -r -l jukebox.log -q \$QUITCMD=47 /srv/jukebox/content
[ \$? == 47 ] && sudo shutdown -P now

sync
killterm
EOF
( set -x ; chmod 755 /usr/local/bin/autostart_jukebox )
echo

###############################################################################

pkgfail=""
which omxplayer.bin >/dev/null || pkgfail="$pkgfail omxplayer"
if [ "$smb_en" == "enable" ] ; then
    which samba >/dev/null || pkgfail="$pkgfail samba"
fi
if [ -n "$pkgfail" ] ; then
    echo "##### installing required packages:$pkgfail"
    apt update
    apt -y install $pkgfail
else
    echo "##### all required packages are already installed"
fi
echo

###############################################################################

if [ -d /home/jukebox ] ; then
    echo "##### jukebox user already exists"
else
    echo "##### creating jukebox user"
    useradd -m -U -G audio,video -s /usr/local/bin/autostart_jukebox jukebox
fi
echo

###############################################################################

echo "##### creating directories and mount points"
( set -x ; mkdir -p /boot/{JukeboxContent,incoming} /srv/jukebox/{incoming,content} )
( set -x ; sudo sed -ri "s:(/boot.*defaults)[^ \t]*:\1,gid=$(id -g jukebox),umask=002,iocharset=utf8:" /etc/fstab )
grep /srv/jukebox/content /etc/fstab &>/dev/null || ( set -x ; echo -e "/boot/JukeboxContent\t/srv/jukebox/content\tnone\tbind\t0\t3" >>/etc/fstab )
grep /srv/jukebox/incoming /etc/fstab &>/dev/null || ( set -x ; echo -e "/boot/incoming\t/srv/jukebox/incoming\tnone\tbind\t0\t3" >>/etc/fstab )
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
echo

###############################################################################

echo "##### everything done"
mount -a
sync
echo
echo "Now copy some video files to /srv/jukebox/content"
echo "or the JukeboxContent directory on the FAT32 boot partition"
echo "and restart the system."
echo
