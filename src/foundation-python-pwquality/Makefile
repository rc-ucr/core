# --------------------------------------------------- -*- Makefile -*- --
# $Id: Makefile,v 1.3 2012/11/27 00:48:37 phil Exp $
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
# Revision 1.3  2012/11/27 00:48:37  phil
# Copyright Storm for Emerald Boa
#
# Revision 1.2  2012/05/06 05:48:43  phil
# Copyright Storm for Mamba
#
# Revision 1.1  2012/02/01 20:42:59  phil
# Setuptools needed for MYSQL-python build on Rocks v. 6
#
#

PKGROOT		= /opt/rocks
REDHAT.ROOT     = $(CURDIR)/../../
ROCKSROOT       = ../devel/devel
HTTPGET         = ../../src/devel/devel/bin/httpget.sh
-include $(ROCKSROOT)/etc/Rules.mk
include Rules.mk


build::
# ROCKS8: Creates missing python-pwquality package for foundation-anaconda
ifeq ($(shell ! test -f $(SRCPKGNAME) && echo -n yes),yes)
	@echo "ROCKS8: Sideloading $(SRCPKGNAME)."
	$(HTTPGET) -B $(SRCPKGHOST) -F $(SRCPKGPATH) -n $(SRCPKGNAME)
endif
	(								\
		mkdir content && cd content;				\
		rpm2cpio ../$(SRCPKGNAME) | cpio -idmv;			\
		mv lib$(PYPKGNAME)-$(VERSION).tar.bz2 ../ && cd ..;	\
		tar xjf lib$(PYPKGNAME)-$(VERSION).tar.bz2;		\
		cd lib$(PYPKGNAME)-$(VERSION);				\
		PATCHES=`ls ../content/*.patch`;			\
		for pat in $$PATCHES; do patch -p1 < $$pat; done;	\
		autoreconf -if;						\
		PYTHON=$(PY.PATH) ./configure --prefix=$(PKGROOT) 	\
		--with-pythonsitedir=lib/python2.7/site-packages	\
		--with-securedir=/lib64/security --with-python-binary=$(PY.PATH);\
		$(MAKE);						\
	)

install::
	mkdir -p $(ROOT)/$(PKGROOT)
	(								\
		cd lib$(PYPKGNAME)-$(VERSION);				\
		$(MAKE) DESTDIR=$(ROOT) install;		\
		rm -rf $(ROOT)/$(PKGROOT)/bin;				\
		rm -rf $(ROOT)/$(PKGROOT)/etc;				\
		rm -rf $(ROOT)/$(PKGROOT)/include;			\
		rm -rf $(ROOT)/$(PKGROOT)/share;			\
		rm -rf $(ROOT)/$(PKGROOT)/lib/pkgconfig;		\
		rm -rf $(ROOT)/$(PKGROOT)/lib/lib*;			\
		rm -rf $(ROOT)/lib64;					\
	)

clean::
	rm -rf $(PYPKGNAME)-$(VERSION)
	rm -rf content libpwquality-1.4.4*
