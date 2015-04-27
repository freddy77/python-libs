#!/usr/bin/env python

"""
Copyright (c) 2013, Citrix Inc.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

import os
import os.path
import re
import tempfile
import copy

COUNTER = 0

class MenuEntry(object):
    def __init__(self, hypervisor, hypervisor_args, kernel, kernel_args,
                 initrd, title = None, tboot = None, tboot_args = None,
                 root = None):
        self.tboot = tboot
        self.tboot_args = tboot_args
        self.hypervisor = hypervisor
        self.hypervisor_args = hypervisor_args
        self.kernel = kernel
        self.kernel_args = kernel_args
        self.initrd = initrd
        self.title = title
        self.root = root

    def getTbootArgs(self):
        return re.findall(r'\S[^ "]*(?:"[^"]*")?\S*', self.tboot_args)

    def setTbootArgs(self, args):
        self.tboot_args = ' '.join(args)

    def getHypervisorArgs(self):
        return re.findall(r'\S[^ "]*(?:"[^"]*")?\S*', self.hypervisor_args)

    def setHypervisorArgs(self, args):
        self.hypervisor_args = ' '.join(args)

    def getKernelArgs(self):
        return re.findall(r'\S[^ "]*(?:"[^"]*")?\S*', self.kernel_args)

    def setKernelArgs(self, args):
        self.kernel_args = ' '.join(args)

class Bootloader(object):
    def __init__(self, src_fmt, src_file, menu = None, menu_order = None,
                 default = None, timeout = None, serial = None,
                 location = None):

        if menu is None:
            menu = {}

        if menu_order is None:
            menu_order = []

        self.src_fmt = src_fmt
        self.src_file = src_file
        self.menu = menu
        self.menu_order = menu_order
        self.default = default
        self.timeout = timeout
        self.serial = serial
        self.location = location and location or 'mbr'

    def append(self, label, entry):
        self.menu[label] = entry
        self.menu_order.append(label)

    def remove(self, label):
        del self.menu[label]
        self.menu_order.remove(label)

    @classmethod
    def readExtLinux(cls, src_file):
        menu = {}
        menu_order = []
        default = None
        timeout = None
        location = None
        serial = None
        label = None
        title = None
        kernel = None

        fh = open(src_file)
        try:
            for line in fh:
                l = line.strip()
                els = l.split(None, 2)
                if len(els) == 0:
                    continue
                keywrd = els[0].lower()

                # header
                if (l.startswith('# location ') and len(els) == 3 and
                    els[2] in ['mbr', 'partition']):
                    location = els[2]
                elif keywrd == 'serial' and len(els) > 1:
                    baud = '9600'
                    if len(els) > 2:
                        if ' ' in els[2]:
                            baud, flow = els[2].split(None, 1)
                        else:
                            baud = els[2]
                            flow = None
                    serial = {'port': int(els[1]), 'baud': int(baud), 'flow': flow}
                elif keywrd == 'default' and len(els) == 2:
                    default = els[1]
                elif keywrd == 'timeout' and len(els) == 2:
                    timeout = int(els[1])

                # menu
                elif keywrd == 'label' and len(els) == 2:
                    label = els[1]
                    menu[label] = {}
                    menu_order.append(label)
                    title = None
                elif label:
                    if keywrd == '#':
                        title = l[1:].lstrip()
                    elif keywrd == 'kernel' and len(els) > 1:
                        kernel = els[1]
                    elif keywrd == 'append' and len(els) > 1 and \
                            kernel == 'mboot.c32':
                        if 'tboot' in els[1]:
                            # els[2] contains tboot args, hypervisor,
                            # hypervisor args, kernel,
                            # kernel args & initrd
                            args = map(lambda x: x.strip(), els[2].split('---'))
                            if len(args) == 4:
                                hypervisor = args[1].split(None, 1)
                                kernel = args[2].split(None, 1)
                                if len(hypervisor) == 2 and len(kernel) == 2:
                                    menu[label] = MenuEntry(tboot = els[1],
                                                            tboot_args = args[0],
                                                            hypervisor = hypervisor[0],
                                                            hypervisor_args = hypervisor[1],
                                                            kernel = kernel[0],
                                                            kernel_args = kernel[1],
                                                            initrd = args[3],
                                                            title = title)
                        elif 'xen' in els[1]:
                            # els[2] contains hypervisor args, kernel,
                            # kernel args & initrd
                            args = map(lambda x: x.strip(), els[2].split('---'))
                            if len(args) == 3:
                                kernel = args[1].split(None, 1)
                                if len(kernel) == 2:
                                    menu[label] = MenuEntry(hypervisor = els[1],
                                                            hypervisor_args = args[0],
                                                            kernel = kernel[0],
                                                            kernel_args = kernel[1],
                                                            initrd = args[2],
                                                            title = title)
        finally:
            fh.close()

        return cls('extlinux', src_file, menu, menu_order, default, timeout,
                   serial, location)

    @classmethod
    def readGrub(cls, src_file):
        menu = {}
        menu_order = []
        default = 0
        timeout = None
        location = None
        serial = None
        label = None
        title = None
        hypervisor = None
        hypervisor_args = None
        kernel = None
        kernel_args = None

        def create_label(title):
            global COUNTER

            # FIXME use branding
            if title == 'XenServer':
                return 'xe'

            if title.endswith('(Serial)'):
                return 'xe-serial'
            if title.endswith('Safe Mode'):
                return 'safe'
            if ' / ' in title:
                if '(Serial,' in title:
                    return 'fallback-serial'
                else:
                    return 'fallback'
            COUNTER += 1
            return "label%d" % COUNTER

        fh = open(src_file)
        try:
            for line in fh:
                l = line.strip()
                els = l.split(None, 2)
                if len(els) == 0:
                    continue

                # header
                if (l.startswith('# location ') and len(els) == 3 and
                    els[2] in ['mbr', 'partition']):
                    location = els[2]
                elif els[0] == 'serial' and len(els) > 1:
                    port = 0
                    baud = 9600
                    for arg in l.split(None, 1)[1].split():
                        if '=' in arg:
                            opt, val = arg.split('=')
                            if opt == '--unit':
                                port = int(val)
                            elif opt == '--speed':
                                baud = int(val)
                    serial = {'port': port, 'baud': baud}
                elif els[0] == 'default' and len(els) == 2:
                    # default is index into menu list, fixup later
                    default = int(els[1])
                elif els[0] == 'timeout' and len(els) == 2:
                    timeout = int(els[1]) * 10

                # menu
                elif els[0] == 'title' and len(els) > 1:
                    title = l.split(None, 1)[1]
                elif title:
                    if els[0] == 'kernel' and len(els) > 2:
                        hypervisor, hypervisor_args = (l.split(None, 1)
                                                       [1].split(None, 1))
                    elif els[0] == 'module' and len(els) > 1:
                        if kernel and hypervisor:
                            # second module == initrd
                            label = create_label(title)
                            menu_order.append(label)
                            menu[label] = MenuEntry(hypervisor = hypervisor,
                                                    hypervisor_args = hypervisor_args,
                                                    kernel = kernel,
                                                    kernel_args = kernel_args,
                                                    initrd = els[1], title = title)
                            hypervisor = None
                            kernel = None
                        else:
                            kernel, kernel_args = (l.split(None, 1)
                                                   [1].split(None, 1))
                    elif els[0] == 'initrd' and len(els) > 1:
                        # not multiboot
                        kernel = hypervisor
                        kernel_args = hypervisor_args
                        label = create_label(title)
                        menu_order.append(label)
                        menu[label] = MenuEntry(kernel = kernel,
                                                kernel_args = kernel_args,
                                                initrd = els[1], title = title)
                        hypervisor = None
                        hypervisor_args = None

            # fixup default
            if len(menu_order) > default:
                default = menu_order[default]
        finally:
            fh.close()

        return cls('grub', src_file, menu, menu_order, default,
                   timeout, serial, location)

    @classmethod
    def readGrub2(cls, src_file):
        menu = {}
        menu_order = []
        default = 0
        timeout = None
        serial = None
        title = None
        tboot = None
        tboot_args = None
        hypervisor = None
        hypervisor_args = None
        kernel = None
        kernel_args = None
        initrd = None
        root = None
        menu_entry_extra = None
        menu_entry_contents = []
        boilerplate = []
        boilerplates = []

        def create_label(title):
            global COUNTER

            # FIXME use branding
            if title == 'XenServer':
                return 'xe'
            if title == 'XenServer (Trusted Boot)':
                return 'xe-tboot'

            if title.endswith('(Serial)'):
                return 'xe-serial'
            if title.endswith('(Serial) (Trusted Boot)'):
                return 'xe-serial-tboot'
            if title.endswith('Safe Mode'):
                return 'safe'
            if title.endswith('upgrade'):
                return 'upgrade'
            if ' / ' in title:
                if '(Serial,' in title:
                    return 'fallback-serial'
                else:
                    return 'fallback'
            COUNTER += 1
            return "label%d" % COUNTER

        fh = open(src_file)
        try:
            for line in fh:
                l = line.strip()
                menu_match = re.match("menuentry ['\"]([^']*)['\"](.*){", l)

                # Only parse unindented default and timeout lines to prevent
                # changing these lines in if statements.
                if l.startswith('set default=') and l == line.rstrip():
                    default = l.split('=')[1]
                    match = re.match("['\"](.*)['\"]$", default)
                    if match:
                        default = match.group(1)
                elif l.startswith('set timeout=') and l == line.rstrip():
                    timeout = int(l.split('=')[1]) * 10
                elif l.startswith('serial'):
                    match = re.match("serial --unit=(\d+) --speed=(\d+)", l)
                    if match:
                        serial = {
                            'port': int(match.group(1)),
                            'baud': match.group(2)
                        }
                elif l.startswith('terminal_'):
                    pass
                elif menu_match:
                    title = menu_match.group(1)
                    menu_entry_extra = menu_match.group(2)
                    boilerplates.append(boilerplate)
                    boilerplate = []
                elif title:
                    if l.startswith("multiboot2"):
                        if "tboot" in l:
                            tboot, tboot_args = (l.split(None, 1)
                                                 [1].split(None, 1))
                        else:
                            hypervisor, hypervisor_args = (l.split(None, 1)
                                                           [1].split(None, 1))
                    elif l.startswith("module2"):
                        if not hypervisor:
                            hypervisor, hypervisor_args = (l.split(None, 1)
                                                           [1].split(None, 1))
                        elif kernel:
                            initrd = l.split(None, 1)[1]
                        else:
                            kernel, kernel_args = (l.split(None, 1)
                                                   [1].split(None, 1))
                    elif l.startswith("linux"):
                        kernel, kernel_args = (l.split(None, 1)
                                               [1].split(None, 1))
                    elif l.startswith("initrd"):
                        initrd = l.split(None, 1)[1]
                    elif l.startswith("search --label --set root"):
                        root = l.split()[4]
                    elif l == "}":
                        label = create_label(title)
                        menu_order.append(label)
                        menu[label] = MenuEntry(tboot = tboot,
                                                tboot_args = tboot_args,
                                                hypervisor = hypervisor,
                                                hypervisor_args = hypervisor_args,
                                                kernel = kernel,
                                                kernel_args = kernel_args,
                                                initrd = initrd, title = title,
                                                root = root)
                        menu[label].extra = menu_entry_extra
                        menu[label].contents = menu_entry_contents

                        title = None
                        tboot = None
                        tboot_args = None
                        hypervisor = None
                        hypervisor_args = None
                        kernel = None
                        kernel_args = None
                        initrd = None
                        root = None
                        menu_entry_extra = None
                        menu_entry_contents = []

                    else:
                        menu_entry_contents.append(line.rstrip())
                else:
                    boilerplate.append(line.rstrip())

            # Try parse default as an index into the menu_order list.
            # If this fails, it is probably a string, so leave it unchanged.
            try:
                default = menu_order[int(default)]
            except ValueError, KeyError:
                pass
        finally:
            fh.close()

        bootloader = cls('grub2', src_file, menu, menu_order, default,
                         timeout, serial, None)
        bootloader.boilerplate = boilerplates
        return bootloader

    @classmethod
    def loadExisting(cls, root = '/'):
        if os.path.exists(os.path.join(root, "boot/efi/EFI/xenserver/grub.cfg")):
            return cls.readGrub2(os.path.join(root, "boot/efi/EFI/xenserver/grub.cfg"))
        elif os.path.exists(os.path.join(root, "boot/grub/grub.cfg")):
            return cls.readGrub2(os.path.join(root, "boot/grub/grub.cfg"))
        elif os.path.exists(os.path.join(root, "boot/grub2/grub.cfg")):
            return cls.readGrub2(os.path.join(root, "boot/grub2/grub.cfg"))
        elif os.path.exists(os.path.join(root, "boot/extlinux.conf")):
            return cls.readExtLinux(os.path.join(root, "boot/extlinux.conf"))
        elif os.path.exists(os.path.join(root, "boot/grub/menu.lst")):
            return cls.readGrub(os.path.join(root, "boot/grub/menu.lst"))
        else:
            raise RuntimeError, "No existing bootloader configuration found"

    def writeExtLinux(self, dst_file = None):
        if hasattr(dst_file, 'name'):
            fh = dst_file
        else:
            fh = open(dst_file, 'w')
        print >> fh, "# location " + self.location

        if self.serial:
            if self.serial.get('flow', None) is None: 
                print >> fh, "serial %d %d" % (self.serial['port'],
                                               self.serial['baud'])
            else:
                print >> fh, "serial %d %d %s" % (self.serial['port'],
                                                  self.serial['baud'],
                                                  self.serial['flow'])
        if self.default:
            print >> fh, "default " + self.default
        print >> fh, "prompt 1"
        if self.timeout:
            print >> fh, "timeout %d" % self.timeout

        for label in self.menu_order:
            print >> fh, "\nlabel " + label
            m = self.menu[label]
            if m.title:
                print >> fh, "  # " + m.title
            if m.tboot:
                print >> fh, "  kernel mboot.c32"
                print >> fh, "  append %s %s --- %s %s --- %s %s --- %s" % \
                    (m.tboot, m.tboot_args, m.hypervisor, m.hypervisor_args,
                     m.kernel, m.kernel_args, m.initrd)
            elif m.hypervisor:
                print >> fh, "  kernel mboot.c32"
                print >> fh, "  append %s %s --- %s %s --- %s" % \
                    (m.hypervisor, m.hypervisor_args, m.kernel, m.kernel_args, m.initrd)
            else:
                print >> fh, "  kernel " + m.kernel
                print >> fh, "  append " + m.kernel_args
                print >> fh, "  initrd " + m.initrd
        if not hasattr(dst_file, 'name'):
            fh.close()

    def writeGrub(self, dst_file = None):
        if hasattr(dst_file, 'name'):
            fh = dst_file
        else:
            fh = open(dst_file, 'w')
        print >> fh, "# location " + self.location

        if self.serial:
            print >> fh, "serial --unit=%d --speed=%s" % (self.serial['port'],
                                                          self.serial['baud'])
            print >> fh, "terminal --timeout=10 console serial"
        else:
            print >> fh, "terminal console"
        if self.default:
            for i in range(len(self.menu_order)):
                if self.menu_order[i] == self.default:
                    print >> fh, "default %d" % i
                    break
        if self.timeout:
            print >> fh, "timeout %d" % (self.timeout / 10)

        for label in self.menu_order:
            m = self.menu[label]
            print >> fh, "\ntitle " + m.title
            if m.hypervisor:
                print >> fh, "   kernel " + m.hypervisor + " " + m.hypervisor_args
                print >> fh, "   module " + m.kernel + " " + m.kernel_args
                print >> fh, "   module " + m.initrd
            else:
                print >> fh, "   kernel " + m.kernel + " " + m.kernel_args
                print >> fh, "   initrd " + m.initrd
        if not hasattr(dst_file, 'name'):
            fh.close()

    def writeGrub2(self, dst_file = None):
        if hasattr(dst_file, 'name'):
            fh = dst_file
        else:
            fh = open(dst_file, 'w')

        if self.serial:
            print >> fh, "serial --unit=%d --speed=%s" % (self.serial['port'],
                                                          self.serial['baud'])
            print >> fh, "terminal_input serial console"
            print >> fh, "terminal_output serial console"
        if self.default:
            for i in range(len(self.menu_order)):
                if self.menu_order[i] == self.default:
                    print >> fh, "set default=%d" % i
                    break
            else:
                print >> fh, "set default='%s'" % str(self.default)
        if self.timeout:
            print >> fh, "set timeout=%d" % (self.timeout / 10)

        self.boilerplate = getattr(self, 'boilerplate', [])
        self.boilerplate.reverse()

        for label in self.menu_order:
            m = self.menu[label]

            if self.boilerplate:
                text = self.boilerplate.pop()
                if text:
                    print >> fh, "\n".join(text)

            extra = ' '
            try:
                extra = m.extra
            except AttributeError:
                pass
            print >> fh, "menuentry '%s'%s{" % (m.title, extra)

            try:
                contents = "\n".join(m.contents)
                if contents:
                    print >> fh, contents
            except AttributeError:
                pass

            if m.root:
                print >> fh, "\tsearch --label --set root %s" % m.root

            if m.hypervisor:
                if m.tboot:
                    print >> fh, "\tmultiboot2 %s %s" % (m.tboot, m.tboot_args)
                    print >> fh, "\tmodule2 %s %s" % (m.hypervisor, m.hypervisor_args)
                else:
                    print >> fh, "\tmultiboot2 %s %s" % (m.hypervisor, m.hypervisor_args)
                if m.kernel:
                    print >> fh, "\tmodule2 %s %s" % (m.kernel, m.kernel_args)
                if m.initrd:
                    print >> fh, "\tmodule2 %s" % m.initrd
            else:
                if m.kernel:
                    print >> fh, "\tlinux %s %s" % (m.kernel, m.kernel_args)
                if m.initrd:
                    print >> fh, "\tinitrd %s" % m.initrd
            print >> fh, "}"
        if not hasattr(dst_file, 'name'):
            fh.close()

    def commit(self, dst_file = None):
        if not dst_file:
            dst_file = self.src_file

        # write to temp file in final destination directory
        fd, tmp_file = tempfile.mkstemp(dir = os.path.dirname(dst_file))

        if self.src_fmt == 'extlinux':
            self.writeExtLinux(tmp_file)
        elif self.src_fmt == 'grub':
            self.writeGrub(tmp_file)
        elif self.src_fmt == 'grub2':
            self.writeGrub2(tmp_file)

        # atomically replace destination file
        os.close(fd)
        os.rename(tmp_file, dst_file)

    @classmethod
    def newDefault(cls, kernel_link_name, initrd_link_name, root = '/'):
        b = cls.loadExisting(root)
        # FIXME handle initial case
        if b.menu[b.default].kernel != kernel_link_name:
            backup = []
            if not os.path.exists(os.path.join(root, kernel_link_name[1:])):
                raise RuntimeError, "kernel symlink not found"
            if not os.path.exists(os.path.join(root, initrd_link_name[1:])):
                raise RuntimeError, "initrd symlink not found"
            old_kernel_link = b.menu[b.default].kernel
            old_ver = 'old'
            m = re.search(r'(-\d+\.\d+)-', old_kernel_link)
            if m:
                old_ver = m.group(1)

            for k, v in b.menu.items():
                if v.kernel == old_kernel_link:
                    c = copy.deepcopy(v)
                    if c.title:
                        c.title += " (%s)" % old_ver
                    backup.append((k+old_ver, c))
                    v.kernel = kernel_link_name
                    v.initrd = initrd_link_name

            if len(backup) > 0:
                for l, e in backup:
                    b.append(l, e)
            b.commit()
