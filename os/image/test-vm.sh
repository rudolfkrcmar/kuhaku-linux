#!/bin/sh
# Boots the built image in a headless QEMU/KVM machine for testing (no root needed, user in group kvm).
#   ./image/test-vm.sh            then: python3 image/vm.py shot /tmp/kvm/s.png ; python3 image/vm.py run "cmd"
set -e
ISO=/var/tmp/kuhaku-iso/kuhaku-amd64.hybrid.iso
D=/tmp/kvm; mkdir -p $D; cd $D
7z e -y "$ISO" live/vmlinuz live/initrd.img >/dev/null
rm -f serial.sock qmp.sock
setsid -f qemu-system-x86_64 -enable-kvm -m 3072 -smp 2 -cpu host -cdrom "$ISO" -kernel vmlinuz -initrd initrd.img \
  -append "boot=live components username=admin live-config.username=admin live-config.user-fullname=Kuhaku console=tty0 console=ttyS0,115200" \
  -nic user,model=virtio-net-pci -vga std -display none \
  -serial unix:$D/serial.sock,server,nowait -qmp unix:$D/qmp.sock,server,nowait -monitor none
echo "booting; serial $D/serial.sock, QMP $D/qmp.sock"
