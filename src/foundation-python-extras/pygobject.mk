#
# $Id: pygobject.mk,v 1.8 2012/11/27 00:48:36 phil Exp $
#
# @Copyright@
# 
# 				Rocks(r)
# 		         www.rocksclusters.org
# 		         version 6.2 (SideWinder)
# 		         version 7.0 (Manzanita)
# 
# Copyright (c) 2000 - 2017 The Regents of the University of California.
# All rights reserved.	
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
# 
# 1. Redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer.
# 
# 2. Redistributions in binary form must reproduce the above copyright
# notice unmodified and in its entirety, this list of conditions and the
# following disclaimer in the documentation and/or other materials provided 
# with the distribution.
# 
# 3. All advertising and press materials, printed or electronic, mentioning
# features or use of this software must display the following acknowledgement: 
# 
# 	"This product includes software developed by the Rocks(r)
# 	Cluster Group at the San Diego Supercomputer Center at the
# 	University of California, San Diego and its contributors."
# 
# 4. Except as permitted for the purposes of acknowledgment in paragraph 3,
# neither the name or logo of this software nor the names of its
# authors may be used to endorse or promote products derived from this
# software without specific prior written permission.  The name of the
# software includes the following terms, and any derivatives thereof:
# "Rocks", "Rocks Clusters", and "Avalanche Installer".  For licensing of 
# the associated name, interested parties should contact Technology 
# Transfer & Intellectual Property Services, University of California, 
# San Diego, 9500 Gilman Drive, Mail Code 0910, La Jolla, CA 92093-0910, 
# Ph: (858) 534-5815, FAX: (858) 534-7345, E-MAIL:invent@ucsd.edu
# 
# THIS SOFTWARE IS PROVIDED BY THE REGENTS AND CONTRIBUTORS ``AS IS''
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
# THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE REGENTS OR CONTRIBUTORS
# BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR
# BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
# WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
# OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN
# IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
# 
# @Copyright@
#
# $Log: pygobject.mk,v $
# Revision 1.8  2012/11/27 00:48:36  phil
# Copyright Storm for Emerald Boa
#
# Revision 1.7  2012/05/06 05:48:43  phil
# Copyright Storm for Mamba
#
# Revision 1.6  2011/07/23 02:30:45  phil
# Viper Copyright
#
# Revision 1.5  2010/09/07 23:53:06  bruno
# star power for gb
#
# Revision 1.4  2009/05/01 19:07:06  mjk
# chimi con queso
#
# Revision 1.3  2008/10/18 00:56:00  mjk
# copyright 5.1
#
# Revision 1.2  2008/03/06 23:41:43  mjk
# copyright storm on
#
# Revision 1.1  2008/01/14 17:27:29  bruno
# updates for V
#
#
ifeq ($(strip $(VERSION.MAJOR)), 6)
PYGOBJECT_VERSION	= 2.14.1
endif

ifeq ($(strip $(VERSION.MAJOR)), 7)
PYGOBJECT_VERSION	= 3.14.0
endif

ifeq ($(strip $(VERSION.MAJOR)), 8)
# ROCKS8: Bump to version 2.28.7 (last in 2-series)
# https://download.gnome.org/sources/pygobject/2.28/pygobject-2.28.7.tar.xz
# https://gitlab.gnome.org/GNOME/pygobject/-/blob/master/NEWS: 3.36.1 is last 
# release supporting Python 2.
# https://download.gnome.org/sources/pygobject/3.36/pygobject-3.36.1.tar.xz
PYGOBJECT_VERSION	= 3.36.1
endif

# ROCKS8: inactive code, now trasferred to foundation-python-pygobject

HTTPGET         = ../../src/devel/devel/bin/httpget.sh

# install twice - first time, install into a temporary install directory
# so that pygtk can link against it
build::
ifeq ($(shell ! test -f pygobject-$(PYGOBJECT_VERSION).tar.xz && echo -n yes),yes)
	@echo "ROCKS8: Sideloading pygobject-$(PYGOBJECT_VERSION).tar.xz."
	$(HTTPGET) -B https://download.gnome.org -F sources/pygobject/3.36 -n pygobject-$(PYGOBJECT_VERSION).tar.xz
endif
	unxz -c pygobject-$(PYGOBJECT_VERSION).tar.xz | $(TAR) -xf -
	(										\
		cd pygobject-$(PYGOBJECT_VERSION);					\
		PKG_CONFIG_PATH=$(PWD)/../tmpinstall/lib/pkgconfig:$$PKG_CONFIG_PATH	\
		$(PY.PATH) setup.py build;						\
		PKG_CONFIG_PATH=$(PWD)/../tmpinstall/lib/pkgconfig:$$PKG_CONFIG_PATH    \
		$(PY.PATH) setup.py install --root=$(PWD)/../tmpinstall;		\
	)
	
install::
	(										\
		cd pygobject-$(PYGOBJECT_VERSION);					\
		PKG_CONFIG_PATH=$(PWD)/../tmpinstall/lib/pkgconfig:$$PKG_CONFIG_PATH	\
		$(PY.PATH) setup.py install --root=$(ROOT);				\
	)


clean::
	rm -rf pygobject-$(PYGOBJECT_VERSION)

