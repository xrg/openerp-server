# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2010-2011 OpenERP SA. (www.openerp.com)
#    Copyright (C) 2008-2011 P. Christeas <xrg@hellug.gr>
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

#.apidoc title: Simple fields

""" Linear flields, with direct db storage
"""

from fields import _column, _symbol_set_long, _symbol_set_integer, \
    _symbol_set_float, register_field_classes

import datetime as DT
import tools
from tools.translate import _
import __builtin__

class boolean(_column):
    _type = 'boolean'
    _sql_type = 'bool'
    _symbol_c = '%s'
    _symbol_f = lambda x: x and 'True' or 'False'
    _symbol_set = (_symbol_c, _symbol_f)

class integer(_column):
    _type = 'integer'
    _sql_type = 'integer'
    _symbol_c = '%s'
    _symbol_f = _symbol_set_long
    _symbol_set = (_symbol_c, _symbol_f)
    _symbol_get = lambda self,x: x or 0

class integer_big(_column):
    _type = 'integer_big'
    _sql_type = 'bigint'
    # do not reference the _symbol_* of integer class, as that would possibly
    # unbind the lambda functions
    _symbol_c = '%s'
    _symbol_f = _symbol_set_integer
    _symbol_set = (_symbol_c, _symbol_f)
    _symbol_get = lambda self,x: x or 0

class _string_field(_column):
    """ Common baseclass for char and text fields
    """
    pass

class char(_string_field):
    """ Limited characters string type
        Like text, but have a size bound
    """
    _type = 'char'
    _sql_type = 'varchar'

    def __init__(self, string, size, **args):
        _string_field.__init__(self, string=string, size=size, **args)
        self._symbol_set = (self._symbol_c, self._symbol_set_char)

    # takes a string (encoded in utf8) and returns a string (encoded in utf8)
    def _symbol_set_char(self, symb):
        #TODO:
        # * we need to remove the "symb==False" from the next line BUT
        #   for now too many things rely on this broken behavior
        # * the symb==None test should be common to all data types
        if symb == None or symb == False:
            return None

        # we need to convert the string to a unicode object to be able
        # to evaluate its length (and possibly truncate it) reliably
        u_symb = tools.ustr(symb)

        return u_symb[:self.size].encode('utf8')

    def copy_copy(self, cr, uid, obj, id, f, data, context):
        """ Copies this string as "old-val (copy)"
        """
        return _("%s (copy)") % data[f]

class text(_string_field):
    _type = 'text'
    _sql_type = 'text'

class float(_column):
    _type = 'float'
    _sql_type = 'double precision'
    _symbol_c = '%s'
    _symbol_f = _symbol_set_float
    _symbol_set = (_symbol_c, _symbol_f)
    _symbol_get = None

    def __init__(self, string='unknown', digits=None, digits_compute=None, **args):
        _column.__init__(self, string=string, **args)
        self.digits = digits
        if digits or digits_compute:
            # with digits_compute, we are pretty sure we will need 'numeric',
            # but cannot tell the exact size at this stage
            self._sql_type = 'numeric'
        self.digits_compute = digits_compute


    def post_init(self, cr, name, obj):
        super(float, self).post_init(cr, name, obj)
        if self.digits_compute:
            t = self.digits_compute(cr)
            self._symbol_set=('%s', lambda x: ('%.'+str(t[1])+'f') % (__builtin__.float(x or 0.0),))
            self.digits = t
            self._sql_type = 'numeric'

class date(_column):
    _type = 'date'
    _sql_type = 'date'

    @staticmethod
    def today(*args):
        """ Returns the current date in a format fit for being a
        default value to a ``date`` field.

        This method should be provided as is to the _defaults dict, it
        should not be called.
        """
        return DT.date.today().strftime(
            tools.DEFAULT_SERVER_DATE_FORMAT)

class datetime(_column):
    _type = 'datetime'
    _sql_type = 'timestamp'

    @staticmethod
    def now(*args):
        """ Returns the current datetime in a format fit for being a
        default value to a ``datetime`` field.

        This method should be provided as is to the _defaults dict, it
        should not be called.
        """
        return DT.datetime.now().strftime(
            tools.DEFAULT_SERVER_DATETIME_FORMAT)

class time(_column):
    _type = 'time'
    _sql_type = 'time'

    @staticmethod
    def now( *args):
        """ Returns the current time in a format fit for being a
        default value to a ``time`` field.

        This method should be proivided as is to the _defaults dict,
        it should not be called.
        """
        return DT.datetime.now().strftime(
            tools.DEFAULT_SERVER_TIME_FORMAT)

register_field_classes(boolean, integer, integer_big, char, text,
        float, date, datetime, time)

#eof