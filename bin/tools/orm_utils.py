# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2011,2012 P. Christeas <xrg@hellug.gr>
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

#.apidoc title: Utility functions for ORM classes

"""These functions closely accompany ORM classes

    However, since we don't want to create unwanted imports of orm.py, we put
    these extra functions here
"""

browse_record_list = None # MUST be set by orm.py!
browse_record_null = None

def only_ids(ids):
    """ Return the list of ids from either a browse_record_list or plain list
    """
    if isinstance(ids, browse_record_list):
        return [ id._id for id in ids]
    else:
        return ids

# fields(copy_data) helpers:

def copy_false(*args, **kw):
    """Empty value, for scalar fields """
    return False

def copy_empty(*args, **kw):
    """Empty set, for x2many fields """
    return []

def copy_value(value):
    """Use this default value. Will yield a function"""
    return lambda *a, **kw: value

def copy_default(self, cr, uid, obj, id, f, data, context):
    """Use the default value at copying
    
        So far, this method uses *only* the value of _defaults. It does
        not consult ir.values or so.
    """
    val = obj._defaults.get(f, NotImplemented)
    if val is NotImplemented:
        raise KeyError("At %s.%s copy_default is specified, but no value in _defaults!" %\
                        (obj._name,f))
    if callable(val):
        return val(obj, cr, uid, context)
    else:
        return val

#eof
