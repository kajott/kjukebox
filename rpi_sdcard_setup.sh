#!/bin/bash
echo "This script sets up an SD card with current Raspbian Lite"
echo "and a larger than usual FAT32 (boot) partition."
echo

IMGFILE=raspbian_lite_latest.img
MOUNT_SRC=/tmp/rpi-sdcard-setup.src
MOUNT_DEST=/tmp/rpi-sdcard-setup.dest

DEV=$1
if [ -z "$DEV" -o "$DEV" == "-h" -o "$DEV" == "--help" ] ; then
    echo "Usage: $0 </dev/sdX> [<root_partition_size_in_MiB>]"
    exit 2
fi

set -e

###############################################################################

if [ -f $IMGFILE ] ; then
    echo "##### $IMGFILE already exists"
else
    echo "##### downloading $IMGFILE"
    ( set -x ; wget -O- https://downloads.raspberrypi.org/raspbian_lite_latest | funzip >$IMGFILE.part )
    mv $IMGFILE.part $IMGFILE
fi
echo

echo "##### inspecting image file"
ls -l $IMGFILE
declare -a starts
starts=($(sfdisk -d $IMGFILE | grep -Eo 'start= *([0-9]+)' | cut -d= -f2))
(( bootoffset = starts[0] * 512 ))
(( rootoffset = starts[1] * 512 ))
echo "boot partition starts at $bootoffset bytes, root partition starts at $rootoffset bytes"
echo

###############################################################################

echo "##### preparing target device"
bytes=$(set -x ; lsblk -nrbdoSIZE $DEV)
(( devmegs = bytes / 1048576 ))
(( gigs = bytes / 1000000000 ))
echo "$DEV capacity: $devmegs MiB ($gigs GB)"
echo

rootmegs=$2
if [ -n "$rootmegs" ] ; then
    echo "Assigning $rootmegs MiB to the root (ext4) partition."
else
    echo -n "How many MiB to assign for the root (ext4) partition? => "
    read rootmegs
fi
[ $rootmegs -lt 1024 ] && ( echo "That's not sufficient." ; exit 1 )
(( bootsize = (devmegs - rootmegs - 4) / 4 * 8192 ))
[ $bootsize -lt 250000 ] && ( echo "That's too much." ; exit 1 )
echo

( set -x ; lsblk $DEV )
echo
echo "WARNING: All data on $DEV will be completely DESTROYED!"
echo -n "If you want to continue, type 'YES' (in uppercase) here => "
read confirm
[ "$confirm" != "YES" ] && ( echo "Aborted by user." ; exit 1 )
echo

echo "##### writing partition table"
(( rootstart = bootsize + 8192 ))
( set -x ; echo -e "start=8192, size=$bootsize, type=c\nstart=$rootstart, type=83" | sudo sfdisk $DEV )
echo

echo "##### verifying partition table"
sync
( set -x ; sudo partprobe $DEV )
sync
declare -a parts
parts=($(lsblk -pnroNAME,TYPE -xNAME $DEV | grep part | cut -d' ' -f1))
bootpart=${parts[0]}
rootpart=${parts[1]}
retry=""
while [ "$retry" != ".........." ] ; do
    diskuuid=$(lsblk -rnoPARTUUID $DEV | tail -n 1 | cut -d- -f1)
    [ -n "$diskuuid" ] && break
    [ -z "$retry" ] && echo "Disk UUID not known yet, trying again ..."
    sleep 1
    retry=".$retry"
done
echo "boot partition: $bootpart"
echo "root partition: $rootpart"
echo "disk UUID: $diskuuid"
[ -z "$bootpart" -o -z "$rootpart" -o -z "$diskuuid" ] && ( echo "Partition setup failed." ; exit 1 )
echo

echo "##### formatting partitions"
( set -x ; sudo mkfs.vfat -F 32 -n RASPBERRYPI $bootpart )
( set -x ; sudo mkfs.ext4 -F $rootpart )
echo

###############################################################################

echo "#### preparing copy"
sudo umount $MOUNT_SRC $MOUNT_DEST 2>/dev/null || true
( set -x ; sudo rm -rf $MOUNT_SRC $MOUNT_DEST )
( set -x ; sudo mkdir $MOUNT_SRC $MOUNT_DEST )
echo

echo "##### copying boot partition"
( set -x ; sudo mount -o ro,loop,offset=$bootoffset $IMGFILE $MOUNT_SRC )
( set -x ; sudo mount $bootpart $MOUNT_DEST )
( set -x ; sudo cp -av $MOUNT_SRC/* $MOUNT_DEST/ | awk '{printf "%d files copied\r",NR }')
echo
( set -x ; sudo sed -ri "s:PARTUUID=[a-fA-F0-9]+:PARTUUID=$diskuuid:;s: init=/usr/lib/raspi-config/init_resize.sh::" $MOUNT_DEST/cmdline.txt )
( set -x ; sync )
( set -x ; sudo umount $MOUNT_SRC $MOUNT_DEST )
echo

echo "##### copying root partition"
( set -x ; sudo mount -o ro,loop,offset=$rootoffset $IMGFILE $MOUNT_SRC )
( set -x ; sudo mount $rootpart $MOUNT_DEST )
( set -x ; sudo cp -av $MOUNT_SRC/* $MOUNT_DEST/ | awk '{printf "%d files copied\r",NR }')
echo
( set -x ; sudo sed -ri "s:PARTUUID=[a-fA-F0-9]+:PARTUUID=$diskuuid:" $MOUNT_DEST/etc/fstab )
( set -x ; sync )
( set -x ; sudo umount $MOUNT_SRC $MOUNT_DEST )
echo

###############################################################################

echo "##### cleaning up"
sudo umount $MOUNT_SRC $MOUNT_DEST 2>/dev/null || true
( set -x ; sudo rm -rf $MOUNT_SRC $MOUNT_DEST )
sync
( set -x ; sudo eject $DEV 2>/dev/null || true )
echo
echo "All done. You may now remove the SD card and"
echo "    rm $IMGFILE"
echo "if you want."
