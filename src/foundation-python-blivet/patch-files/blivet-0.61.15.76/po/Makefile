#
# Makefile for the PO files (translation) catalog
#
# $Id$

TOP	 = ../..

# What is this package?
NLSPACKAGE	= blivet
POTFILE		= $(NLSPACKAGE).pot
INSTALL		= /usr/bin/install -c
INSTALL_DATA	= $(INSTALL) -m 644
INSTALL_DIR	= /usr/bin/install -d

# destination directory
INSTALL_NLS_DIR = $(RPM_BUILD_ROOT)/opt/rocks/share/locale

# PO catalog handling
MSGMERGE	= msgmerge -v
XGETTEXT	= xgettext --default-domain=$(NLSPACKAGE) \
		  --add-comments
MSGFMT		= msgfmt --statistics --verbose

# What do we need to do
POFILES		= $(wildcard *.po)
MOFILES		= $(patsubst %.po,%.mo,$(POFILES))
PYSRC		= $(wildcard ../blivet/*.py ../blivet/*/*.py)
SRCFILES 	= $(PYSRC)

all::  update-po $(MOFILES)

$(POTFILE): $(SRCFILES)
	$(XGETTEXT) -L Python --keyword=_ --keyword=N_ $(SRCFILES)
	@if cmp -s $(NLSPACKAGE).po $(POTFILE); then \
	    rm -f $(NLSPACKAGE).po; \
	else \
	    mv -f $(NLSPACKAGE).po $(POTFILE); \
	fi; \

update-po: Makefile $(POTFILE) refresh-po

refresh-po: Makefile
	for cat in $(POFILES); do \
		lang=`basename $$cat .po`; \
		if $(MSGMERGE) $$lang.po $(POTFILE) > $$lang.pot ; then \
			mv -f $$lang.pot $$lang.po ; \
			echo "$(MSGMERGE) of $$lang succeeded" ; \
		else \
			echo "$(MSGMERGE) of $$lang failed" ; \
			rm -f $$lang.pot ; \
		fi \
	done

clean:
	@rm -fv *mo *~ .depend

install: $(MOFILES)
	@for n in $(MOFILES); do \
	    l=`basename $$n .mo`; \
	    $(INSTALL_DIR) $(INSTALL_NLS_DIR)/$$l/LC_MESSAGES; \
	    $(INSTALL_DATA) --verbose $$n $(INSTALL_NLS_DIR)/$$l/LC_MESSAGES/$(NLSPACKAGE).mo; \
	done

%.mo: %.po
	$(MSGFMT) -o $@ $<

.PHONY: missing depend


