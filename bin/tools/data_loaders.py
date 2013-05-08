# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP-f3, Open Source Management Solution
#    Copyright (C) 2013 P. Christeas <xrg@hellug.gr>
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

from tools.service_meta import _ServiceMeta, abstractmethod
import logging

#.apidoc title: API for loading data files

"""A clean, extendable API for loading contents of data files in our DB

  This replaces the hard-coded XML,CSV and YAML functions of the previous
  OpenERP/F3 versions
"""

class DataLoaderException(Exception):
    pass

class DataLoader(object):
    """Abstract class of Loader algorithms. Each loader is a file format
    
        All formats are supposed to import data from a file into the ERP
        database, in "DB init" phase. That is, this API is designed for
        the init/upgrade of modules, NOT everyday data transfers (so far).

    """
    __metaclass__ = _ServiceMeta

    logger = logging.getLogger('init')

    def __init__(self, pool, uid, module_name, idref, mode='init', noupdate=False, context=None, **kwargs):
        """A loader for a specific file.
        """
        self.pool = pool
        self.uid = uid
        self.module_name = module_name
        self.idref = idref
        self.mode = mode
        self.noupdate = noupdate
        self.context = context
        self.extra_args = kwargs

    @abstractmethod
    def parse(self, cr, fname, fp):
        """ Parse the contents of a file

            @param fname the name of the file, for error reporting etc.
            @param fp A file object that can stream the contents, seek(0)'ed

            The calling code is responsible for any error handling, resetting
            the cursor etc.
        """
        pass

    @classmethod
    def unload(cls):
        """If needed, free any class-wide cache.

            This will be called at the end of all operations (like db init)
            so that we can free any persistent cache.
        """
        pass

    @staticmethod
    def unload_all():
        """Unload data of all DataLoader classes
        """
        for cn in DataLoader.list_classes():
            DataLoader.get_class(cn).unload()

#eof