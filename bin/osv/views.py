# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2012 P. Christeas <xrg@hellug.gr>
#    Parts Copyright (C) 2004-2011 OpenERP SA. (www.openerp.com)
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

from lxml import etree
import tools
import os
import logging

#.apidoc title: Views definitions and utilities for ORM

"""The 'V' component in OpenObject's MVC

"""

# Let the _() be a dummy:
_ = str

class oo_view(object):
    """A view *type* definition.
    
    An instance of this object will be a _singleton_ in the server process,
    so far valid for all loaded databases
    """
    _view_type = False
    _view_name = False
    __registry = {}
    __validator = None #: XML representation of the RNG definition, cached
    __rng_paths = [['base','rng','view.rng'],] #: list of paths to open. Order matters!
    _rng_path = False
    
    def __init__(self):
        """Initialise the singleton """
        assert self._view_type and self._view_name, \
                "Invalid usage for class: %s" % self.__class__.__name__
        oo_view.__registry[self._view_type] = self
        if self._rng_path and self._rng_path not in oo_view.__rng_paths:
            oo_view.__rng_paths.append(self._rng_path)
        oo_view.__validator = None

    @classmethod
    def _load_validator(cls):
        """Preloads the RNG validator for XML parsing
        """
        if cls.__validator:
            return
        if len(cls.__rng_paths) > 1:
            raise NotImplementedError # TODO

        for fpath in cls.__rng_paths:
            try:
                frng = None
                frng = tools.file_open(os.path.join(*fpath))
                relaxng_doc = etree.parse(frng)
                relaxng = etree.RelaxNG(relaxng_doc)
            finally:
                if frng:
                    frng.close()
            
            cls.__validator = relaxng
            break # until I implement the inheritance
    
    @classmethod
    def _unload_validator(cls):
        """Clears the validator from the memory
        
            The validator is cached. This will force it to unload, to save that
            memory
        """
        cls.__validator = None

    @classmethod
    def check_xml(cls, view_xml, view_type=None, errors=None):
        """ Check if view_xml is valid and conforms to view definition
        
            @params errors pass a list there and it will be filled with the
                RNG error messages
        """
        cls._load_validator()
        eview = None
        if isinstance(view_xml, unicode):
            eview = etree.fromstring(view_xml.encode('utf8'))
        elif isinstance(view_xml, str):
            eview = etree.fromstring(view_xml)
        elif isinstance(view_xml, etree.Element):
            eview = view_xml
        else:
            raise TypeError("Cannot use %s for view_xml" %type(view_xml))
        
        logger = logging.getLogger('init')
        relaxng = cls.__validator
        if not relaxng.validate(eview):
            for e in relaxng.error_log:
                eus = tools.ustr(e)
                logger.error(eus)
                if errors is not None:
                    errors.append(eus)
            return False
        return True

    def _default_view(self, cr, uid, obj, context=None):
        """ Devise a default view for `obj`
        """
        raise NotImplementedError

    @classmethod
    def get_default_view(cls, cr, uid, vtype, obj, context=None):
        """Retrieve the default view for type `vtype`
            @param obj the ORM model object
        """
        
        if vtype not in cls.__registry:
            raise KeyError("No view support for %s" % vtype)
        
        return cls.__registry[vtype]._default_view(cr, uid, obj, context=context)

    @classmethod
    def get_view_types(cls, obj, cr, uid, context=None):
        """for the ir.ui.view.type selection
        """
        ret = []
        for vtype, vclass in cls.__registry.items():
            ret.append((vtype, vclass._view_name))

        # if cr and uid and context: TODO
        #       # Try to translate them

        return ret

#eof
