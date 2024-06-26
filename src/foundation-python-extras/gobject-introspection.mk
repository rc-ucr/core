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

# ROCKS - inactive code, transferred to foundation-python-gobject-introspection

HTTPGET         = ../../src/devel/devel/bin/httpget.sh
GOBJECT_VERSION	= 1.56.1
GOBJ = gobject-introspection

# build twice - first time, install into a temporary install directory
# so that pygobject can link against it
build::
# ROCKS8: Bump to version 1.56.1 (same as on Rocky8)
# https://download.gnome.org/sources/gobject-introspection/1.56/gobject-introspection-1.56.1.tar.xz
ifeq ($(shell ! test -f $(GOBJ)-$(GOBJECT_VERSION).tar.xz && echo -n yes),yes)
	@echo "ROCKS8: Sideloading $(GOBJ)-$(GOBJECT_VERSION).tar.xz."
	$(HTTPGET) -B https://download.gnome.org -F sources/gobject-introspection/1.56 -n $(GOBJ)-$(GOBJECT_VERSION).tar.xz
endif
	unxz -c $(GOBJ)-$(GOBJECT_VERSION).tar.xz | $(TAR) -xf -
	(								\
		cd $(GOBJ)-$(GOBJECT_VERSION);				\
		PATH=/opt/rocks/bin:$$PATH				\
			./configure --prefix=$(PWD)/../$(TMPINSTALL);	\
		$(MAKE); $(MAKE) install; $(MAKE) clean;		\
		PATH=/opt/rocks/bin:$$PATH				\
			./configure --prefix=$(PKGROOT);		\
		$(MAKE);						\
	)
	
install::
	(								\
		cd $(GOBJ)-$(GOBJECT_VERSION);			\
		$(MAKE) prefix=$(ROOT)/$(PKGROOT) install;		\
	)


clean::
	rm -rf $(GOBJ)-$(GOBJECT_VERSION)

