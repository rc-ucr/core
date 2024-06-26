#
# $Id: Makefile,v 1.3 2012/11/27 00:48:08 phil Exp $
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
# $Log: Makefile,v $
# Revision 1.3  2012/11/27 00:48:08  phil
# Copyright Storm for Emerald Boa
#
# Revision 1.2  2012/05/06 05:48:18  phil
# Copyright Storm for Mamba
#
# Revision 1.1  2012/01/23 19:53:44  phil
# Required to properly complete buildinstall script provided with Redhat anaconda used in
# RHEL/CentOS 6
#
#

REDHAT.ROOT     = $(CURDIR)/../../
HTTPGET         = $(ROCKSROOT)/bin/httpget.sh

-include $(ROCKSROOT)/etc/Rules.mk
include Rules.mk

BUILDROOT =  $(CURDIR)/$(NAME).buildroot


build::
# ROCKS8 Neded for port of old Rocks Installer to Rocky8
# http://vault.centos.org/7.9.2009/updates/Source/SPackages/NetworkManager-1.18.8-2.el7_9.src.rpm
ifeq ($(shell ! test -f $(SRCPKGNAME)  && echo -n yes),yes)
	@echo "ROCKS8: Sideloading $(SRCPKGNAME)."
	$(HTTPGET) -B $(SRCPKGHOST) -F $(SRCPKGPATH) -n $(SRCPKGNAME)
endif
	(									\
		mkdir content && cd content;					\
		rpm2cpio ../$(SRCPKGNAME) | cpio -idmv;				\
		mv NetworkManager-$(VERSION).tar.xz ../ && cd ..;		\
		tar xf NetworkManager-$(VERSION).tar.xz;			\
		cd NetworkManager-$(VERSION);					\
		PATCHES=`ls ../content/*.patch`;				\
		for pat in $$PATCHES; do patch -p1 < $$pat; done;		\
		autoreconf --install --force;					\
		intltoolize --automake --copy --force;				\
		PKG_CONFIG_PATH=/opt/rocks/lib/pkgconfig:$$PKG_CONFIG_PATH 	\
		PYTHON=/opt/rocks/bin/python \
		LIBS="-lsepol /opt/rocks/lib/libcurl.a /opt/rocks/lib/libssl.a /opt/rocks/lib/libcrypto.a -lpcre" \
		./configure --prefix=$(PKGROOT) --disable-silent-rules \
		--disable-static --with-dhclient=yes --with-dhcpcd=no --with-dhcpcanon=no \
		--with-config-dhcp-default=internal --with-address-sanitizer=no \
		--disable-undefined-sanitizer --disable-more-logging --without-more-asserts \
		--enable-ld-gc --with-libnm-glib;				\
		$(MAKE);							\
	)

install::
	mkdir -p $(ROOT)/$(PKGROOT)
	(									\
		cd NetworkManager-$(VERSION);					\
		$(MAKE) prefix=$(ROOT)/$(PKGROOT) install;			\
	)	
#		rm -rf $(ROOT)/$(PKGROOT)/bin;					\
		rm -rf $(ROOT)/$(PKGROOT)/etc;					\
		rm -rf $(ROOT)/$(PKGROOT)/libexec;				\
		rm -rf $(ROOT)/$(PKGROOT)/sbin;					\
		rm -rf $(ROOT)/$(PKGROOT)/var;					\

clean::
	rm -rf NetworkManager-$(VERSION)
