#
# Radek Vykydal <rvykydal@redhat.com>
#
# Copyright 2018 Red Hat, Inc.
#
# This copyrighted material is made available to anyone wishing to use, modify,
# copy, or redistribute it subject to the terms and conditions of the GNU
# General Public License v.2.  This program is distributed in the hope that it
# will be useful, but WITHOUT ANY WARRANTY expressed or implied, including the
# implied warranties of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.  Any Red Hat
# trademarks that are incorporated in the source code or documentation are not
# subject to the GNU General Public License and may only be used or replicated
# with the express permission of Red Hat, Inc.
#

import warnings
from pykickstart.base import BaseData, KickstartCommand, DeprecatedCommand
from pykickstart.errors import KickstartParseError, KickstartParseWarning
from pykickstart.options import KSOptionParser, commaSplit
from pykickstart.constants import NVDIMM_MODE_SECTOR, NVDIMM_ACTION_RECONFIGURE, \
    NVDIMM_ACTION_USE
from pykickstart.version import F28, F40, versionToLongString

from pykickstart.i18n import _


class F28_NvdimmData(BaseData):
    removedKeywords = BaseData.removedKeywords
    removedAttrs = BaseData.removedAttrs

    def __init__(self, *args, **kwargs):
        BaseData.__init__(self, *args, **kwargs)
        self.action = kwargs.get("action", None)
        self.namespace = kwargs.get("namespace", "")
        self.mode = kwargs.get("mode", None)
        self.sectorsize = kwargs.get("sectorsize", None)
        self.blockdevs = kwargs.get("blockdevs", [])

    def __eq__(self, y):
        if not y:
            return False
        return (self.action == y.action and
                self.namespace == y.namespace and
                self.blockdevs == y.blockdevs)

    def __ne__(self, y):
        return not self == y

    def _getArgsAsStr(self):
        retval = "%s" % self.action
        if self.action == NVDIMM_ACTION_RECONFIGURE:
            if self.namespace:
                retval += " --namespace=%s" % self.namespace
            if self.mode == NVDIMM_MODE_SECTOR:
                retval += " --mode=%s" % NVDIMM_MODE_SECTOR
                if self.sectorsize:
                    retval += " --sectorsize=%d" % self.sectorsize
        elif self.action == NVDIMM_ACTION_USE:
            if self.namespace:
                retval += " --namespace=%s" % self.namespace
            if self.blockdevs:
                retval += " --blockdevs=%s" % ",".join(self.blockdevs)

        return retval

    def __str__(self):
        retval = BaseData.__str__(self)
        retval += "nvdimm %s" % self._getArgsAsStr()
        return retval.strip() + "\n"


class F28_Nvdimm(KickstartCommand):
    removedKeywords = KickstartCommand.removedKeywords
    removedAttrs = KickstartCommand.removedAttrs

    validActions = [NVDIMM_ACTION_RECONFIGURE, NVDIMM_ACTION_USE]
    validModes = [NVDIMM_MODE_SECTOR]

    def __init__(self, writePriority=80, *args, **kwargs):
        KickstartCommand.__init__(self, writePriority, *args, **kwargs)
        self.actionList = kwargs.get("actionList", [])
        self.op = self._getParser()

    def __str__(self):
        retval = ""

        for action in self.actionList:
            retval += action.__str__()

        if retval != "":
            retval = "# NVDIMM devices setup\n" + retval + "\n"

        return retval

    def _getParser(self):
        op = KSOptionParser(prog="nvdimm", version=F28, description="""
                            Perform an action on an nvdimm device.""")
        op.add_argument("action", choices=self.validActions, nargs=1, default=NVDIMM_ACTION_RECONFIGURE,
                        version=F28, help="""
                        The action to be performed on the device specified by further options.
                        The device can be specified by ``--namespace`` or ``--blockdevs`` options,
                        depending on the action.

                        Valid actions:
                        - ``reconfigure``: Reconfigures the device specified by ``--namespace``
                                           into the mode specified by ``--mode`` and (depending on the mode)
                                           ``--sectorsize`` options. The device reconfigured into sector mode
                                           will be allowed to be used for storage configuration.
                        - ``use``: Allow the device to be used for storage configuration. By default nvdimm
                                   devices are ignored. Only devices in sector mode can be used.
                        """)
        op.add_argument("--namespace", metavar="<namespace>", version=F28,
                        help="""The device specification by namespace.""")
        op.add_argument("--blockdevs", metavar="<devspec1>,<devspec2>,...,<devspecN>", type=commaSplit, version=F28,
                        help="""Specification of devices by comma separated list of block device names.""")
        op.add_argument("--mode", choices=self.validModes, default=NVDIMM_MODE_SECTOR,
                        version=F28, help="""The mode specification.""")
        op.add_argument("--sectorsize", type=int, version=F28,
                        help="""Size of a sector for sector mode.""")
        return op

    def parse(self, args):
        (ns, extra) = self.op.parse_known_args(args=args, lineno=self.lineno)

        if extra:
            raise KickstartParseError(_("Unexpected arguments to nvdimm command: %(arguments)s") %
                                      {"arguments": extra}, lineno=self.lineno)

        nvdimm_data = self.dataClass()   # pylint: disable=not-callable
        self.set_to_obj(ns, nvdimm_data)
        nvdimm_data.lineno = self.lineno
        action = ns.action[0]
        nvdimm_data.action = action

        if nvdimm_data.namespace and nvdimm_data.blockdevs:
            raise KickstartParseError(_("Only one of --namespace and --blockdevs device specifications can be used"))

        # Check for duplicates in the data list.
        if nvdimm_data in self.dataList():
            if nvdimm_data.namespace:
                warnings.warn(_("An action %(action)s on namespace %(namespace)s has already been defined.")
                              % {"action": action, "namespace": nvdimm_data.namespace}, KickstartParseWarning)
            if nvdimm_data.blockdevs:
                warnings.warn(_("An action %(action)s on devices %(blockdevs)s has already been defined.")
                              % {"action": action, "blockdevs": nvdimm_data.blockdevs}, KickstartParseWarning)

        if action == NVDIMM_ACTION_RECONFIGURE:
            if not nvdimm_data.namespace:
                raise KickstartParseError(_("Action %(action)s requires --namespace argument to be set")
                                          % {"action": action})
            if nvdimm_data.mode == NVDIMM_MODE_SECTOR and not nvdimm_data.sectorsize:
                raise KickstartParseError(_("Action %(action)s with mode %(mode)s requires --sectorsize argument to be set")
                                          % {"action": action, "mode": nvdimm_data.mode})
        elif action == NVDIMM_ACTION_USE:
            if not nvdimm_data.namespace and not nvdimm_data.blockdevs:
                raise KickstartParseError(_("Action %(action)s requires --namespace or --blockdevs argument to be set")
                                          % {"action": action})

        return nvdimm_data

    def dataList(self):
        return self.actionList

    @property
    def dataClass(self):
        return self.handler.NvdimmData


class F40_Nvdimm(DeprecatedCommand, F28_Nvdimm):
    def __init__(self):  # pylint: disable=super-init-not-called
        DeprecatedCommand.__init__(self)

    def _getParser(self):
        op = F28_Nvdimm._getParser(self)
        op.description += "\n\n.. deprecated:: %s" % versionToLongString(F40)
        return op

# ROCKS8 backport from pykickstart-1.99.66.22/pykickstart/commands/nvdimm.py
# Changed op.add_option() to op.add_argument()
class RHEL7_NvdimmData(BaseData):
    removedKeywords = BaseData.removedKeywords
    removedAttrs = BaseData.removedAttrs

    def __init__(self, *args, **kwargs):
        BaseData.__init__(self, *args, **kwargs)
        self.action = kwargs.get("action", None)
        self.namespace = kwargs.get("namespace", "")
        self.mode = kwargs.get("mode", None)
        self.sectorsize = kwargs.get("sectorsize", None)
        self.blockdevs = kwargs.get("blockdevs", [])

    def __eq__(self, y):
        if not y:
            return False
        return (self.action == y.action and
                self.namespace == y.namespace and
                self.blockdevs == y.blockdevs)

    def __ne__(self, y):
        return not self == y

    def _getArgsAsStr(self):
        retval = "%s" % self.action
        if self.action == NVDIMM_ACTION_RECONFIGURE:
            if self.namespace:
                retval += " --namespace=%s" % self.namespace
            if self.mode == NVDIMM_MODE_SECTOR:
                retval += " --mode=%s" % NVDIMM_MODE_SECTOR
                if self.sectorsize:
                    retval += " --sectorsize=%d" % self.sectorsize
        elif self.action == NVDIMM_ACTION_USE:
            if self.namespace:
                retval += " --namespace=%s" % self.namespace
            if self.blockdevs:
                retval += " --blockdevs=%s" % ",".join(self.blockdevs)

        return retval

    def __str__(self):
        retval = BaseData.__str__(self)
        retval += "nvdimm %s" % self._getArgsAsStr()
        return retval.strip() + "\n"


class RHEL7_Nvdimm(KickstartCommand):
    removedKeywords = KickstartCommand.removedKeywords
    removedAttrs = KickstartCommand.removedAttrs

    def __init__(self, writePriority=80, *args, **kwargs):
        KickstartCommand.__init__(self, writePriority, *args, **kwargs)

        self.actionList = kwargs.get("actionList", [])
        self.validActions = [NVDIMM_ACTION_RECONFIGURE, NVDIMM_ACTION_USE]
        self.validModes = [NVDIMM_MODE_SECTOR]

        self.op = self._getParser()

    def __str__(self):
        retval = ""

        for action in self.actionList:
            retval += action.__str__()

        if retval != "":
            retval = "# NVDIMM devices setup\n" + retval + "\n"

        return retval

    def _getParser(self):
        def devs_cb (option, opt_str, value, parser):
            for d in value.split(','):
                parser.values.ensure_value(option.dest, []).append(d)

        op = KSOptionParser()
        op.add_argument("--namespace", dest="namespace")
        op.add_argument("--blockdevs", metavar="<devspec1>,<devspec2>,...,<devspecN>",
                      action="callback", callback=devs_cb, nargs=1, type="string")
        op.add_argument("--mode", choices=self.validModes, default=NVDIMM_MODE_SECTOR, dest="mode")
        op.add_argument("--sectorsize", type=int)
        return op

    def parse(self, args):
        (opts, extra) = self.op.parse_args(args=args, lineno=self.lineno)

        if len(extra) > 1:
            message = _("Unexpected arguments to nvdimm command: %(arguments)s") % {"arguments": extra}
            raise KickstartValueError(formatErrorMsg(self.lineno, msg=message))

        if not extra:
            message = _("Action argument is required for nvdimm command")
            raise KickstartValueError(formatErrorMsg(self.lineno, msg=message))

        action = extra[0]

        if action not in self.validActions:
            message = _("Invalid action argument (choose from '%(valid_arguments)s')") \
                % {"valid_arguments": ",".join(self.validActions)}
            raise KickstartValueError(formatErrorMsg(self.lineno, msg=message))

        nvdimm_data = self.handler.NvdimmData()
        self._setToObj(self.op, opts, nvdimm_data)
        nvdimm_data.lineno = self.lineno
        nvdimm_data.action = action

        if nvdimm_data.namespace and nvdimm_data.blockdevs:
            message = _("Only one of --namespace and --blockdevs device specifications can be used")
            raise KickstartValueError(formatErrorMsg(self.lineno, msg=message))

        # Check for duplicates in the data list.
        if nvdimm_data in self.dataList():
            if nvdimm_data.namespace:
                warnings.warn(_("An action %(action)s on namespace %(namespace)s has already been defined.")
                              % {"action": action, "namespace": nvdimm_data.namespace})
            if nvdimm_data.blockdevs:
                warnings.warn(_("An action %(action)s on devices %(blockdevs)s has already been defined.")
                              % {"action": action, "blockdevs": nvdimm_data.blockdevs})

        if action == NVDIMM_ACTION_RECONFIGURE:
            if not nvdimm_data.namespace:
                message = _("Action %(action)s requires --namespace argument to be set") \
                    % {"action": action}
                raise KickstartValueError(formatErrorMsg(self.lineno, msg=message))
            if nvdimm_data.mode == NVDIMM_MODE_SECTOR and not nvdimm_data.sectorsize:
                message = _("Action %(action)s with mode %(mode)s requires --sectorsize argument to be set") \
                    % {"action": action, "mode": nvdimm_data.mode}
                raise KickstartValueError(formatErrorMsg(self.lineno, msg=message))
        elif action == NVDIMM_ACTION_USE:
            if not nvdimm_data.namespace and not nvdimm_data.blockdevs:
                message = _("Action %(action)s requires --namespace or --blockdevs argument to be set") \
                    % {"action": action}
                raise KickstartValueError(formatErrorMsg(self.lineno, msg=message))

        return nvdimm_data

    def dataList(self):
        return self.actionList
