#
# Project Kimchi
#
# Copyright IBM, Corp. 2013-2014
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA

import os
import string
import socket
import time
import urlparse
import uuid


from distutils.version import LooseVersion


from kimchi import osinfo
from kimchi.exception import InvalidParameter, IsoFormatError, MissingParameter
from kimchi.imageinfo import probe_image, probe_img_info
from kimchi.isoinfo import IsoImage
from kimchi.utils import check_url_path, pool_name_from_uri
from lxml import etree
from lxml.builder import E


QEMU_NAMESPACE = "xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'"


class VMTemplate(object):
    _bus_to_dev = {'ide': 'hd', 'virtio': 'vd', 'scsi': 'sd'}

    def __init__(self, args, scan=False):
        """
        Construct a VM Template from a widely variable amount of information.
        The only required parameter is a name for the VMTemplate.  If present,
        the os_distro and os_version fields are used to lookup recommended
        settings.  Any parameters provided by the caller will override the
        defaults.  If scan is True and a cdrom or a base img is present, the
        operating system will be detected by probing the installation media.
        """
        self.info = {}
        self.fc_host_support = args.get('fc_host_support')

        # Fetch defaults based on the os distro and version
        distro, version = self._get_os_info(args, scan)
        os_distro = args.get('os_distro', distro)
        os_version = args.get('os_version', version)
        entry = osinfo.lookup(os_distro, os_version)
        self.info.update(entry)

        # Auto-generate a template name and no one is passed
        if 'name' not in args or args['name'] == '':
            args['name'] = self._gen_name(distro, version)
        self.name = args['name']

        # Override with the passed in parameters
        graph_args = args.get('graphics')
        if graph_args:
            graphics = dict(self.info['graphics'])
            graphics.update(graph_args)
            args['graphics'] = graphics
        self.info.update(args)

    def _get_os_info(self, args, scan):
        distro = version = 'unknown'

        # Identify the cdrom if present
        iso = args.get('cdrom', '')
        if len(iso) > 0:
            if not iso.startswith('/'):
                self.info.update({'iso_stream': True})

            if scan:
                distro, version = self.get_iso_info(iso)

            return distro, version

        # CDROM is not presented: check for base image
        base_imgs = []
        for d in args.get('disks', []):
            if 'base' in d.keys():
                base_imgs.append(d)
                if scan:
                    distro, version = probe_image(d['base'])

                if 'size' not in d.keys():
                    d['size'] = probe_img_info(d['base'])['virtual-size']

        if len(base_imgs) == 0:
            raise MissingParameter("KCHTMPL0016E")

        return distro, version

    def _gen_name(self, distro, version):
        if distro == 'unknown':
            name = str(uuid.uuid4())
        else:
            name = distro + version + '.' + str(int(time.time() * 1000))
        return name

    def get_iso_info(self, iso):
        iso_prefixes = ['/', 'http', 'https', 'ftp', 'ftps', 'tftp']
        if len(filter(iso.startswith, iso_prefixes)) == 0:
            raise InvalidParameter("KCHTMPL0006E", {'param': iso})
        try:
            iso_img = IsoImage(iso)
            return iso_img.probe()
        except IsoFormatError:
            raise InvalidParameter("KCHISO0001E", {'filename': iso})

    def _get_cdrom_xml(self, libvirt_stream_protocols, qemu_stream_dns):
        if 'cdrom' not in self.info:
            return ''
        bus = self.info['cdrom_bus']
        dev = "%s%s" % (self._bus_to_dev[bus],
                        string.lowercase[self.info['cdrom_index']])

        local_file = """
            <disk type='file' device='cdrom'>
              <driver name='qemu' type='raw'/>
              <source file='%(src)s' />
              <target dev='%(dev)s' bus='%(bus)s'/>
              <readonly/>
            </disk>
        """

        network_file = """
            <disk type='network' device='cdrom'>
              <driver name='qemu' type='raw'/>
              <source protocol='%(protocol)s' name='%(url_path)s'>
                <host name='%(hostname)s' port='%(port)s'/>
              </source>
              <target dev='%(dev)s' bus='%(bus)s'/>
              <readonly/>
            </disk>
        """

        qemu_stream_cmdline = """
            <qemu:commandline>
              <qemu:arg value='-drive'/>
              <qemu:arg value='file=%(url)s,if=none,id=drive-%(bus)s0-1-0,\
readonly=on,format=raw'/>
              <qemu:arg value='-device'/>
              <qemu:arg value='%(bus)s-cd,bus=%(bus)s.1,unit=0,\
drive=drive-%(bus)s0-1-0,id=%(bus)s0-1-0'/>
            </qemu:commandline>
        """

        if not self.info.get('iso_stream', False):
            params = {'src': self.info['cdrom'], 'dev': dev, 'bus': bus}
            return local_file % (params)

        output = urlparse.urlparse(self.info['cdrom'])
        port = output.port
        protocol = output.scheme
        hostname = output.hostname
        url_path = output.path

        if port is None:
            port = socket.getservbyname(protocol)

        url = self.info['cdrom']
        if not qemu_stream_dns:
            hostname = socket.gethostbyname(hostname)
            url = protocol + "://" + hostname + ":" + str(port) + url_path

        if protocol not in libvirt_stream_protocols:
            return qemu_stream_cmdline % {'url': url, 'bus': bus}

        params = {'protocol': protocol, 'url_path': url_path,
                  'hostname': hostname, 'port': port, 'dev': dev, 'bus': bus}

        return network_file % (params)

    def _get_disks_xml(self, vm_uuid):
        storage_path = self._get_storage_path()
        ret = ""
        for i, disk in enumerate(self.info['disks']):
            index = disk.get('index', i)
            volume = "%s-%s.img" % (vm_uuid, index)
            src = os.path.join(storage_path, volume)
            dev = "%s%s" % (self._bus_to_dev[self.info['disk_bus']],
                            string.lowercase[index])
            fmt = 'raw' if self._get_storage_type() in ['logical'] else 'qcow2'
            params = {'src': src, 'dev': dev, 'bus': self.info['disk_bus'],
                      'type': fmt}
            ret += """
            <disk type='file' device='disk'>
              <driver name='qemu' type='%(type)s' cache='none'/>
              <source file='%(src)s' />
              <target dev='%(dev)s' bus='%(bus)s' />
            </disk>
            """ % params
        return ret

    def _get_graphics_xml(self, params):
        graphics_xml = """
            <graphics type='%(type)s' autoport='yes' listen='%(listen)s'>
            </graphics>
        """
        spicevmc_xml = """
            <channel type='spicevmc'>
              <target type='virtio' name='com.redhat.spice.0'/>
            </channel>
        """
        graphics = dict(self.info['graphics'])
        if params:
            graphics.update(params)
        graphics_xml = graphics_xml % graphics
        if graphics['type'] == 'spice':
            graphics_xml = graphics_xml + spicevmc_xml
        return graphics_xml

    def _get_scsi_disks_xml(self):
        luns = [disk['volume'] for disk in self.info.get('disks', {})
                if 'volume' in disk]

        ret = ""
        # Passthrough configuration
        disk_xml = """
            <disk type='volume' device='lun'>
              <driver name='qemu' type='raw' cache='none'/>
              <source dev='%(src)s'/>
              <target dev='%(dev)s' bus='scsi'/>
            </disk>"""
        if not self.fc_host_support:
            disk_xml = disk_xml.replace('volume', 'block')

        pool = self._storage_validate()
        # Creating disk xml for each lun passed
        for index, lun in enumerate(luns):
            path = pool.storageVolLookupByName(lun).path()
            dev = "sd%s" % string.lowercase[index]
            params = {'src': path, 'dev': dev}
            ret = ret + disk_xml % params
        return ret

    def _get_iscsi_disks_xml(self):
        def build_disk_xml(children=[]):
            disk = E.disk(type='block', device='disk')
            disk.extend(children)
            return etree.tostring(disk)

        ret = ""
        children = []
        children.append(E.driver(name='qemu', type='raw'))
        disk_bus = self.info['disk_bus']
        dev_prefix = self._bus_to_dev[disk_bus]
        pool_name = pool_name_from_uri(self.info['storagepool'])
        for i, d in enumerate(self.info['disks']):
            source = E.source(dev=self._get_volume_path(pool_name,
                                                        d.get('volume')))
            # FIXME if more than 26 disks
            target = E.target(dev=dev_prefix + string.lowercase[i],
                              bus=disk_bus)
            ret += build_disk_xml(children+[source, target])

        return ret

    def to_volume_list(self, vm_uuid):
        storage_path = self._get_storage_path()
        fmt = 'raw' if self._get_storage_type() in ['logical'] else 'qcow2'
        ret = []
        for i, d in enumerate(self.info['disks']):
            index = d.get('index', i)
            volume = "%s-%s.img" % (vm_uuid, index)

            info = {'name': volume,
                    'capacity': d['size'],
                    'format': fmt,
                    'path': '%s/%s' % (storage_path, volume)}
            info['allocation'] = 0 if fmt == 'qcow2' else info['capacity']

            if 'base' in d:
                info['base'] = dict()
                base_fmt = probe_img_info(d['base'])['format']
                if base_fmt is None:
                    raise InvalidParameter("KCHTMPL0024E", {'path': d['base']})
                info['base']['path'] = d['base']
                info['base']['format'] = base_fmt

            v_tree = E.volume(E.name(info['name']))
            v_tree.append(E.allocation(str(info['allocation']), unit='G'))
            v_tree.append(E.capacity(str(info['capacity']), unit='G'))
            target = E.target(
                E.format(type=info['format']), E.path(info['path']))
            if 'base' in d:
                v_tree.append(E.backingStore(
                    E.path(info['base']['path']),
                    E.format(type=info['base']['format'])))
            v_tree.append(target)
            info['xml'] = etree.tostring(v_tree)
            ret.append(info)
        return ret

    def _disable_vhost(self):
        # Hack to disable vhost feature in Ubuntu LE and SLES LE (PPC)
        driver = ""
        if self.info['arch'] == 'ppc64' and \
            ((self.info['os_distro'] == 'ubuntu' and LooseVersion(
             self.info['os_version']) >= LooseVersion('14.04')) or
             (self.info['os_distro'] == 'sles' and LooseVersion(
              self.info['os_version']) >= LooseVersion('12'))):
            driver = "  <driver name='qemu'/>\n            "
        return driver

    def _get_networks_xml(self):
        network = """
            <interface type='network'>
              <source network='%(network)s'/>
              <model type='%(nic_model)s'/>
            %(driver)s</interface>
        """
        networks = ""
        net_info = {"nic_model": self.info['nic_model'],
                    "driver": self._disable_vhost()}
        for nw in self.info['networks']:
            net_info['network'] = nw
            networks += network % net_info
        return networks

    def _get_input_output_xml(self):
        sound = """
            <sound model='%(sound_model)s' />
        """
        mouse = """
            <input type='mouse' bus='%(mouse_bus)s'/>
        """
        keyboard = """
            <input type='kbd' bus='%(kbd_bus)s'> </input>
        """
        tablet = """
            <input type='tablet' bus='%(kbd_bus)s'> </input>
        """

        input_output = ""
        if 'mouse_bus' in self.info.keys():
            input_output += mouse % self.info
        if 'kbd_bus' in self.info.keys():
            input_output += keyboard % self.info
        if 'tablet_bus' in self.info.keys():
            input_output += tablet % self.info
        if 'sound_model' in self.info.keys():
            input_output += sound % self.info
        return input_output

    def to_vm_xml(self, vm_name, vm_uuid, **kwargs):
        params = dict(self.info)
        params['name'] = vm_name
        params['uuid'] = vm_uuid
        params['networks'] = self._get_networks_xml()
        params['input_output'] = self._get_input_output_xml()
        params['qemu-namespace'] = ''
        params['cdroms'] = ''
        params['qemu-stream-cmdline'] = ''
        graphics = kwargs.get('graphics')
        params['graphics'] = self._get_graphics_xml(graphics)

        # Current implementation just allows to create disk in one single
        # storage pool, so we cannot mix the types (scsi volumes vs img file)
        storage_type = self._get_storage_type()
        if storage_type == "iscsi":
            params['disks'] = self._get_iscsi_disks_xml()
        elif storage_type == "scsi":
            params['disks'] = self._get_scsi_disks_xml()
        else:
            params['disks'] = self._get_disks_xml(vm_uuid)

        qemu_stream_dns = kwargs.get('qemu_stream_dns', False)
        libvirt_stream_protocols = kwargs.get('libvirt_stream_protocols', [])
        cdrom_xml = self._get_cdrom_xml(libvirt_stream_protocols,
                                        qemu_stream_dns)

        if not urlparse.urlparse(self.info.get('cdrom', "")).scheme in \
                libvirt_stream_protocols and \
                params.get('iso_stream', False):
            params['qemu-namespace'] = QEMU_NAMESPACE
            params['qemu-stream-cmdline'] = cdrom_xml
        else:
            params['cdroms'] = cdrom_xml

        xml = """
        <domain type='%(domain)s' %(qemu-namespace)s>
          %(qemu-stream-cmdline)s
          <name>%(name)s</name>
          <uuid>%(uuid)s</uuid>
          <memory unit='MiB'>%(memory)s</memory>
          <vcpu>%(cpus)s</vcpu>
          <os>
            <type arch='%(arch)s'>hvm</type>
            <boot dev='hd'/>
            <boot dev='cdrom'/>
          </os>
          <features>
            <acpi/>
            <apic/>
            <pae/>
          </features>
          <clock offset='utc'/>
          <on_poweroff>destroy</on_poweroff>
          <on_reboot>restart</on_reboot>
          <on_crash>restart</on_crash>
          <devices>
            %(disks)s
            %(cdroms)s
            %(networks)s
            %(graphics)s
            %(input_output)s
            <memballoon model='virtio' />
          </devices>
        </domain>
        """ % params
        return xml

    def validate(self):
        self._storage_validate()
        self._network_validate()
        self._iso_validate()

    def _iso_validate(self):
        pass

    def _network_validate(self):
        pass

    def _storage_validate(self):
        pass

    def fork_vm_storage(self, vm_uuid):
        pass

    def _get_storage_path(self):
        return ''

    def _get_storage_type(self):
        return ''

    def _get_volume_path(self):
        return ''

    def _get_all_networks_name(self):
        return []

    def _get_all_storagepools_name(self):
        return []

    def validate_integrity(self):
        invalid = {}
        # validate networks integrity
        invalid_networks = list(set(self.info['networks']) -
                                set(self._get_all_networks_name()))
        if invalid_networks:
            invalid['networks'] = invalid_networks

        # validate storagepools integrity
        pool_uri = self.info['storagepool']
        pool_name = pool_name_from_uri(pool_uri)
        if pool_name not in self._get_all_storagepools_name():
            invalid['storagepools'] = [pool_name]

        # validate iso integrity
        # FIXME when we support multiples cdrom devices
        iso = self.info.get('cdrom')
        if iso and not (os.path.isfile(iso) or check_url_path(iso)):
            invalid['cdrom'] = [iso]

        self.info['invalid'] = invalid

        return self.info