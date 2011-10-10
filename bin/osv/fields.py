# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
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

#.apidoc title: Fields for ORM models

""" Fields:
      - simple
      - relations (one2many, many2one, many2many)
      - function

    Fields Attributes:
        * _classic_read: is a classic sql fields
        * _type   : field type
        * readonly
        * required
        * size
"""

import base64
import re
import warnings
import xmlrpclib

import tools
from tools.translate import _
import __builtin__

def _symbol_set(symb):
    if symb is None or symb is False:
        return None
    elif isinstance(symb, unicode):
        return symb.encode('utf-8')
    return str(symb)


def _symbol_set_float(symb):
    if symb is None or symb is False:
        return None
    elif symb is '':
        warnings.warn("You passed empty string as value to a float",
                      DeprecationWarning, stacklevel=4)
        return None
    return __builtin__.float(symb)

def _symbol_set_integer(symb):
    if symb is None or symb is False:
        return None
    elif symb is '':
        warnings.warn("You passed empty string as value to an integer",
                      DeprecationWarning, stacklevel=4)
        return None
    return int(symb)

def _symbol_set_long(symb):
    if symb is None or symb is False:
        return None
    elif symb is '':
        warnings.warn("You passed empty string as value to a long integer",
                      DeprecationWarning, stacklevel=4)
        return None
    #elif not isinstance(symb, (basestring, int, long, float)):
    #    raise ValueError("Why passed %r to _symbol_set_long?" % symb)
    return long(symb)


class _column(object):
    """ Base of all fields, a database column
    
        An instance of this object is a *description* of a database column. It will
        not hold any data, but only provide the methods to manipulate data of an
        ORM record or even prepare/update the database to hold such a field of data.
    """
    _classic_read = True
    _classic_write = True
    _prefetch = True
    _properties = False
    _type = 'unknown'
    _obj = None
    _multi = False
    _sql_type = None #: type of sql column to be created. Leave empty if you redefine _auto_init_sql
    _symbol_c = '%s'
    _symbol_f = _symbol_set
    _symbol_set = (_symbol_c, _symbol_f)
    _symbol_get = None

    def __init__(self, string='unknown', required=False, readonly=False,
                    domain=None, context=None, states=None, priority=0,
                    change_default=False, size=None, ondelete=None,
                    translate=False, select=False, **args):
        # TODO docstring
        if domain is None:
            domain = []
        if context is None:
            context = {}
        self.states = states or {}
        self.string = string
        self.readonly = readonly
        self.required = required
        self.size = size
        self.help = args.get('help', '')
        self.priority = priority
        self.change_default = change_default
        self.ondelete = ondelete or (required and "restrict") or "set null"
        self.translate = translate
        self._domain = domain
        self._context = context
        self.write = False
        self.read = False
        self.view_load = 0
        self.select = select
        self.selectable = True
        self.group_operator = args.get('group_operator', False)
        for a in args:
            if args[a]:
                setattr(self, a, args[a])

    def restart(self):
        pass

    def set(self, cr, obj, id, name, value, user=None, context=None):
        cr.execute('update '+obj._table+' set '+name+'='+self._symbol_set[0]+' where id=%s', (self._symbol_set[1](value), id), debug=obj._debug)

    def set_memory(self, cr, obj, id, name, value, user=None, context=None):
        raise Exception(_('Not implemented %s.%s.set_memory method !') %(obj._name, name))

    def get_memory(self, cr, obj, ids, name, user=None, context=None, values=None):
        raise Exception(_('Not implemented %s.%s.get_memory method !')%(obj._name, name))

    def get(self, cr, obj, ids, name, user=None, offset=0, context=None, values=None):
        raise Exception(_('undefined %s.%s.get method ! (%s)')%(obj._name, name, type(self)))

    def search(self, cr, obj, args, name, value, offset=0, limit=None, uid=None, context=None):
        res = obj.search_read(cr, uid, domain=args+self._domain+[(name, 'ilike', value)],
            offset=offset, limit=limit, fields=[name], context=context)
        return [x[name] for x in res]

    def search_memory(self, cr, obj, args, name, value, offset=0, limit=None, uid=None, context=None):
        raise Exception(_('Not implemented %s.%s.search_memory method !')%(obj._name, name))


    def __copy_data_template(self, cr, uid, obj, id, f, data, context):
        """ Sample function for copying the data of this column.
        If some column needs to override the conventional copying of
        its data, it should define a copy_data() like this function.
        
        @param obj The parent orm object
        @param id  The id of the record in the parent orm object
        @param f The name of the field
        @param data The current data of the object. It may be the data of
                the source object (for the later fields) or the copied data
                for the fields that have been already computed
        @return The raw value, or None, if this field should not be set.
        
        Note: please respect the pythonic need to bind the function to an
        object. Functions outside this object are permitted on the constructor
        of the field, thus binding is not implied. For convenience, instead
        of calling the constructor with the function argument, please call
        it with the string name of the function, if it requires binding:
        @code
            def my_copy_fn(cr, uid, ...): pass
            _columns={ 'sfield': fields.char('aaa', copy_data=my_copy_fn) }
                # OK, because my_copy_fn is unbound
            
            def copy_fn(self, cr, uid): pass
            _columns={ 'sfield': fields.char('aaa', copy_data=copy_fn) }
              # Wrong: copy_fn should have been bound
            _columns={ 'sfield': fields.char('aaa', copy_data='copy_fn') }
              # OK, the string will be bound.
        """
        return None

    def _auto_init_prefetch(self, name, obj, prefetch_schema, context=None):
        """Populate schema's hints with tables to fetch from SQL

            Override this fn. for relational fields
        """
        pass

    def _auto_init_sql(self, name, obj, schema_table, context=None):
        """ Update this column in in schema_table

            Here, the database schema is _virtually_ updated to fit
            this column. (real DB actions may be deferred)

            @param name Name of this column in obj
            @param obj the parent ORM model
            @param schema_table an sql_model Table() instance

            @return ?? todo actions?
        """

        if not self._sql_type:
            raise NotImplementedError("Why called _auto_init_sql() on %s (%s.%s) ?" % \
                    (self.__class__.__name__, obj._name, name))

        col = schema_table.column_or_renamed(name, getattr(self, 'oldname', None))

        r = schema_table.check_column(name, self._sql_type, not_null=self.required,
                default=self._sql_default_for(name,obj), select=self.select, size=self.size,
                references=False, comment=self.string)
        assert r

    def _sql_default_for(self, name, obj):
        """returns the default SQL value for this column, if available

            If this column has a scalar, stable, default, this will be returned.
            May also work for some special functions (like "now()")
        """

        obj_def = obj._defaults.get(name, None)
        if obj_def is None:
            return None
        elif callable(obj_def):
            return None
        else:
            return self._symbol_set[1](obj_def)

def get_nice_size(a):
    (x,y) = a
    if isinstance(y, (int,long)):
        size = y
    elif y:
        size = len(y)
    else:
        size = 0
    return (x, tools.human_size(size))

# See http://www.w3.org/TR/2000/REC-xml-20001006#NT-Char
# and http://bugs.python.org/issue10066
invalid_xml_low_bytes = re.compile(r'[\x00-\x08\x0b-\x0c\x0e-\x1f]')

def sanitize_binary_value(dict_item):
    """ Binary fields should be 7-bit ASCII base64-encoded data,
        but we do additional sanity checks to make sure the values
        are not something else that won't pass via xmlrpc
    """
    index, value = dict_item
    if isinstance(value, (xmlrpclib.Binary, tuple, list, dict)):
        # these builtin types are meant to pass untouched
        return index, value

    # Handle invalid bytes values that will cause problems
    # for XML-RPC. See for more info:
    #  - http://bugs.python.org/issue10066
    #  - http://www.w3.org/TR/2000/REC-xml-20001006#NT-Char

    # Coercing to unicode would normally allow it to properly pass via
    # XML-RPC, transparently encoded as UTF-8 by xmlrpclib.
    # (this works for _any_ byte values, thanks to the fallback
    #  to latin-1 passthrough encoding when decoding to unicode)
    value = tools.ustr(value)

    # Due to Python bug #10066 this could still yield invalid XML
    # bytes, specifically in the low byte range, that will crash
    # the decoding side: [\x00-\x08\x0b-\x0c\x0e-\x1f]
    # So check for low bytes values, and if any, perform
    # base64 encoding - not very smart or useful, but this is
    # our last resort to avoid crashing the request.
    if invalid_xml_low_bytes.search(value):
        # b64-encode after restoring the pure bytes with latin-1
        # passthrough encoding
        value = base64.b64encode(value.encode('latin-1'))

    return index, value

def register_field_classes(*args):
    """ register another module's class as if it were defined here, in fields.py
    
        Used so that field classes defined elsewhere can appear as if they
        were originally included in this module (like the 6.0 days)
        
        Use like::
        
            register_field_classes(boolean, many2one, selection)
    """
    
    for klass in args:
        assert issubclass(klass, _column), klass
        assert klass.__name__ not in globals(), klass.__name__
        globals()[klass.__name__] = klass

def register_any_classes(*args):
    """ register another module's class as if it were defined here, in fields.py
    
        Like register_field_classes, but accepts any class
        Use with care!
    """
    
    for klass in args:
        assert klass.__name__ not in globals(), klass.__name__
        globals()[klass.__name__] = klass

def get_field_class(clname):
    """ Returns fields.clname class

        Useful for dynamically retrieving any of the available classes,
        without the need to import it directly
    """
    if clname not in globals():
        raise NameError("fields.%s is not registered" % clname)
    return globals()[clname]

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

