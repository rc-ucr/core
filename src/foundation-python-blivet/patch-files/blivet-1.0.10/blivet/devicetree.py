# devicetree.py
# Device management for anaconda's storage configuration module.
#
# Copyright (C) 2009-2014  Red Hat, Inc.
#
# This copyrighted material is made available to anyone wishing to use,
# modify, copy, or redistribute it subject to the terms and conditions of
# the GNU General Public License v.2, or (at your option) any later version.
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY expressed or implied, including the implied warranties of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General
# Public License for more details.  You should have received a copy of the
# GNU General Public License along with this program; if not, write to the
# Free Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301, USA.  Any Red Hat trademarks that are incorporated in the
# source code or documentation are not subject to the GNU General Public
# License and may only be used or replicated with the express permission of
# Red Hat, Inc.
#
# Red Hat Author(s): Dave Lehman <dlehman@redhat.com>
#

import os
import re
import shutil
import pprint
import copy

from gi.repository import BlockDev as blockdev

from .errors import DeviceError, DeviceTreeError, DiskLabelCommitError, DMError, FSError, InvalidDiskLabelError, LUKSError, StorageError, UnusableConfigurationError
from .devices import BTRFSDevice, BTRFSSubVolumeDevice, BTRFSVolumeDevice, BTRFSSnapShotDevice
from .devices import DASDDevice, DMDevice, DMLinearDevice, DMRaidArrayDevice, DiskDevice
from .devices import FcoeDiskDevice, FileDevice, LoopDevice, LUKSDevice
from .devices import LVMLogicalVolumeDevice, LVMVolumeGroupDevice
from .devices import LVMThinPoolDevice, LVMThinLogicalVolumeDevice
from .devices import LVMSnapShotDevice, LVMThinSnapShotDevice
from .devices import MDRaidArrayDevice, MDBiosRaidArrayDevice
from .devices import MDContainerDevice
from .devices import MultipathDevice, NoDevice, OpticalDevice
from .devices import PartitionDevice, ZFCPDiskDevice, iScsiDiskDevice
from .devices import devicePathToName
from .deviceaction import ActionCreateDevice, ActionDestroyDevice, action_type_from_string, action_object_from_string
from . import formats
from .formats import getFormat
from .formats.fs import nodev_filesystems
from .devicelibs import lvm
from .devicelibs import edd
from .devicelibs import raid
from . import udev
from . import util
from .platform import platform
from . import tsort
from .flags import flags
from .storage_log import log_exception_info, log_method_call, log_method_return
from .i18n import _
from .size import Size

import logging
log = logging.getLogger("blivet")

class DeviceTree(object):
    """ A quasi-tree that represents the devices in the system.

        The tree contains a list of :class:`~.devices.StorageDevice` instances,
        which does not necessarily reflect the actual state of the system's
        devices. :class:`~.deviceaction.DeviceAction` is used to perform
        modifications to the tree, except when initially populating the tree.

        :class:`~.deviceaction.DeviceAction` instances are registered, possibly
        causing the addition or removal of :class:`~.devices.StorageDevice`
        instances to/from the tree. A :class:`~.deviceaction.DeviceAction`
        is reversible up to the time its 'execute' method is called.

        Only one action of any given type/object pair should exist for
        any given device at any given time.

        :class:`~.deviceaction.DeviceAction` instances can only be registered
        for leaf devices, except for resize actions.
    """

    def __init__(self, conf=None, passphrase=None, luksDict=None,
                 iscsi=None, dasd=None):
        """

            :keyword conf: storage discovery configuration
            :type conf: :class:`~.StorageDiscoveryConfig`
            :keyword passphrase: default LUKS passphrase
            :keyword luksDict: a dict with UUID keys and passphrase values
            :type luksDict: dict
            :keyword iscsi: ISCSI control object
            :type iscsi: :class:`~.iscsi.iscsi`
            :keyword dasd: DASD control object
            :type dasd: :class:`~.dasd.DASD`

        """
        self.reset(conf, passphrase, luksDict, iscsi, dasd)

    def reset(self, conf=None, passphrase=None, luksDict=None,
              iscsi=None, dasd=None):
        """ Reset the instance to its initial state. """
        # internal data members
        self._devices = []
        self._actions = []
        self._completed_actions = []

        # a list of all device names we encounter
        self.names = []

        self._hidden = []

        # indicates whether or not the tree has been fully populated
        self.populated = False

        self.exclusiveDisks = getattr(conf, "exclusiveDisks", [])
        self.ignoredDisks = getattr(conf, "ignoredDisks", [])
        self.iscsi = iscsi
        self.dasd = dasd

        self.diskImages = {}
        images = getattr(conf, "diskImages", {})
        if images:
            # this will overwrite self.exclusiveDisks
            self.setDiskImages(images)

        # protected device specs as provided by the user
        self.protectedDevSpecs = getattr(conf, "protectedDevSpecs", [])
        self.liveBackingDevice = None

        # names of protected devices at the time of tree population
        self.protectedDevNames = []

        self.unusedRaidMembers = []

        # initialize attributes that may later hold cached lvm info
        self.dropLVMCache()

        self.__passphrases = []
        if passphrase:
            self.__passphrases.append(passphrase)

        self.__luksDevs = {}
        if luksDict and isinstance(luksDict, dict):
            self.__luksDevs = luksDict
            self.__passphrases.extend([p for p in luksDict.values() if p])

        lvm.lvm_cc_resetFilter()

        self._cleanup = False

    def setDiskImages(self, images):
        """ Set the disk images and reflect them in exclusiveDisks.

            :param images: dict with image name keys and filename values
            :type images: dict

            .. note::

                Disk images are automatically exclusive. That means that, in the
                presence of disk images, any local storage not associated with
                the disk images is ignored.
        """
        self.diskImages = images
        # disk image files are automatically exclusive
        self.exclusiveDisks = list(self.diskImages.keys())

    def addIgnoredDisk(self, disk):
        self.ignoredDisks.append(disk)
        lvm.lvm_cc_addFilterRejectRegexp(disk)

    @property
    def pvInfo(self):
        if self._pvs_cache is None:
            pvs = blockdev.lvm.pvs()
            self._pvs_cache = dict((pv.pv_name, pv) for pv in pvs) # pylint: disable=attribute-defined-outside-init

        return self._pvs_cache

    @property
    def lvInfo(self):
        if self._lvs_cache is None:
            lvs = blockdev.lvm.lvs()
            self._lvs_cache = dict(("%s-%s" % (lv.vg_name, lv.lv_name), lv) for lv in lvs) # pylint: disable=attribute-defined-outside-init

        return self._lvs_cache

    def dropLVMCache(self):
        """ Drop cached lvm information. """
        self._pvs_cache = None # pylint: disable=attribute-defined-outside-init
        self._lvs_cache = None # pylint: disable=attribute-defined-outside-init

    def pruneActions(self):
        """ Remove redundant/obsolete actions from the action list. """
        for action in reversed(self._actions[:]):
            if action not in self._actions:
                log.debug("action %d already pruned", action.id)
                continue

            for obsolete in self._actions[:]:
                if action.obsoletes(obsolete):
                    log.info("removing obsolete action %d (%d)",
                             obsolete.id, action.id)
                    self._actions.remove(obsolete)

                    if obsolete.obsoletes(action) and action in self._actions:
                        log.info("removing mutually-obsolete action %d (%d)",
                                 action.id, obsolete.id)
                        self._actions.remove(action)

    def sortActions(self):
        """ Sort actions based on dependencies. """
        if not self._actions:
            return

        edges = []

        # collect all ordering requirements for the actions
        for action in self._actions:
            action_idx = self._actions.index(action)
            children = []
            for _action in self._actions:
                if _action == action:
                    continue

                # create edges based on both action type and dependencies.
                if _action.requires(action):
                    children.append(_action)

            for child in children:
                child_idx = self._actions.index(child)
                edges.append((action_idx, child_idx))

        # create a graph reflecting the ordering information we have
        graph = tsort.create_graph(list(range(len(self._actions))), edges)

        # perform a topological sort based on the graph's contents
        order = tsort.tsort(graph)

        # now replace self._actions with a sorted version of the same list
        actions = []
        for idx in order:
            actions.append(self._actions[idx])
        self._actions = actions

    def _preProcessActions(self):
        """ Prepare the action queue for execution. """
        for action in self._actions:
            log.debug("action: %s", action)

        log.info("pruning action queue...")
        self.pruneActions()

        problematic = self.findActiveDevicesOnActionDisks()
        if problematic:
            if flags.installer_mode:
                self.teardownAll()
            else:
                raise RuntimeError("partitions in use on disks with changes "
                                   "pending: %s" %
                                   ",".join(problematic))

        log.info("resetting parted disks...")
        for device in self.devices:
            if device.partitioned:
                device.format.resetPartedDisk()

            if device.originalFormat.type == "disklabel" and \
               device.originalFormat != device.format:
                device.originalFormat.resetPartedDisk()

        # Call preCommitFixup on all devices
        mpoints = [getattr(d.format, 'mountpoint', "") for d in self.devices]
        for device in self.devices:
            device.preCommitFixup(mountpoints=mpoints)

        # Also call preCommitFixup on any devices we're going to
        # destroy (these are already removed from the tree)
        for action in self._actions:
            if isinstance(action, ActionDestroyDevice):
                action.device.preCommitFixup(mountpoints=mpoints)

        # setup actions to create any extended partitions we added
        #
        # If the extended partition was explicitly requested it will already
        # have an action registered.
        #
        # XXX At this point there can be duplicate partition paths in the
        #     tree (eg: non-existent sda6 and previous sda6 that will become
        #     sda5 in the course of partitioning), so we access the list
        #     directly here.
        for device in self._devices:
            if isinstance(device, PartitionDevice) and \
               device.isExtended and not device.exists and \
               not self.findActions(device=device, action_type="create"):
                # don't properly register the action since the device is
                # already in the tree
                action = ActionCreateDevice(device)
                # apply the action first in case the apply method fails
                action.apply()
                self._actions.append(action)

        log.info("sorting actions...")
        self.sortActions()
        for action in self._actions:
            log.debug("action: %s", action)

            # Remove lvm filters for devices we are operating on
            for device in (d for d in self._devices if d.dependsOn(action.device)):
                lvm.lvm_cc_removeFilterRejectRegexp(device.name)

    def _postProcessActions(self):
        """ Clean up relics from action queue execution. """
        # removal of partitions makes use of originalFormat, so it has to stay
        # up to date in case of multiple passes through this method
        for disk in (d for d in self.devices if d.partitioned):
            disk.format.updateOrigPartedDisk()
            disk.originalFormat = copy.deepcopy(disk.format)

        # now we have to update the parted partitions of all devices so they
        # match the parted disks we just updated
        for partition in self.getDevicesByInstance(PartitionDevice):
            pdisk = partition.disk.format.partedDisk
            partition.partedPartition = pdisk.getPartitionByPath(partition.path)

    def findActiveDevicesOnActionDisks(self):
        """ Return a list of devices using the disks we plan to change. """
        # Find out now if there are active devices using partitions on disks
        # whose disklabels we are going to change. If there are, do not proceed.
        disks = []
        for action in self._actions:
            disk = None
            if action.isFormat and action.format.type == "disklabel":
                disk = action.device

            if disk is not None and disk not in disks:
                disks.append(disk)

        active = []
        for dev in self.devices:
            if dev.status and not dev.isDisk and \
               not isinstance(dev, PartitionDevice):
                active.append(dev)

            elif dev.format.status and not dev.isDisk:
                active.append(dev)

        devices = [a.name for a in active if any(d in disks for d in a.disks)]
        return devices

    def processActions(self, callbacks=None, dryRun=None):
        """
        Execute all registered actions.

        :param callbacks: callbacks to be invoked when actions are executed
        :type callbacks: :class:`~.callbacks.DoItCallbacks`

        """

        self._preProcessActions()

        for action in self._actions[:]:
            log.info("executing action: %s", action)
            if not dryRun:
                try:
                    action.execute(callbacks)
                except DiskLabelCommitError:
                    # it's likely that a previous action
                    # triggered setup of an lvm or md device.
                    # include deps no longer in the tree due to pending removal
                    devs = self._devices + [a.device for a in self._actions]
                    for dep in set(devs):
                        if dep.exists and dep.dependsOn(action.device.disk):
                            dep.teardown(recursive=True)

                    action.execute(callbacks)

                udev.settle()
                for device in self._devices:
                    # make sure we catch any renumbering parted does
                    if device.exists and isinstance(device, PartitionDevice):
                        device.updateName()
                        device.format.device = device.path

                self._completed_actions.append(self._actions.pop(0))

        self._postProcessActions()

    def _addDevice(self, newdev, new=True):
        """ Add a device to the tree.

            :param newdev: the device to add
            :type newdev: a subclass of :class:`~.devices.StorageDevice`

            Raise ValueError if the device's identifier is already
            in the list.
        """
        if newdev.uuid and newdev.uuid in [d.uuid for d in self._devices] and \
           not isinstance(newdev, NoDevice):
            raise ValueError("device is already in tree")

        # make sure this device's parent devices are in the tree already
        for parent in newdev.parents:
            if parent not in self._devices:
                raise DeviceTreeError("parent device not in tree")

        newdev.addHook(new=new)
        self._devices.append(newdev)

        # don't include "req%d" partition names
        if ((newdev.type != "partition" or
             not newdev.name.startswith("req")) and
            newdev.type != "btrfs volume" and
            newdev.name not in self.names):
            self.names.append(newdev.name)
        log.info("added %s %s (id %d) to device tree", newdev.type,
                                                       newdev.name,
                                                       newdev.id)

    def _removeDevice(self, dev, force=None, modparent=True):
        """ Remove a device from the tree.

            :param dev: the device to remove
            :type dev: a subclass of :class:`~.devices.StorageDevice`
            :keyword force: whether to force removal of a non-leaf device
            :type force: bool
            :keyword modparent: update parent device to account for removal
            :type modparent: bool

            .. note::

                Only leaves may be removed.
        """
        if dev not in self._devices:
            raise ValueError("Device '%s' not in tree" % dev.name)

        if not dev.isleaf and not force:
            log.debug("%s has %d kids", dev.name, dev.kids)
            raise ValueError("Cannot remove non-leaf device '%s'" % dev.name)

        dev.removeHook(modparent=modparent)
        if modparent:
            # if this is a partition we need to remove it from the parted.Disk
            if isinstance(dev, PartitionDevice) and dev.disk is not None:
                # adjust all other PartitionDevice instances belonging to the
                # same disk so the device name matches the potentially altered
                # name of the parted.Partition
                for device in self._devices:
                    if isinstance(device, PartitionDevice) and \
                       device.disk == dev.disk:
                        device.updateName()

        self._devices.remove(dev)
        if dev.name in self.names and getattr(dev, "complete", True):
            self.names.remove(dev.name)
        log.info("removed %s %s (id %d) from device tree", dev.type,
                                                           dev.name,
                                                           dev.id)

    def _removeChildrenFromTree(self, device):
        devs_to_remove = self.getDependentDevices(device)
        while devs_to_remove:
            leaves = [d for d in devs_to_remove if d.isleaf]
            for leaf in leaves:
                self._removeDevice(leaf, modparent=False)
                devs_to_remove.remove(leaf)
            if len(devs_to_remove) == 1 and devs_to_remove[0].isExtended:
                self._removeDevice(devs_to_remove[0], force=True, modparent=False)
                break

    def registerAction(self, action):
        """ Register an action to be performed at a later time.

            :param action: the action
            :type action: :class:`~.deviceaction.DeviceAction`

            Modifications to the Device instance are handled before we
            get here.
        """
        if not (action.isCreate and action.isDevice) and \
           action.device not in self._devices:
            raise DeviceTreeError("device is not in the tree")
        elif (action.isCreate and action.isDevice):
            if action.device in self._devices:
                raise DeviceTreeError("device is already in the tree")

        if action.isCreate and action.isDevice:
            self._addDevice(action.device)
        elif action.isDestroy and action.isDevice:
            self._removeDevice(action.device)
        elif action.isCreate and action.isFormat:
            if isinstance(action.device.format, formats.fs.FS) and \
               action.device.format.mountpoint in self.filesystems:
                raise DeviceTreeError("mountpoint already in use")

        # apply the action before adding it in case apply raises an exception
        action.apply()
        log.info("registered action: %s", action)
        self._actions.append(action)

    def cancelAction(self, action):
        """ Cancel a registered action.

            :param action: the action
            :type action: :class:`~.deviceaction.DeviceAction`

            This will unregister the action and do any required
            modifications to the device list.

            Actions all operate on a Device, so we can use the devices
            to determine dependencies.
        """
        if action.isCreate and action.isDevice:
            # remove the device from the tree
            self._removeDevice(action.device)
        elif action.isDestroy and action.isDevice:
            # add the device back into the tree
            self._addDevice(action.device, new=False)

        action.cancel()
        self._actions.remove(action)
        log.info("canceled action %s", action)

    def findActions(self, device=None, action_type=None, object_type=None,
                    path=None, devid=None):
        """ Find all actions that match all specified parameters.

            A value of None for any of the keyword arguments indicates that any
            value is acceptable for that field.

            :keyword device: device to match
            :type device: :class:`~.devices.StorageDevice` or None
            :keyword action_type: action type to match (eg: "create", "destroy")
            :type action_type: str or None
            :keyword object_type: operand type to match (eg: "device" or "format")
            :type object_type: str or None
            :keyword path: device path to match
            :type path: str or None
            :keyword devid: device id to match
            :type devid: int or None
            :returns: a list of matching actions
            :rtype: list of :class:`~.deviceaction.DeviceAction`

        """
        if device is None and action_type is None and object_type is None and \
           path is None and devid is None:
            return self._actions[:]

        # convert the string arguments to the types used in actions
        _type = action_type_from_string(action_type)
        _object = action_object_from_string(object_type)

        actions = []
        for action in self._actions:
            if device is not None and action.device != device:
                continue

            if _type is not None and action.type != _type:
                continue

            if _object is not None and action.obj != _object:
                continue

            if path is not None and action.device.path != path:
                continue

            if devid is not None and action.device.id != devid:
                continue

            actions.append(action)

        return actions

    # ROCKS8: backport from python-blivet-0.61.15.75
    def getDependentDevices(self, dep, hidden=False):
        """ Return a list of devices that depend on dep.

            The list includes both direct and indirect dependents.

            :param dep: the device whose dependents we are looking for
            :type dep: :class:`~.devices.StorageDevice`
            :keyword bool hidden: include hidden devices in search
        """
        dependents = []
        log_method_call(self, dep=dep, hidden=hidden)

        # don't bother looping looking for dependents if this is a leaf device
        # XXX all hidden devices are leaves
        if dep.isleaf and not hidden:
            log.debug("dep is a leaf")
            return dependents

        devices = self._devices[:]
        if hidden:
            devices.extend(self._hidden)

        for device in devices:
            log.debug("checking if %s depends on %s", device.name, dep.name)
            if device.dependsOn(dep):
                dependents.append(device)

        return dependents

#    def getDependentDevices(self, dep):
#        """ Return a list of devices that depend on dep.
#
#            The list includes both direct and indirect dependents.
#
#            :param dep: the device whose dependents we are looking for
#            :type dep: :class:`~.devices.StorageDevice`
#        """
#        dependents = []
#
#        # don't bother looping looking for dependents if this is a leaf device
#        if dep.isleaf:
#            return dependents
#
#        incomplete = [d for d in self._devices
#                            if not getattr(d, "complete", True)]
#        for device in self.devices + incomplete:
#            if device.dependsOn(dep):
#                dependents.append(device)
#
#        return dependents

    def isIgnored(self, info):
        """ Return True if info is a device we should ignore.

            :param info: udevdb device entry
            :type info: dict
            :returns: whether the device will be ignored
            :rtype: bool

        """
        sysfs_path = udev.device_get_sysfs_path(info)
        name = udev.device_get_name(info)
        if not sysfs_path:
            return None

        # Special handling for mdraid external metadata sets (mdraid BIOSRAID):
        # 1) The containers are intermediate devices which will never be
        # in exclusiveDisks
        # 2) Sets get added to exclusive disks with their dmraid set name by
        # the filter ui.  Note that making the ui use md names instead is not
        # possible as the md names are simpy md# and we cannot predict the #
        if udev.device_is_md(info) and \
           udev.device_get_md_level(info) == "container":
            return False

        if udev.device_get_md_container(info) and \
               udev.device_is_md(info) and \
               udev.device_get_md_name(info):
            md_name = udev.device_get_md_name(info)
            # mdadm may have appended _<digit>+ if the current hostname
            # does not match the one in the array metadata
            alt_name = re.sub(r"_\d+$", "", md_name)
            raw_pattern = "isw_[a-z]*_%s"
            # XXX FIXME: This is completely insane.
            for i in range(0, len(self.exclusiveDisks)):
                if re.match(raw_pattern % md_name, self.exclusiveDisks[i]) or \
                   re.match(raw_pattern % alt_name, self.exclusiveDisks[i]):
                    self.exclusiveDisks[i] = name
                    return False

        # never ignore mapped disk images. if you don't want to use them,
        # don't specify them in the first place
        if udev.device_is_dm_anaconda(info) or udev.device_is_dm_livecd(info):
            return False

        # Ignore loop and ram devices, we normally already skip these in
        # udev.py: enumerate_block_devices(), but we can still end up trying
        # to add them to the tree when they are slaves of other devices, this
        # happens for example with the livecd
        if name.startswith("ram"):
            return True

        if name.startswith("loop"):
            # ignore loop devices unless they're backed by a file
            return (not blockdev.loop.get_backing_file(name))

        # FIXME: check for virtual devices whose slaves are on the ignore list

    def udevDeviceIsDisk(self, info):
        """ Return True if the udev device looks like a disk.

            :param info: udevdb device entry
            :type info: dict
            :returns: whether the device is a disk
            :rtype: bool

            We want exclusiveDisks to operate on anything that could be
            considered a directly usable disk, ie: fwraid array, mpath, or disk.

            Unfortunately, since so many things are represented as disks by
            udev/sysfs, we have to define what is a disk in terms of what is
            not a disk.
        """
        return (udev.device_is_disk(info) and
                not (udev.device_is_cdrom(info) or
                     udev.device_is_partition(info) or
                     udev.device_is_dm_partition(info) or
                     udev.device_is_dm_lvm(info) or
                     udev.device_is_dm_crypt(info) or
                     (udev.device_is_md(info) and
                      not udev.device_get_md_container(info))))

    def addUdevLVDevice(self, info):
        name = udev.device_get_name(info)
        log_method_call(self, name=name)
        sysfs_path = udev.device_get_sysfs_path(info)

        vg_name = udev.device_get_lv_vg_name(info)
        device = self.getDeviceByName(vg_name, hidden=True)
        if device and not isinstance(device, LVMVolumeGroupDevice):
            log.warning("found non-vg device with name %s", vg_name)
            device = None

        if not device:
            # initiate detection of all PVs and hope that it leads to us having
            # the VG and LVs in the tree
            for pv_name in os.listdir(sysfs_path + "/slaves"):
                link = os.readlink(sysfs_path + "/slaves/" + pv_name)
                pv_sysfs_path = os.path.normpath(sysfs_path + '/slaves/' + link)
                pv_info = udev.get_device(pv_sysfs_path)
                self.addUdevDevice(pv_info)

        vg_name = udev.device_get_lv_vg_name(info)
        device = self.getDeviceByName(vg_name)
        if not device:
            log.error("failed to find vg '%s' after scanning pvs", vg_name)

        # Don't return the device like we do in the other addUdevFooDevice
        # methods. The device we have here is a vg, not an lv.

    def addUdevDMDevice(self, info):
        name = udev.device_get_name(info)
        log_method_call(self, name=name)
        sysfs_path = udev.device_get_sysfs_path(info)
        device = None

        for dmdev in self.devices:
            if not isinstance(dmdev, DMDevice):
                continue

            try:
                # there is a device in the tree already with the same
                # major/minor as this one but with a different name
                # XXX this is kind of racy
                if dmdev.getDMNode() == os.path.basename(sysfs_path):
                    # XXX should we take the name already in use?
                    device = dmdev
                    break
            except DMError:
                # This is a little lame, but the VG device is a DMDevice
                # and it won't have a dm node. At any rate, this is not
                # important enough to crash the install.
                log.debug("failed to find dm node for %s", dmdev.name)
                continue

        handle_luks = (udev.device_is_dm_luks(info) and
                        (self._cleanup or not flags.installer_mode))
        slave_dev = None
        slave_info = None
        if device is None:
            # we couldn't find it, so create it
            # first, get a list of the slave devs and look them up
            slave_dir = os.path.normpath("%s/slaves" % sysfs_path)
            slave_names = os.listdir(slave_dir)
            for slave_name in slave_names:
                # if it's a dm-X name, resolve it to a map name first
                if slave_name.startswith("dm-"):
                    dev_name = blockdev.dm.name_from_node(slave_name)
                else:
                    dev_name = slave_name.replace("!", "/") # handles cciss
                slave_dev = self.getDeviceByName(dev_name)
                path = os.path.normpath("%s/%s" % (slave_dir, slave_name))
                new_info = udev.get_device(os.path.realpath(path))
                if not slave_dev:
                    # we haven't scanned the slave yet, so do it now
                    if new_info:
                        self.addUdevDevice(new_info)
                        slave_dev = self.getDeviceByName(dev_name)
                        if slave_dev is None:
                            # if the current slave is still not in
                            # the tree, something has gone wrong
                            log.error("failure scanning device %s: could not add slave %s", name, dev_name)
                            return

                if handle_luks:
                    slave_info = new_info

            # try to get the device again now that we've got all the slaves
            device = self.getDeviceByName(name)

            if device is None and udev.device_is_dm_partition(info):
                diskname = udev.device_get_dm_partition_disk(info)
                disk = self.getDeviceByName(diskname)
                return self.addUdevPartitionDevice(info, disk=disk)

            # if this is a luks device whose map name is not what we expect,
            # fix up the map name and see if that sorts us out
            if device is None and handle_luks and slave_info and slave_dev:
                slave_dev.format.mapName = name
                self.handleUdevLUKSFormat(slave_info, slave_dev)

                # try once more to get the device
                device = self.getDeviceByName(name)

            # create a device for the livecd OS image(s)
            if device is None and udev.device_is_dm_livecd(info):
                device = DMDevice(name, dmUuid=info.get('DM_UUID'),
                                  sysfsPath=sysfs_path, exists=True,
                                  parents=[slave_dev])
                device.protected = True
                device.controllable = False
                self._addDevice(device)

            # if we get here, we found all of the slave devices and
            # something must be wrong -- if all of the slaves are in
            # the tree, this device should be as well
            if device is None:
                lvm.lvm_cc_addFilterRejectRegexp(name)
                log.warning("ignoring dm device %s", name)

        return device

    def addUdevMultiPathDevice(self, info):
        name = udev.device_get_name(info)
        log_method_call(self, name=name)
        sysfs_path = udev.device_get_sysfs_path(info)

        slave_devs = []

        # TODO: look for this device by dm-uuid?

        # first, get a list of the slave devs and look them up
        slave_dir = os.path.normpath("%s/slaves" % sysfs_path)
        slave_names = os.listdir(slave_dir)
        for slave_name in slave_names:
            # if it's a dm-X name, resolve it to a map name first
            if slave_name.startswith("dm-"):
                dev_name = blockdev.dm.name_from_node(slave_name)
            else:
                dev_name = slave_name.replace("!", "/") # handles cciss
            slave_dev = self.getDeviceByName(dev_name)
            path = os.path.normpath("%s/%s" % (slave_dir, slave_name))
            new_info = udev.get_device(os.path.realpath(path))
            if not slave_dev:
                # we haven't scanned the slave yet, so do it now
                if new_info:
                    self.addUdevDevice(new_info)
                    slave_dev = self.getDeviceByName(dev_name)
                    if slave_dev is None:
                        # if the current slave is still not in
                        # the tree, something has gone wrong
                        log.error("failure scanning device %s: could not add slave %s", name, dev_name)
                        return

            slave_devs.append(slave_dev)

        device = None
        if slave_devs:
            try:
                serial = info["DM_UUID"].split("-", 1)[1]
            except (IndexError, AttributeError):
                log.error("multipath device %s has no DM_UUID", name)
                raise DeviceTreeError("multipath %s has no DM_UUID" % name)

            device = MultipathDevice(name, parents=slave_devs,
                                     serial=serial)
            self._addDevice(device)

        return device

    def addUdevMDDevice(self, info):
        name = udev.device_get_md_name(info)
        log_method_call(self, name=name)
        sysfs_path = udev.device_get_sysfs_path(info)

        slave_dir = os.path.normpath("%s/slaves" % sysfs_path)
        slave_names = os.listdir(slave_dir)
        for slave_name in slave_names:
            # if it's a dm-X name, resolve it to a map name
            if slave_name.startswith("dm-"):
                dev_name = blockdev.dm.name_from_node(slave_name)
            else:
                dev_name = slave_name
            slave_dev = self.getDeviceByName(dev_name)
            if not slave_dev:
                # we haven't scanned the slave yet, so do it now
                path = os.path.normpath("%s/%s" % (slave_dir, slave_name))
                new_info = udev.get_device(os.path.realpath(path))
                if new_info:
                    self.addUdevDevice(new_info)
                    if self.getDeviceByName(dev_name) is None:
                        # if the current slave is still not in
                        # the tree, something has gone wrong
                        log.error("failure scanning device %s: could not add slave %s", name, dev_name)
                        return

        # try to get the device again now that we've got all the slaves
        device = self.getDeviceByName(name, incomplete=flags.allow_imperfect_devices)

        if device is None:
            try:
                uuid = udev.device_get_md_uuid(info)
            except KeyError:
                log.warning("failed to obtain uuid for mdraid device")
            else:
                device = self.getDeviceByUuid(uuid, incomplete=flags.allow_imperfect_devices)

        if device:
            # update the device instance with the real name in case we had to
            # look it up by something other than name
            # XXX bypass the setter since a) the device exists and b) the name
            #     must be valid since it is currently in use
            device._name = name
        else:
            # if we get here, we found all of the slave devices and
            # something must be wrong -- if all of the slaves are in
            # the tree, this device should be as well
            if name is None:
                name = udev.device_get_name(info)
                path = "/dev/" + name
            else:
                path = "/dev/md/" + name

            log.error("failed to scan md array %s", name)
            try:
                blockdev.md.deactivate(path)
            except blockdev.MDRaidError:
                log.error("failed to stop broken md array %s", name)

        return device

    def addUdevPartitionDevice(self, info, disk=None):
        name = udev.device_get_name(info)
        log_method_call(self, name=name)
        sysfs_path = udev.device_get_sysfs_path(info)

        if name.startswith("md"):
            name = blockdev.md.name_from_node(name)
            device = self.getDeviceByName(name)
            if device:
                return device

        if disk is None:
            disk_name = os.path.basename(os.path.dirname(sysfs_path))
            disk_name = disk_name.replace('!','/')
            if disk_name.startswith("md"):
                disk_name = blockdev.md.name_from_node(disk_name)

            disk = self.getDeviceByName(disk_name)

        if disk is None:
            # create a device instance for the disk
            new_info = udev.get_device(os.path.dirname(sysfs_path))
            if new_info:
                self.addUdevDevice(new_info)
                disk = self.getDeviceByName(disk_name)

            if disk is None:
                # if the current device is still not in
                # the tree, something has gone wrong
                log.error("failure scanning device %s", disk_name)
                lvm.lvm_cc_addFilterRejectRegexp(name)
                return

        if not disk.partitioned:
            # Ignore partitions on:
            #  - devices we do not support partitioning of, like logical volumes
            #  - devices that do not have a usable disklabel
            #  - devices that contain disklabels made by isohybrid
            #
            if disk.partitionable and \
               disk.format.type != "iso9660" and \
               not disk.format.hidden and \
               not self._isIgnoredDisk(disk):
                if info.get("ID_PART_TABLE_TYPE") == "gpt":
                    msg = "corrupt gpt disklabel on disk %s" % disk.name
                else:
                    msg = "failed to scan disk %s" % disk.name

                raise UnusableConfigurationError(msg)

            # there's no need to filter partitions on members of multipaths or
            # fwraid members from lvm since multipath and dmraid are already
            # active and lvm should therefore know to ignore them
            if not disk.format.hidden:
                lvm.lvm_cc_addFilterRejectRegexp(name)

            log.debug("ignoring partition %s on %s", name, disk.format.type)
            return

        device = None
        try:
            device = PartitionDevice(name, sysfsPath=sysfs_path,
                                     major=udev.device_get_major(info),
                                     minor=udev.device_get_minor(info),
                                     exists=True, parents=[disk])
        except DeviceError as e:
            # corner case sometime the kernel accepts a partition table
            # which gets rejected by parted, in this case we will
            # prompt to re-initialize the disk, so simply skip the
            # faulty partitions.
            # XXX not sure about this
            log.error("Failed to instantiate PartitionDevice: %s", e)
            return

        self._addDevice(device)
        return device

    def addUdevDiskDevice(self, info):
        name = udev.device_get_name(info)
        log_method_call(self, name=name)
        sysfs_path = udev.device_get_sysfs_path(info)
        serial = udev.device_get_serial(info)
        bus = udev.device_get_bus(info)

        # udev doesn't always provide a vendor.
        vendor = udev.device_get_vendor(info)
        if not vendor:
            vendor = ""

        kwargs = { "serial": serial, "vendor": vendor, "bus": bus }
        if udev.device_is_iscsi(info):
            diskType = iScsiDiskDevice
            initiator = udev.device_get_iscsi_initiator(info)
            target = udev.device_get_iscsi_name(info)
            address = udev.device_get_iscsi_address(info)
            port = udev.device_get_iscsi_port(info)
            nic = udev.device_get_iscsi_nic(info)
            kwargs["initiator"] = initiator
            if initiator == self.iscsi.initiator:
                node = self.iscsi.getNode(target, address, port, nic)
                kwargs["node"] = node
                kwargs["ibft"] = node in self.iscsi.ibftNodes
                kwargs["nic"] = self.iscsi.ifaces.get(node.iface, node.iface)
                log.info("%s is an iscsi disk", name)
            else:
                # qla4xxx partial offload
                kwargs["node"] = None
                kwargs["ibft"] = False
                kwargs["nic"] = "offload:not_accessible_via_iscsiadm"
                kwargs["fw_address"] = address
                kwargs["fw_port"] = port
                kwargs["fw_name"] = name
        elif udev.device_is_fcoe(info):
            diskType = FcoeDiskDevice
            kwargs["nic"]        = udev.device_get_fcoe_nic(info)
            kwargs["identifier"] = udev.device_get_fcoe_identifier(info)
            log.info("%s is an fcoe disk", name)
        elif udev.device_get_md_container(info):
            name = udev.device_get_md_name(info)
            diskType = MDBiosRaidArrayDevice
            parentPath = udev.device_get_md_container(info)
            parentName = devicePathToName(parentPath)
            container = self.getDeviceByName(parentName)
            if not container:
                parentSysName = blockdev.md.node_from_name(parentName)
                container_sysfs = "/sys/class/block/" + parentSysName
                container_info = udev.get_device(container_sysfs)
                if not container_info:
                    log.error("failed to find md container %s at %s",
                                parentName, container_sysfs)
                    return

                self.addUdevDevice(container_info)
                container = self.getDeviceByName(parentName)
                if not container:
                    log.error("failed to scan md container %s", parentName)
                    return

            kwargs["parents"] = [container]
            kwargs["level"]  = udev.device_get_md_level(info)
            kwargs["memberDevices"] = udev.device_get_md_devices(info)
            kwargs["uuid"] = udev.device_get_md_uuid(info)
            kwargs["exists"]  = True
            del kwargs["serial"]
            del kwargs["vendor"]
            del kwargs["bus"]
        elif udev.device_is_dasd(info):
            diskType = DASDDevice
            kwargs["busid"] = udev.device_get_dasd_bus_id(info)
            kwargs["opts"] = {}

            for attr in ['readonly', 'use_diag', 'erplog', 'failfast']:
                kwargs["opts"][attr] = udev.device_get_dasd_flag(info, attr)

            log.info("%s is a dasd device", name)
        elif udev.device_is_zfcp(info):
            diskType = ZFCPDiskDevice

            for attr in ['hba_id', 'wwpn', 'fcp_lun']:
                kwargs[attr] = udev.device_get_zfcp_attribute(info, attr=attr)

            log.info("%s is a zfcp device", name)
        # ROCKS8: from python-blivet-0.61.15.75
        elif udev.device_is_nvdimm_namespace(info):
            diskType = NVDIMMNamespaceDevice

            from .nvdimm import nvdimm
            ninfo = nvdimm.get_namespace_info(info.get("DEVNAME"))

            kwargs["mode"] = blockdev.nvdimm_namespace_get_mode_str(ninfo.mode)
            kwargs["devname"] = ninfo.dev
            kwargs["uuid"] = ninfo.uuid
            kwargs["sectorSize"] = ninfo.sector_size

            log.info("%s is an NVDIMM namespace device", udev.device_get_name(info))
        else:
            diskType = DiskDevice
            log.info("%s is a disk", name)

        device = diskType(name,
                          major=udev.device_get_major(info),
                          minor=udev.device_get_minor(info),
                          sysfsPath=sysfs_path, **kwargs)

        if diskType == DASDDevice:
            self.dasd.append(device)

        self._addDevice(device)
        return device

    def addUdevOpticalDevice(self, info):
        log_method_call(self)
        # XXX should this be RemovableDevice instead?
        #
        # Looks like if it has ID_INSTANCE=0:1 we can ignore it.
        device = OpticalDevice(udev.device_get_name(info),
                               major=udev.device_get_major(info),
                               minor=udev.device_get_minor(info),
                               sysfsPath=udev.device_get_sysfs_path(info),
                               vendor=udev.device_get_vendor(info),
                               model=udev.device_get_model(info))
        self._addDevice(device)
        return device

    def addUdevLoopDevice(self, info):
        name = udev.device_get_name(info)
        log_method_call(self, name=name)
        sysfs_path = udev.device_get_sysfs_path(info)
        sys_file = "%s/loop/backing_file" % sysfs_path
        backing_file = open(sys_file).read().strip()
        file_device = self.getDeviceByName(backing_file)
        if not file_device:
            file_device = FileDevice(backing_file, exists=True)
            self._addDevice(file_device)
        device = LoopDevice(name,
                            parents=[file_device],
                            sysfsPath=sysfs_path,
                            exists=True)
        if not self._cleanup or file_device not in self.diskImages.values():
            # don't allow manipulation of loop devices other than those
            # associated with disk images, and then only during cleanup
            file_device.controllable = False
            device.controllable = False
        self._addDevice(device)
        return device

    def addUdevDevice(self, info):
        name = udev.device_get_name(info)
        log_method_call(self, name=name, info=pprint.pformat(dict(info)))
        uuid = udev.device_get_uuid(info)
        sysfs_path = udev.device_get_sysfs_path(info)

        # make sure this device was not scheduled for removal and also has not
        # been hidden
        removed = [a.device for a in self.findActions(action_type="destroy", object_type="device")]
        for ignored in removed + self._hidden:
            if (sysfs_path and ignored.sysfsPath == sysfs_path) or \
               (uuid and uuid in (ignored.uuid, ignored.format.uuid)):
                if ignored in removed:
                    reason = "removed"
                else:
                    reason = "hidden"

                log.debug("skipping %s device %s", reason, name)
                return

        # make sure we note the name of every device we see
        if name not in self.names:
            self.names.append(name)

        if self.isIgnored(info):
            log.info("ignoring %s (%s)", name, sysfs_path)
            if name not in self.ignoredDisks:
                self.addIgnoredDisk(name)

            return

        log.info("scanning %s (%s)...", name, sysfs_path)
        device = self.getDeviceByName(name)
        if device is None and udev.device_is_md(info):

            # If the md name is None, then some udev info is missing. Likely,
            # this is because the array is degraded, and mdadm has deactivated
            # it. Try to activate it and re-get the udev info.
            if flags.allow_imperfect_devices and udev.device_get_md_name(info) is None:
                devname = udev.device_get_devname(info)
                if devname:
                    try:
                        blockdev.md.run(devname)
                    except blockdev.MDRaidError as e:
                        log.warning("Failed to start possibly degraded md array: %s", e)
                    else:
                        udev.settle()
                        info = udev.get_device(sysfs_path)
                else:
                    log.warning("Failed to get devname for possibly degraded md array.")

            md_name = udev.device_get_md_name(info)
            if md_name is None:
                log.warning("No name for possibly degraded md array.")
            else:
                device = self.getDeviceByName(md_name, incomplete=flags.allow_imperfect_devices)

            if device and not isinstance(device, MDRaidArrayDevice):
                log.warning("Found device %s, but it turns out not be an md array device after all.", device.name)
                device = None

        if device and device.isDisk and \
           blockdev.mpath.is_mpath_member(device.path):
            # newly added device (eg iSCSI) could make this one a multipath member
            if device.format and device.format.type != "multipath_member":
                log.debug("%s newly detected as multipath member, dropping old format and removing kids", device.name)
                # remove children from tree so that we don't stumble upon them later
                self._removeChildrenFromTree(device)
                device.format = formats.DeviceFormat()

        #
        # The first step is to either look up or create the device
        #
        device_added = True
        if device:
            # we successfully looked up the device. skip to format handling.
            # first, grab the parted.Device while it's active
            _unused = device.partedDevice
            device_added = False
        elif udev.device_is_loop(info):
            log.info("%s is a loop device", name)
            device = self.addUdevLoopDevice(info)
        elif udev.device_is_dm_mpath(info) and \
             not udev.device_is_dm_partition(info):
            log.info("%s is a multipath device", name)
            device = self.addUdevMultiPathDevice(info)
        elif udev.device_is_dm_lvm(info):
            log.info("%s is an lvm logical volume", name)
            device = self.addUdevLVDevice(info)
        elif udev.device_is_dm(info):
            log.info("%s is a device-mapper device", name)
            device = self.addUdevDMDevice(info)
        elif udev.device_is_md(info) and not udev.device_get_md_container(info):
            log.info("%s is an md device", name)
            device = self.addUdevMDDevice(info)
        elif udev.device_is_cdrom(info):
            log.info("%s is a cdrom", name)
            device = self.addUdevOpticalDevice(info)
        elif udev.device_is_biosraid_member(info) and udev.device_is_disk(info):
            log.info("%s is part of a biosraid", name)
            device = DiskDevice(name,
                            major=udev.device_get_major(info),
                            minor=udev.device_get_minor(info),
                            sysfsPath=sysfs_path, exists=True)
            self._addDevice(device)
        elif udev.device_is_disk(info):
            device = self.addUdevDiskDevice(info)
        elif udev.device_is_partition(info):
            log.info("%s is a partition", name)
            device = self.addUdevPartitionDevice(info)
        else:
            log.error("Unknown block device type for: %s", name)
            return

        if not device:
            log.debug("no device obtained for %s", name)
            return

        # If this device is read-only, mark it as such now.
        if self.udevDeviceIsDisk(info) and \
                util.get_sysfs_attr(udev.device_get_sysfs_path(info), 'ro') == '1':
            device.readonly = True

        # If this device is protected, mark it as such now. Once the tree
        # has been populated, devices' protected attribute is how we will
        # identify protected devices.
        if device.name in self.protectedDevNames:
            device.protected = True
            # if this is the live backing device we want to mark its parents
            # as protected also
            if device.name == self.liveBackingDevice:
                for parent in device.parents:
                    parent.protected = True

        # If we just added a multipath or fwraid disk that is in exclusiveDisks
        # we have to make sure all of its members are in the list too.
        mdclasses = (DMRaidArrayDevice, MDRaidArrayDevice, MultipathDevice)
        if device.isDisk and isinstance(device, mdclasses):
            if device.name in self.exclusiveDisks:
                for parent in device.parents:
                    if parent.name not in self.exclusiveDisks:
                        self.exclusiveDisks.append(parent.name)

        log.info("got device: %r", device)

        # now handle the device's formatting
        self.handleUdevDeviceFormat(info, device)
        if device_added:
            device.originalFormat = copy.copy(device.format)
        device.deviceLinks = udev.device_get_symlinks(info)

    def handleUdevDiskLabelFormat(self, info, device):
        disklabel_type = udev.device_get_disklabel_type(info)
        log_method_call(self, device=device.name, label_type=disklabel_type)
        # if there is no disklabel on the device
        # blkid doesn't understand dasd disklabels, so bypass for dasd
        if disklabel_type is None and not \
           (device.isDisk and udev.device_is_dasd(info)):
            log.debug("device %s does not contain a disklabel", device.name)
            return

        if device.partitioned:
            # this device is already set up
            log.debug("disklabel format on %s already set up", device.name)
            return

        try:
            device.setup()
        except Exception: # pylint: disable=broad-except
            log_exception_info(log.warning, "setup of %s failed, aborting disklabel handler", [device.name])
            return

        # special handling for unsupported partitioned devices
        if not device.partitionable:
            try:
                fmt = getFormat("disklabel", device=device.path, labelType=disklabel_type, exists=True)
            except InvalidDiskLabelError:
                log.warning("disklabel detected but not usable on %s",
                            device.name)
            else:
                device.format = fmt
            return

        # we're going to pass the "best" disklabel type into the DiskLabel
        # constructor, but it only has meaning for non-existent disklabels.
        labelType = platform.bestDiskLabelType(device)

        try:
            fmt = getFormat("disklabel", device=device.path, labelType=labelType, exists=True)
        except InvalidDiskLabelError as e:
            log.info("no usable disklabel on %s", device.name)
            if disklabel_type == "gpt":
                log.debug(e)
                device.format = getFormat(_("Invalid Disk Label"))
        else:
            device.format = fmt

    def handleUdevLUKSFormat(self, info, device):
        # pylint: disable=unused-argument
        log_method_call(self, name=device.name, type=device.format.type)
        if not device.format.uuid:
            log.info("luks device %s has no uuid", device.path)
            return

        # look up or create the mapped device
        if not self.getDeviceByName(device.format.mapName):
            passphrase = self.__luksDevs.get(device.format.uuid)
            if device.format.configured:
                pass
            elif passphrase:
                device.format.passphrase = passphrase
            elif device.format.uuid in self.__luksDevs:
                log.info("skipping previously-skipped luks device %s",
                            device.name)
            elif self._cleanup or flags.testing:
                # if we're only building the devicetree so that we can
                # tear down all of the devices we don't need a passphrase
                if device.format.status:
                    # this makes device.configured return True
                    device.format.passphrase = 'yabbadabbadoo'
            else:
                # Try each known passphrase. Include luksDevs values in case a
                # passphrase has been set for a specific device without a full
                # reset/populate, in which case the new passphrase would not be
                # in self.__passphrases.
                for passphrase in self.__passphrases + list(self.__luksDevs.values()):
                    device.format.passphrase = passphrase
                    try:
                        device.format.setup()
                    except blockdev.BlockDevError:
                        device.format.passphrase = None
                    else:
                        break

            luks_device = LUKSDevice(device.format.mapName,
                                     parents=[device],
                                     exists=True)
            try:
                luks_device.setup()
            except (LUKSError, blockdev.CryptoError, DeviceError) as e:
                log.info("setup of %s failed: %s", device.format.mapName, e)
                device.removeChild()
            else:
                luks_device.updateSysfsPath()
                self._addDevice(luks_device)
        else:
            log.warning("luks device %s already in the tree",
                        device.format.mapName)

    def handleVgLvs(self, vg_device):
        """ Handle setup of the LV's in the vg_device. """
        vg_name = vg_device.name
        lv_info = dict((k, v) for (k, v) in iter(self.lvInfo.items())
                                if v.vg_name == vg_name)

        self.names.extend(n for n in lv_info.keys() if n not in self.names)

        if not vg_device.complete:
            log.warning("Skipping LVs for incomplete VG %s", vg_name)
            return

        if not lv_info:
            log.debug("no LVs listed for VG %s", vg_name)
            return

        def addRequiredLV(name, msg):
            """ Add a prerequisite/parent LV.

                The parent is strictly required in order to be able to add
                some other LV that depends on it. For this reason, failure to
                add the specified LV results in a DeviceTreeError with the
                message string specified in the msg parameter.

                :param str name: the full name of the LV (including vgname)
                :param str msg: message to pass DeviceTreeError ctor on error
                :returns: None
                :raises: :class:`~.errors.DeviceTreeError` on failure

            """
            vol = self.getDeviceByName(name)
            if vol is None:
                addLV(lv_info[name])
                vol = self.getDeviceByName(name)

                if vol is None:
                    log.error("%s: %s", msg, name)
                    raise DeviceTreeError(msg)

        def addLV(lv):
            """ Instantiate and add an LV based on data from the VG. """
            lv_name = lv.lv_name
            lv_uuid = lv.uuid
            lv_attr = lv.attr
            lv_size = Size(lv.size)
            lv_type = lv.segtype

            lv_class = LVMLogicalVolumeDevice
            lv_parents = [vg_device]
            lv_kwargs = {}
            name = "%s-%s" % (vg_name, lv_name)

            if self.getDeviceByName(name):
                # some lvs may have been added on demand below
                log.debug("already added %s", name)
                return

            if lv_attr[0] in 'Ss':
                log.info("found lvm snapshot volume '%s'", name)
                origin_name = blockdev.lvm.lvorigin(vg_name, lv_name)
                if not origin_name:
                    log.error("lvm snapshot '%s-%s' has unknown origin",
                                vg_name, lv_name)
                    return

                if origin_name.endswith("_vorigin]"):
                    lv_kwargs["vorigin"] = True
                    origin = None
                else:
                    origin_device_name = "%s-%s" % (vg_name, origin_name)
                    addRequiredLV(origin_device_name,
                                  "failed to locate origin lv")
                    origin = self.getDeviceByName(origin_device_name)

                lv_kwargs["origin"] = origin
                lv_class = LVMSnapShotDevice
            elif lv_attr[0] == 'v':
                # skip vorigins
                return
            elif lv_attr[0] in 'Ii':
                # mirror image
                rname = re.sub(r'_[rm]image.+', '', lv_name[1:-1])
                name = "%s-%s" % (vg_name, rname)
                addRequiredLV(name, "failed to look up raid lv")
                raid_items[name]["copies"] += 1
                return
            elif lv_attr[0] == 'e':
                if lv_name.endswith("_pmspare]"):
                    # spare metadata area for any thin pool that needs repair
                    return

                # raid metadata volume
                lv_name = re.sub(r'_[tr]meta.*', '', lv_name[1:-1])
                name = "%s-%s" % (vg_name, lv_name)
                addRequiredLV(name, "failed to look up raid lv")
                raid_items[name]["meta"] += lv_size
                return
            elif lv_attr[0] == 'l':
                # log volume
                rname = re.sub(r'_mlog.*', '', lv_name[1:-1])
                name = "%s-%s" % (vg_name, rname)
                addRequiredLV(name, "failed to look up log lv")
                raid_items[name]["log"] = lv_size
                return
            elif lv_attr[0] == 't':
                # thin pool
                lv_class = LVMThinPoolDevice
            elif lv_attr[0] == 'V':
                # thin volume
                pool_name = blockdev.lvm.thlvpoolname(vg_name, lv_name)
                pool_device_name = "%s-%s" % (vg_name, pool_name)
                addRequiredLV(pool_device_name, "failed to look up thin pool")

                origin_name = blockdev.lvm.lvorigin(vg_name, lv_name)
                if origin_name:
                    origin_device_name = "%s-%s" % (vg_name, origin_name)
                    addRequiredLV(origin_device_name, "failed to locate origin lv")
                    origin = self.getDeviceByName(origin_device_name)
                    lv_kwargs["origin"] = origin
                    lv_class = LVMThinSnapShotDevice
                else:
                    lv_class = LVMThinLogicalVolumeDevice

                lv_parents = [self.getDeviceByName(pool_device_name)]
            elif lv_name.endswith(']'):
                # Internal LVM2 device
                return
            elif lv_attr[0] not in '-mMrRoO':
                # Ignore anything else except for the following:
                #   - normal lv
                #   m mirrored
                #   M mirrored without initial sync
                #   r raid
                #   R raid without initial sync
                #   o origin
                #   O origin with merging snapshot
                return

            lv_dev = self.getDeviceByUuid(lv_uuid)
            if lv_dev is None:
                lv_device = lv_class(lv_name, parents=lv_parents,
                                     uuid=lv_uuid, size=lv_size,segType=lv_type,
                                     exists=True, **lv_kwargs)
                self._addDevice(lv_device)
                if flags.installer_mode:
                    lv_device.setup()

                if lv_device.status:
                    lv_device.updateSysfsPath()
                    lv_info = udev.get_device(lv_device.sysfsPath)
                    if not lv_info:
                        log.error("failed to get udev data for lv %s", lv_device.name)
                        return

                    # do format handling now
                    self.addUdevDevice(lv_info)

        raid_items = dict((n.replace("[", "").replace("]", ""),
                     {"copies": 0, "log": Size(0), "meta": Size(0)})
                     for n in lv_info.keys())
        for lv in lv_info.values():
            addLV(lv)

        for name, data in raid_items.items():
            lv_dev = self.getDeviceByName(name)
            if not lv_dev:
                # hidden lv, eg: pool00_tdata
                continue

            lv_dev.copies = data["copies"] or 1
            lv_dev.metaDataSize = data["meta"]
            lv_dev.logSize = data["log"]
            log.debug("set %s copies to %d, metadata size to %s, log size "
                      "to %s, total size %s",
                        lv_dev.name, lv_dev.copies, lv_dev.metaDataSize,
                        lv_dev.logSize, lv_dev.vgSpaceUsed)

    def handleUdevLVMPVFormat(self, info, device):
        # pylint: disable=unused-argument
        log_method_call(self, name=device.name, type=device.format.type)
        pv_info = self.pvInfo.get(device.path, None)
        if pv_info:
            vg_name = pv_info.vg_name
            vg_uuid = pv_info.vg_uuid
        else:
            # no info about the PV -> we're done
            return

        if not vg_name:
            log.info("lvm pv %s has no vg", device.name)
            return

        vg_device = self.getDeviceByUuid(vg_uuid, incomplete=True)
        if vg_device:
            vg_device.parents.append(device)
        else:
            same_name = self.getDeviceByName(vg_name)
            if isinstance(same_name, LVMVolumeGroupDevice) and \
               not (all(self._isIgnoredDisk(d) for d in same_name.disks) or
                    all(self._isIgnoredDisk(d) for d in device.disks)):
                raise UnusableConfigurationError("multiple LVM volume groups with the same name")

            try:
                vg_size = Size(pv_info.vg_size)
                vg_free = Size(pv_info.vg_free)
                pe_size = Size(pv_info.vg_extent_size)
                pe_count = pv_info.vg_extent_count
                pe_free = pv_info.vg_free_count
                pv_count = pv_info.vg_pv_count
            except (KeyError, ValueError) as e:
                log.warning("invalid data for %s: %s", device.name, e)
                return

            vg_device = LVMVolumeGroupDevice(vg_name,
                                             parents=[device],
                                             uuid=vg_uuid,
                                             size=vg_size,
                                             free=vg_free,
                                             peSize=pe_size,
                                             peCount=pe_count,
                                             peFree=pe_free,
                                             pvCount=pv_count,
                                             exists=True)
            self._addDevice(vg_device)

        self.handleVgLvs(vg_device)

    def handleUdevMDMemberFormat(self, info, device):
        # pylint: disable=unused-argument
        log_method_call(self, name=device.name, type=device.format.type)
        md_info = blockdev.md.examine(device.path)

        # Use mdadm info if udev info is missing
        md_uuid = md_info.uuid
        device.format.mdUuid = device.format.mdUuid or md_uuid
        md_array = self.getDeviceByUuid(device.format.mdUuid, incomplete=True)

        if md_array:
            md_array.parents.append(device)
        else:
            # create the array with just this one member
            # level is reported as, eg: "raid1"
            md_level = md_info.level
            md_devices = md_info.num_devices

            if md_level is None:
                log.warning("invalid data for %s: no RAID level", device.name)
                return

            # md_examine yields metadata (MD_METADATA) only for metadata version > 0.90
            # if MD_METADATA is missing, assume metadata version is 0.90
            md_metadata = md_info.metadata or "0.90"
            md_name = None
            md_path = md_info.device or ""
            if md_path:
                md_name = devicePathToName(md_path)
                if re.match(r'md\d+$', md_name):
                    # md0 -> 0
                    md_name = md_name[2:]

                if md_name:
                    array = self.getDeviceByName(md_name, incomplete=True)
                    if array and array.uuid != md_uuid:
                        log.error("found multiple devices with the name %s", md_name)

            if md_name:
                log.info("using name %s for md array containing member %s",
                         md_name, device.name)
            else:
                log.error("failed to determine name for the md array %s", (md_uuid or "unknown"))
                return

            array_type = MDRaidArrayDevice
            try:
                if raid.getRaidLevel(md_level) is raid.Container and \
                   getattr(device.format, "biosraid", False):
                    array_type = MDContainerDevice
            except raid.RaidError as e:
                log.error("failed to create md array: %s", e)
                return

            try:
                md_array = array_type(
                   md_name,
                   level=md_level,
                   memberDevices=md_devices,
                   uuid=md_uuid,
                   metadataVersion=md_metadata,
                   exists=True
                )
            except (ValueError, DeviceError) as e:
                log.error("failed to create md array: %s", e)
                return

            md_array.updateSysfsPath()
            md_array.parents.append(device)
            self._addDevice(md_array)

    def handleUdevDMRaidMemberFormat(self, info, device):
        # if dmraid usage is disabled skip any dmraid set activation
        if not flags.dmraid:
            return

        log_method_call(self, name=device.name, type=device.format.type)
        name = udev.device_get_name(info)
        uuid = udev.device_get_uuid(info)
        major = udev.device_get_major(info)
        minor = udev.device_get_minor(info)

        # Have we already created the DMRaidArrayDevice?
        rs_names = blockdev.dm.get_member_raid_sets(uuid, name, major, minor)
        if len(rs_names) == 0:
            log.warning("dmraid member %s does not appear to belong to any "
                        "array", device.name)
            return

        for rs_name in rs_names:
            dm_array = self.getDeviceByName(rs_name, incomplete=True)
            if dm_array is not None:
                # We add the new device.
                dm_array.parents.append(device)
            else:
                # Activate the Raid set.
                blockdev.dm.activate_raid_set(rs_name)
                dm_array = DMRaidArrayDevice(rs_name,
                                             parents=[device])

                self._addDevice(dm_array)

                # Wait for udev to scan the just created nodes, to avoid a race
                # with the udev.get_device() call below.
                udev.settle()

                # Get the DMRaidArrayDevice a DiskLabel format *now*, in case
                # its partitions get scanned before it does.
                dm_array.updateSysfsPath()
                dm_array_info = udev.get_device(dm_array.sysfsPath)
                self.handleUdevDiskLabelFormat(dm_array_info, dm_array)

                # Use the rs's object on the device.
                # pyblock can return the memebers of a set and the
                # device has the attribute to hold it.  But ATM we
                # are not really using it. Commenting this out until
                # we really need it.
                #device.format.raidmem = block.getMemFromRaidSet(dm_array,
                #        major=major, minor=minor, uuid=uuid, name=name)

    def handleBTRFSFormat(self, info, device):
        log_method_call(self, name=device.name)
        uuid = udev.device_get_uuid(info)

        btrfs_dev = None
        for d in self.devices:
            if isinstance(d, BTRFSVolumeDevice) and d.uuid == uuid:
                btrfs_dev = d
                break

        if btrfs_dev:
            log.info("found btrfs volume %s", btrfs_dev.name)
            btrfs_dev.parents.append(device)
        else:
            label = udev.device_get_label(info)
            log.info("creating btrfs volume btrfs.%s", label)
            btrfs_dev = BTRFSVolumeDevice(label, parents=[device], uuid=uuid,
                                          exists=True)
            self._addDevice(btrfs_dev)

        if not btrfs_dev.subvolumes:
            snapshots = btrfs_dev.listSubVolumes(snapshotsOnly=True)
            snapshot_ids = [s.id for s in snapshots]
            for subvol_dict in btrfs_dev.listSubVolumes():
                vol_id = subvol_dict.id
                vol_path = subvol_dict.path
                parent_id = subvol_dict.parent_id
                if vol_path in [sv.name for sv in btrfs_dev.subvolumes]:
                    continue

                # look up the parent subvol
                parent = None
                subvols = [btrfs_dev] + btrfs_dev.subvolumes
                for sv in subvols:
                    if sv.vol_id == parent_id:
                        parent = sv
                        break

                if parent is None:
                    log.error("failed to find parent (%d) for subvol %s",
                              parent_id, vol_path)
                    raise DeviceTreeError("could not find parent for subvol")

                fmt = getFormat("btrfs", device=btrfs_dev.path, exists=True,
                                volUUID=btrfs_dev.format.volUUID,
                                mountopts="subvol=%s" % vol_path)
                if vol_id in snapshot_ids:
                    device_class = BTRFSSnapShotDevice
                else:
                    device_class = BTRFSSubVolumeDevice

                subvol = device_class(vol_path,
                                      vol_id=vol_id,
                                      fmt=fmt,
                                      parents=[parent],
                                      exists=True)
                self._addDevice(subvol)

    def handleUdevDeviceFormat(self, info, device):
        log_method_call(self, name=getattr(device, "name", None))

        if not info:
            log.debug("no information for device %s", device.name)
            return
        if not device.mediaPresent:
            log.debug("no media present for device %s", device.name)
            return

        name = udev.device_get_name(info)
        uuid = udev.device_get_uuid(info)
        label = udev.device_get_label(info)
        format_type = udev.device_get_format(info)
        serial = udev.device_get_serial(info)

        is_multipath_member = blockdev.mpath.is_mpath_member(device.path)
        if is_multipath_member:
            format_type = "multipath_member"

        # Now, if the device is a disk, see if there is a usable disklabel.
        # If not, see if the user would like to create one.
        # XXX ignore disklabels on multipath or biosraid member disks
        if not udev.device_is_biosraid_member(info) and \
           not is_multipath_member and \
           format_type != "iso9660":
            self.handleUdevDiskLabelFormat(info, device)
            if device.partitioned or self.isIgnored(info) or \
               (not device.partitionable and
                device.format.type == "disklabel"):
                # If the device has a disklabel, or the user chose not to
                # create one, we are finished with this device. Otherwise
                # it must have some non-disklabel formatting, in which case
                # we fall through to handle that.
                return

        if (not device) or (not format_type) or device.format.type:
            # this device has no formatting or it has already been set up
            # FIXME: this probably needs something special for disklabels
            log.debug("no type or existing type for %s, bailing", name)
            return

        # set up the common arguments for the format constructor
        format_designator = format_type
        kwargs = {"uuid": uuid,
                  "label": label,
                  "device": device.path,
                  "serial": serial,
                  "exists": True}

        # set up type-specific arguments for the format constructor
        if format_type == "crypto_LUKS":
            # luks/dmcrypt
            kwargs["name"] = "luks-%s" % uuid
        elif format_type in formats.mdraid.MDRaidMember._udevTypes:
            # mdraid
            try:
                # ID_FS_UUID contains the array UUID
                kwargs["mdUuid"] = udev.device_get_uuid(info)
            except KeyError:
                log.warning("mdraid member %s has no md uuid", name)

            # reset the uuid to the member-specific value
            # this will be None for members of v0 metadata arrays
            kwargs["uuid"] = udev.device_get_md_device_uuid(info)

            kwargs["biosraid"] = udev.device_is_biosraid_member(info)
        elif format_type == "LVM2_member":
            # lvm
            pv_info = self.pvInfo.get(device.path, None)
            if pv_info:
                if pv_info.vg_name:
                    kwargs["vgName"] = pv_info.vg_name
                else:
                    log.warning("PV %s has no vg_name", name)
                if pv_info.vg_uuid:
                    kwargs["vgUuid"] = pv_info.vg_uuid
                else:
                    log.warning("PV %s has no vg_uuid", name)
                if pv_info.pe_start:
                    kwargs["peStart"] = Size(pv_info.pe_start)
                else:
                    log.warning("PV %s has no pe_start", name)
        elif format_type == "vfat":
            # efi magic
            if isinstance(device, PartitionDevice) and device.bootable:
                efi = formats.getFormat("efi")
                if efi.minSize <= device.size <= efi.maxSize:
                    format_designator = "efi"
        elif format_type == "hfsplus":
            if isinstance(device, PartitionDevice):
                macefi = formats.getFormat("macefi")
                if macefi.minSize <= device.size <= macefi.maxSize and \
                   device.partedPartition.name == macefi.name:
                    format_designator = "macefi"
        elif format_type == "hfs":
            # apple bootstrap magic
            if isinstance(device, PartitionDevice) and device.bootable:
                apple = formats.getFormat("appleboot")
                if apple.minSize <= device.size <= apple.maxSize:
                    format_designator = "appleboot"
        elif format_type == "btrfs":
            # the format's uuid attr will contain the UUID_SUB, while the
            # overarching volume UUID will be stored as volUUID
            kwargs["uuid"] = info["ID_FS_UUID_SUB"]
            kwargs["volUUID"] = uuid

        try:
            log.info("type detected on '%s' is '%s'", name, format_designator)
            device.format = formats.getFormat(format_designator, **kwargs)
            if device.format.type:
                log.info("got format: %s", device.format)
        except FSError:
            log.warning("type '%s' on '%s' invalid, assuming no format",
                      format_designator, name)
            device.format = formats.DeviceFormat()
            return

        #
        # now do any special handling required for the device's format
        #
        if device.format.type == "luks":
            self.handleUdevLUKSFormat(info, device)
        elif device.format.type == "mdmember":
            self.handleUdevMDMemberFormat(info, device)
        elif device.format.type == "dmraidmember":
            self.handleUdevDMRaidMemberFormat(info, device)
        elif device.format.type == "lvmpv":
            self.handleUdevLVMPVFormat(info, device)
        elif device.format.type == "btrfs":
            self.handleBTRFSFormat(info, device)

    def updateDeviceFormat(self, device):
        log.info("updating format of device: %s", device)
        try:
            util.notify_kernel(device.sysfsPath)
        except (ValueError, IOError) as e:
            log.warning("failed to notify kernel of change: %s", e)

        udev.settle()
        info = udev.get_device(device.sysfsPath)

        self.handleUdevDeviceFormat(info, device)

    def _handleInconsistencies(self):
        for vg in [d for d in self.devices if d.type == "lvmvg"]:
            if vg.complete:
                continue

            # Make sure lvm doesn't get confused by PVs that belong to
            # incomplete VGs. We will remove the PVs from the blacklist when/if
            # the time comes to remove the incomplete VG and its PVs.
            for pv in vg.pvs:
                lvm.lvm_cc_addFilterRejectRegexp(pv.name)

    # ROCKS8: from python-blivet-0.61.15.75
    def getRelatedDisks(self, disk):
        """ Return disks related to disk by container membership.

            :param :class:`~.devices.StorageDevice` disk: the disk
            :returns: related disks
            :rtype: set of :class:`~.devices.StorageDevice`

            .. note::

                The disk may be hidden.

        """
        return set(d for dep in self.getDependentDevices(disk, hidden=True)
                        for d in dep.disks)

    def hide(self, device):
        """ Hide the specified device.

            :param device: the device to hide
            :type device: :class:`~.devices.StorageDevice`

            Hiding a device will cancel all actions that involve the device and
            will remove the device from the device list.

            If the device is not a leaf device, all devices that depend on it
            will be hidden leaves-first until the device is a leaf device.

            If a device exists, performs some special actions and places
            it on a list of hidden devices.

            Mixes recursion and side effects, most significantly in the code
            that removes all the actions. However, this code is a null op
            in every case except the first base case that is reached,
            where all actions are removed. This means that when a device
            is removed explicitly in this function by means of a direct call to
            _removeDevices it is guaranteed that all actions have already
            been canceled.

            If a device does not exist then it must have been removed by the
            cancelation of all the actions, so it does not need to be removed
            explicitly.

            Most devices are considered leaf devices if they have no children,
            however, some devices must satisfy more stringent requirements.
            _removeDevice() will raise an exception if the device it is
            removing is not a leaf device. hide() guarantees that any
            device that it removes will have no children, but it does not
            guarantee that the more stringent requirements will be enforced.
            Therefore, _removeDevice() is invoked with the force parameter
            set to True, to skip the isleaf check.
        """
        if device in self._hidden:
            return

        # cancel actions first thing so that we hide the correct set of devices
        if device.isDisk:
            # Cancel all actions on this disk and any disk related by way of an
            # aggregate/container device (eg: lvm volume group).
            disks = [device]
            related_actions = [a for a in self._actions
                                    if a.device.dependsOn(device)]
            for related_device in (a.device for a in related_actions):
                disks.extend(related_device.disks)

            disks = set(disks)
            cancel = [a for a in self._actions
                            if set(a.device.disks).intersection(disks)]
            for action in reversed(cancel):
                self.cancelAction(action)

        for d in self.getChildren(device):
            self.hide(d)

        log.info("hiding device %s", device)

        if not device.exists:
            return

        self._removeDevice(device, force=True, modparent=False)

        self._hidden.append(device)
        lvm.lvm_cc_addFilterRejectRegexp(device.name)

        if isinstance(device, DASDDevice):
            self.dasd.remove(device)

        if device.name not in self.names:
            self.names.append(device.name)

    def unhide(self, device):
        """ Restore a device's visibility.

            :param device: the device to restore/unhide
            :type device: :class:`~.devices.StorageDevice`

            .. note::

                Actions canceled while hiding the device are not rescheduled
                automatically.

        """

        # the hidden list should be in leaves-first order
        for hidden in reversed(self._hidden):
            if hidden == device or hidden.dependsOn(device) and \
               not any(parent in self._hidden for parent in hidden.parents):

                log.info("unhiding device %s %s (id %d)", hidden.type,
                                                          hidden.name,
                                                          hidden.id)
                self._hidden.remove(hidden)
                self._devices.append(hidden)
                hidden.addHook(new=False)
                lvm.lvm_cc_removeFilterRejectRegexp(hidden.name)
                if isinstance(device, DASDDevice):
                    self.dasd.append(device)

    def setupDiskImages(self):
        """ Set up devices to represent the disk image files. """
        for (name, path) in self.diskImages.items():
            log.info("setting up disk image file '%s' as '%s'", path, name)
            dmdev = self.getDeviceByName(name)
            if dmdev and isinstance(dmdev, DMLinearDevice) and \
               path in (d.path for d in dmdev.ancestors):
                log.debug("using %s", dmdev)
                dmdev.setup()
                continue

            try:
                filedev = FileDevice(path, exists=True)
                filedev.setup()
                log.debug("%s", filedev)

                loop_name = blockdev.loop.get_loop_name(filedev.path)
                loop_sysfs = None
                if loop_name:
                    loop_sysfs = "/class/block/%s" % loop_name
                loopdev = LoopDevice(name=loop_name,
                                     parents=[filedev],
                                     sysfsPath=loop_sysfs,
                                     exists=True)
                loopdev.setup()
                log.debug("%s", loopdev)
                dmdev = DMLinearDevice(name,
                                       dmUuid="ANACONDA-%s" % name,
                                       parents=[loopdev],
                                       exists=True)
                dmdev.setup()
                dmdev.updateSysfsPath()
                log.debug("%s", dmdev)
            except (ValueError, DeviceError) as e:
                log.error("failed to set up disk image: %s", e)
            else:
                self._addDevice(filedev)
                self._addDevice(loopdev)
                self._addDevice(dmdev)
                info = udev.get_device(dmdev.sysfsPath)
                self.addUdevDevice(info)

    def backupConfigs(self, restore=False):
        """ Create a backup copies of some storage config files. """
        configs = ["/etc/mdadm.conf"]
        for cfg in configs:
            if restore:
                src = cfg + ".anacbak"
                dst = cfg
                func = os.rename
                op = "restore from backup"
            else:
                src = cfg
                dst = cfg + ".anacbak"
                func = shutil.copy2
                op = "create backup copy"

            if os.access(dst, os.W_OK):
                try:
                    os.unlink(dst)
                except OSError as e:
                    msg = str(e)
                    log.info("failed to remove %s: %s", dst, msg)

            if os.access(src, os.W_OK):
                # copy the config to a backup with extension ".anacbak"
                try:
                    func(src, dst)
                except (IOError, OSError) as e:
                    msg = str(e)
                    log.error("failed to %s of %s: %s", op, cfg, msg)
            elif restore and os.access(cfg, os.W_OK):
                # remove the config since we created it
                log.info("removing anaconda-created %s", cfg)
                try:
                    os.unlink(cfg)
                except OSError as e:
                    msg = str(e)
                    log.error("failed to remove %s: %s", cfg, msg)
            else:
                # don't try to backup non-existent configs
                log.info("not going to %s of non-existent %s", op, cfg)

    def restoreConfigs(self):
        self.backupConfigs(restore=True)

    def populate(self, cleanupOnly=False):
        """ Locate all storage devices.

            Everything should already be active. We just go through and gather
            details as needed and set up the relations between various devices.

            Devices excluded via disk filtering (or because of disk images) are
            scanned just the rest, but then they are hidden at the end of this
            process.
        """
        self.backupConfigs()
        if cleanupOnly:
            self._cleanup = True

        try:
            self._populate()
        except Exception:
            raise
        finally:
            self._hideIgnoredDisks()
            self.restoreConfigs()

    def _populate(self):
        log.info("DeviceTree.populate: ignoredDisks is %s ; exclusiveDisks is %s",
                    self.ignoredDisks, self.exclusiveDisks)

        # this has proven useful when populating after opening a LUKS device
        udev.settle()

        self.dropLVMCache()

        if flags.installer_mode and not flags.image_install:
            blockdev.mpath.set_friendly_names(flags.multipath_friendly_names)

        self.setupDiskImages()

        # mark the tree as unpopulated so exception handlers can tell the
        # exception originated while finding storage devices
        self.populated = False

        # resolve the protected device specs to device names
        for spec in self.protectedDevSpecs:
            name = udev.resolve_devspec(spec)
            log.debug("protected device spec %s resolved to %s", spec, name)
            if name:
                self.protectedDevNames.append(name)

        # FIXME: the backing dev for the live image can't be used as an
        # install target.  note that this is a little bit of a hack
        # since we're assuming that /run/initramfs/live will exist
        for mnt in open("/proc/mounts").readlines():
            if " /run/initramfs/live " not in mnt:
                continue

            live_device_name = mnt.split()[0].split("/")[-1]
            log.info("%s looks to be the live device; marking as protected",
                     live_device_name)
            self.protectedDevNames.append(live_device_name)
            self.liveBackingDevice = live_device_name
            break

        old_devices = {}

        # Now, loop and scan for devices that have appeared since the two above
        # blocks or since previous iterations.
        while True:
            devices = []
            new_devices = udev.get_devices()

            for new_device in new_devices:
                new_name = udev.device_get_name(new_device)
                if new_name not in old_devices:
                    old_devices[new_name] = new_device
                    devices.append(new_device)

            if len(devices) == 0:
                # nothing is changing -- we are finished building devices
                break

            log.info("devices to scan: %s", [udev.device_get_name(d) for d in devices])
            for dev in devices:
                self.addUdevDevice(dev)

        self.populated = True

        # After having the complete tree we make sure that the system
        # inconsistencies are ignored or resolved.
        self._handleInconsistencies()

        if flags.installer_mode:
            self.teardownAll()

    def _isIgnoredDisk(self, disk):
        return ((self.ignoredDisks and disk.name in self.ignoredDisks) or
                (self.exclusiveDisks and
                 disk.name not in self.exclusiveDisks))

    def _hideIgnoredDisks(self):
        # hide any subtrees that begin with an ignored disk
        for disk in [d for d in self._devices if d.isDisk]:
            if self._isIgnoredDisk(disk):
                ignored = True
                # If the filter allows all members of a fwraid or mpath, the
                # fwraid or mpath itself is implicitly allowed as well. I don't
                # like this very much but we have supported this usage in the
                # past, so I guess we will support it forever.
                if disk.parents and all(p.format.hidden for p in disk.parents):
                    ignored = any(self._isIgnoredDisk(d) for d in disk.parents)

                if ignored:
                    self.hide(disk)

    def teardownAll(self):
        """ Run teardown methods on all devices. """
        for device in self.leaves:
            if device.protected:
                continue

            try:
                device.teardown(recursive=True)
            except (StorageError, blockdev.BlockDevError) as e:
                log.info("teardown of %s failed: %s", device.name, e)

    def teardownDiskImages(self):
        """ Tear down any disk image stacks. """
        self.teardownAll()
        for (name, _path) in self.diskImages.items():
            dm_device = self.getDeviceByName(name)
            if not dm_device:
                continue

            dm_device.deactivate()
            loop_device = dm_device.parents[0]
            loop_device.teardown()

    def setupAll(self):
        """ Run setup methods on all devices. """
        for device in self.leaves:
            try:
                device.setup()
            except DeviceError as e:
                log.error("setup of %s failed: %s", device.name, e)

    def _filterDevices(self, incomplete=False, hidden=False):
        """ Return list of devices modified according to parameters.

            :param bool incomplete: include incomplete devices in result
            :param bool hidden: include hidden devices in result

            :returns: a generator of devices
            :rtype: generator of :class:`~.devices.Device`
        """
        if hidden:
            devices = (d for d in self._devices[:] + self._hidden[:])
        else:
            devices = (d for d in self._devices[:])

        if not incomplete:
            devices = (d for d in devices if getattr(d, "complete", True))
        return devices

    def getDeviceBySysfsPath(self, path, incomplete=False, hidden=False):
        """ Return a list of devices with a matching sysfs path.

            :param str path: the sysfs path to match
            :param bool incomplete: include incomplete devices in search
            :param bool hidden: include hidden devices in search
            :returns: the first matching device found
            :rtype: :class:`~.devices.Device`
        """
        log_method_call(self, path=path, incomplete=incomplete, hidden=hidden)
        result = None
        if path:
            devices = self._filterDevices(incomplete=incomplete, hidden=hidden)
            result = next((d for d in devices if d.sysfsPath == path), None)
        log_method_return(self, result)
        return result

    def getDeviceByUuid(self, uuid, incomplete=False, hidden=False):
        """ Return a list of devices with a matching UUID.

            :param str uuid: the UUID to match
            :param bool incomplete: include incomplete devices in search
            :param bool hidden: include hidden devices in search
            :returns: the first matching device found
            :rtype: :class:`~.devices.Device`
        """
        log_method_call(self, uuid=uuid, incomplete=incomplete, hidden=hidden)
        result = None
        if uuid:
            devices = self._filterDevices(incomplete=incomplete, hidden=hidden)
            result = next((d for d in devices if d.uuid == uuid or d.format.uuid == uuid), None)
        log_method_return(self, result)
        return result

    def getDevicesBySerial(self, serial, incomplete=False, hidden=False):
        """ Return a list of devices with a matching serial.

            :param str serial: the serial to match
            :param bool incomplete: include incomplete devices in search
            :param bool hidden: include hidden devices in search
            :returns: all matching devices found
            :rtype: list of :class:`~.devices.Device`
        """
        log_method_call(self, serial=serial, incomplete=incomplete, hidden=hidden)
        devices = self._filterDevices(incomplete=incomplete, hidden=hidden)
        retval = []
        for device in devices:
            if not hasattr(device, "serial"):
                log.warning("device %s has no serial attr", device.name)
                continue
            if device.serial == serial:
                retval.append(device)
        log_method_return(self, [r.name for r in retval])
        return retval

    def getDeviceByLabel(self, label, incomplete=False, hidden=False):
        """ Return a device with a matching filesystem label.

            :param str label: the filesystem label to match
            :param bool incomplete: include incomplete devices in search
            :param bool hidden: include hidden devices in search
            :returns: the first matching device found
            :rtype: :class:`~.devices.Device`
        """
        log_method_call(self, label=label, incomplete=incomplete, hidden=hidden)
        result = None
        if label:
            devices = self._filterDevices(incomplete=incomplete, hidden=hidden)
            result = next((d for d in devices if getattr(d.format, "label", None) == label), None)
        log_method_return(self, result)
        return result

    def getDeviceByName(self, name, incomplete=False, hidden=False):
        """ Return a device with a matching name.

            :param str name: the name to look for
            :param bool incomplete: include incomplete devices in search
            :param bool hidden: include hidden devices in search
            :returns: the first matching device found
            :rtype: :class:`~.devices.Device`
        """
        log_method_call(self, name=name, incomplete=incomplete, hidden=hidden)
        result = None
        if name:
            devices = self._filterDevices(incomplete=incomplete, hidden=hidden)
            result = next((d for d in devices if d.name == name or \
               ((d.type == "lvmlv" or d.type == "lvmvg") and d.name == name.replace("--","-"))),
               None)
        log_method_return(self, result)
        return result

    def getDeviceByPath(self, path, incomplete=False, hidden=False):
        """ Return a device with a matching path.

            If there is more than one device with a matching path,
            prefer a leaf device to a non-leaf device.

            :param str path: the path to match
            :param bool incomplete: include incomplete devices in search
            :param bool hidden: include hidden devices in search
            :returns: the first matching device found
            :rtype: :class:`~.devices.Device`
        """
        log_method_call(self, path=path, incomplete=incomplete, hidden=hidden)
        result = None
        if path:
            devices = self._filterDevices(incomplete=incomplete, hidden=hidden)

            # The usual order of the devices list is one where leaves are at
            # the end. So that the search can prefer leaves to interior nodes
            # the list that is searched is the reverse of the devices list.
            result = next((d for d in reversed(list(devices)) if d.path == path or \
               ((d.type == "lvmlv" or d.type == "lvmvg") and d.path == path.replace("--","-"))),
               None)

        log_method_return(self, result)
        return result

    def getDevicesByType(self, device_type, incomplete=False, hidden=False):
        """ Return a list of devices with a matching device type.

            :param str device_type: the type to match
            :param bool incomplete: include incomplete devices in search
            :param bool hidden: include hidden devices in search
            :returns: all matching device found
            :rtype: list of :class:`~.devices.Device`
        """
        log_method_call(self, device_type=device_type, incomplete=incomplete, hidden=hidden)
        devices = self._filterDevices(incomplete=incomplete, hidden=hidden)
        result = [d for d in devices if d.type == device_type]
        log_method_return(self, [r.name for r in result])
        return result

    def getDevicesByInstance(self, device_class, incomplete=False, hidden=False):
        """ Return a list of devices with a matching device class.

            :param class device_class: the device class to match
            :param bool incomplete: include incomplete devices in search
            :param bool hidden: include hidden devices in search
            :returns: all matching device found
            :rtype: list of :class:`~.devices.Device`
        """
        log_method_call(self, device_class=device_class, incomplete=incomplete, hidden=hidden)
        devices = self._filterDevices(incomplete=incomplete, hidden=hidden)
        result = [d for d in devices if isinstance(d, device_class)]
        log_method_return(self, [r.name for r in result])
        return result

    def getDeviceByID(self, id_num, incomplete=False, hidden=False):
        """ Return a device with specified device id.

            :param int id_num: the id to look for
            :param bool incomplete: include incomplete devices in search
            :param bool hidden: include hidden devices in search
            :returns: the first matching device found
            :rtype: :class:`~.devices.Device`
        """
        log_method_call(self, id_num=id_num, incomplete=incomplete, hidden=hidden)
        devices = self._filterDevices(incomplete=incomplete, hidden=hidden)
        result = next((d for d in devices if d.id == id_num), None)
        log_method_return(self, result)
        return result

    @property
    def devices(self):
        """ List of devices currently in the tree """
        devices = []
        for device in self._devices:
            if not getattr(device, "complete", True):
                continue

            if device.uuid and device.uuid in [d.uuid for d in devices] and \
               not isinstance(device, NoDevice):
                raise DeviceTreeError("duplicate uuids in device tree")

            devices.append(device)

        return devices

    @property
    def filesystems(self):
        """ List of filesystems. """
        #""" Dict with mountpoint keys and filesystem values. """
        filesystems = []
        for dev in self.leaves:
            if dev.format and getattr(dev.format, 'mountpoint', None):
                filesystems.append(dev.format)

        return filesystems

    @property
    def uuids(self):
        """ Dict with uuid keys and :class:`~.devices.Device` values. """
        uuids = {}
        for dev in self._devices:
            try:
                uuid = dev.uuid
            except AttributeError:
                uuid = None

            if uuid:
                uuids[uuid] = dev

            try:
                uuid = dev.format.uuid
            except AttributeError:
                uuid = None

            if uuid:
                uuids[uuid] = dev

        return uuids

    @property
    def labels(self):
        """ Dict with label keys and Device values.

            FIXME: duplicate labels are a possibility
        """
        labels = {}
        for dev in self._devices:
            # don't include btrfs member devices
            if getattr(dev.format, "label", None) and \
               (dev.format.type != "btrfs" or isinstance(dev, BTRFSDevice)):
                labels[dev.format.label] = dev

        return labels

    @property
    def leaves(self):
        """ List of all devices upon which no other devices exist. """
        leaves = [d for d in self._devices if d.isleaf]
        return leaves

    def getChildren(self, device):
        """ Return a list of a device's children. """
        return [c for c in self._devices if device in c.parents]

    def resolveDevice(self, devspec, blkidTab=None, cryptTab=None, options=None):
        """ Return the device matching the provided device specification.

            The spec can be anything from a device name (eg: 'sda3') to a device
            node path (eg: '/dev/mapper/fedora-root' or '/dev/dm-2') to
            something like 'UUID=xyz-tuv-qrs' or 'LABEL=rootfs'.

            :param devspec: a string describing a block device
            :type devspec: str
            :keyword blkidTab: blkid info
            :type blkidTab: :class:`~.BlkidTab`
            :keyword cryptTab: crypto info
            :type cryptTab: :class:`~.CryptTab`
            :keyword options: mount options
            :type options: str
            :returns: the device
            :rtype: :class:`~.devices.StorageDevice` or None
        """
        # find device in the tree
        device = None
        if devspec.startswith("UUID="):
            # device-by-uuid
            uuid = devspec.partition("=")[2]
            if ((uuid.startswith('"') and uuid.endswith('"')) or
                (uuid.startswith("'") and uuid.endswith("'"))):
                uuid = uuid[1:-1]
            device = self.uuids.get(uuid)
        elif devspec.startswith("LABEL="):
            # device-by-label
            label = devspec.partition("=")[2]
            if ((label.startswith('"') and label.endswith('"')) or
                (label.startswith("'") and label.endswith("'"))):
                label = label[1:-1]
            device = self.labels.get(label)
        elif re.match(r'(0x)?[A-Za-z0-9]{2}(p\d+)?$', devspec):
            # BIOS drive number
            spec = int(devspec, 16)
            for (edd_name, edd_number) in edd.edd_dict.items():
                if edd_number == spec:
                    device = self.getDeviceByName(edd_name)
                    break
        elif options and "nodev" in options.split(","):
            device = self.getDeviceByName(devspec)
            if not device:
                device = self.getDeviceByPath(devspec)
        else:
            if not devspec.startswith("/dev/"):
                device = self.getDeviceByName(devspec)
                if not device:
                    devspec = "/dev/" + devspec

            if not device:
                if devspec.startswith("/dev/disk/"):
                    devspec = os.path.realpath(devspec)

                if devspec.startswith("/dev/dm-"):
                    try:
                        dm_name = blockdev.dm.name_from_node(devspec[5:])
                    except blockdev.DMError as e:
                        log.info("failed to resolve %s: %s", devspec, e)
                        dm_name = None

                    if dm_name:
                        devspec = "/dev/mapper/" + dm_name

                if re.match(r'/dev/md\d+(p\d+)?$', devspec):
                    try:
                        md_name = blockdev.md.name_from_node(devspec[5:])
                    except blockdev.MDRaidError as e:
                        log.info("failed to resolve %s: %s", devspec, e)
                        md_name = None

                    if md_name:
                        devspec = "/dev/md/" + md_name

                # device path
                device = self.getDeviceByPath(devspec)

            if device is None:
                if blkidTab:
                    # try to use the blkid.tab to correlate the device
                    # path with a UUID
                    blkidTabEnt = blkidTab.get(devspec)
                    if blkidTabEnt:
                        log.debug("found blkid.tab entry for '%s'", devspec)
                        uuid = blkidTabEnt.get("UUID")
                        if uuid:
                            device = self.getDeviceByUuid(uuid)
                            if device:
                                devstr = device.name
                            else:
                                devstr = "None"
                            log.debug("found device '%s' in tree", devstr)
                        if device and device.format and \
                           device.format.type == "luks":
                            map_name = device.format.mapName
                            log.debug("luks device; map name is '%s'", map_name)
                            mapped_dev = self.getDeviceByName(map_name)
                            if mapped_dev:
                                device = mapped_dev

                if device is None and cryptTab and \
                   devspec.startswith("/dev/mapper/"):
                    # try to use a dm-crypt mapping name to
                    # obtain the underlying device, possibly
                    # using blkid.tab
                    cryptTabEnt = cryptTab.get(devspec.split("/")[-1])
                    if cryptTabEnt:
                        luks_dev = cryptTabEnt['device']
                        try:
                            device = self.getChildren(luks_dev)[0]
                        except IndexError as e:
                            pass
                elif device is None:
                    # dear lvm: can we please have a few more device nodes
                    #           for each logical volume?
                    #           three just doesn't seem like enough.
                    name = devspec[5:]      # strip off leading "/dev/"

                    (vg_name, _slash, lv_name) = name.partition("/")
                    if lv_name and not "/" in lv_name:
                        # looks like we may have one
                        lv = "%s-%s" % (vg_name, lv_name)
                        device = self.getDeviceByName(lv)

        # check mount options for btrfs volumes in case it's a subvol
        if device and device.type.startswith("btrfs") and options:
            # start with the volume -- not a subvolume
            device = getattr(device, "volume", device)

            attr = None
            if "subvol=" in options:
                attr = "name"
                val = util.get_option_value("subvol", options)
            elif "subvolid=" in options:
                attr = "vol_id"
                val = util.get_option_value("subvolid", options)
            elif device.defaultSubVolume:
                # default subvolume
                device = device.defaultSubVolume

            if attr and val:
                for subvol in device.subvolumes:
                    if getattr(subvol, attr, None) == val:
                        device = subvol
                        break

        if device:
            log.debug("resolved '%s' to '%s' (%s)", devspec, device.name, device.type)
        else:
            log.debug("failed to resolve '%s'", devspec)
        return device

    def getActiveMounts(self):
        """ Reflect active mounts in the appropriate devices' formats. """
        log.info("collecting information about active mounts")
        for line in open("/proc/mounts").readlines():
            try:
                (devspec, mountpoint, fstype, options, _rest) = line.split(None, 4)
            except ValueError:
                log.error("failed to parse /proc/mounts line: %s", line)
                continue

            if fstype == "btrfs":
                # get the subvol name from /proc/self/mountinfo
                for line in open("/proc/self/mountinfo").readlines():
                    fields = line.split()
                    _subvol = fields[3]
                    _mountpoint = fields[4]
                    _devspec = fields[9]
                    if _mountpoint == mountpoint and _devspec == devspec:
                        log.debug("subvol %s", _subvol)
                        options += ",subvol=%s" % _subvol[1:]

            if fstype in nodev_filesystems:
                if not flags.include_nodev:
                    continue

                log.info("found nodev %s filesystem mounted at %s",
                            fstype, mountpoint)
                # nodev filesystems require some special handling.
                # For now, a lot of this is based on the idea that it's a losing
                # battle to require the presence of an FS class for every type
                # of nodev filesystem. Based on that idea, we just instantiate
                # NoDevFS directly and then hack in the fstype as the device
                # attribute.
                fmt = getFormat("nodev")
                fmt.device = fstype

                # NoDevice also needs some special works since they don't have
                # per-instance names in the kernel.
                device = NoDevice(fmt=fmt)
                n = len([d for d in self.devices if d.format.type == fstype])
                device._name += ".%d" % n
                self._addDevice(device)
                devspec = device.name

            device = self.resolveDevice(devspec, options=options)
            if device is not None:
                device.format.mountpoint = mountpoint   # for future mounts
                device.format._mountpoint = mountpoint  # active mountpoint
                device.format.mountopts = options
