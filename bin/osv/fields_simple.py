# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2010-2011 OpenERP SA. (www.openerp.com)
#    Copyright (C) 2008-2014 P. Christeas <xrg@hellug.gr>
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

""" Linear fields, with direct db storage
"""

from fields import _column, _symbol_set_long, _symbol_set_integer, \
    _symbol_set_float, register_field_classes

import datetime as DT
import tools
import re
from tools.translate import _
import __builtin__
from tools import expr_utils as eu
from tools.misc import to_date, to_datetime, to_time
from tools.date_eval import lazy_date_eval

class boolean(_column):
    _type = 'boolean'
    _sql_type = 'bool'
    _symbol_c = '%s'
    _symbol_f = lambda x: x and True or False
    _symbol_set = (_symbol_c, _symbol_f)
    merge_op = 'eq'

    def expr_eval(self, cr, uid, obj, lefts, operator, right, pexpr, context):
        if operator not in ('=', '!=', '<>','==', '!=='):
            raise eu.DomainInvalidOperator(obj, lefts, operator, right)
        assert len(lefts) == 1, lefts

        if (not right) and (operator == '='):
            return eu.nested_expr(['|', (lefts[0], '=', None), (lefts[0], '=', False)])
        elif (not right) and (operator in ('<>', '!=')):
            return (lefts[0], '=', True)
        elif (right is None) and (operator in ('==', '!==')):
            return (lefts[0], operator[:-1], None)
        elif (operator in ('==', '!==')):
            return (lefts[0], operator[:-1], True)
        elif right and operator == '!=':
            return eu.nested_expr(['|', (lefts[0], '=', None), (lefts[0], '=', False)])
        else:
            return (lefts[0], operator, bool(right))
        return None # as-is

    def calc_merge(self, cr, uid, obj, name, b_dest, b_src, context):
        if b_dest :
            if self.merge_op == 'empty':
                if b_src[name]:
                    raise ValueError(_("Cannot merge because field %s is not empty.") % \
                        (name,))
            elif self.merge_op in ('any', 'or'):
                if b_dest[name]: # will also discard 0.0 value
                    return None
                elif b_src[name] is not None:
                    return b_src[name]
                elif self.merge_op == 'any':
                    return False
                else: # or
                    raise ValueError(_("Must have at least one value "
                                        "in field %s to proceed with merge of %s.") % \
                                        (name, obj._name)) # TODO translate them!
            elif self.merge_op == 'and':
                return bool(b_dest[name] and b_src[name])

        return super(boolean, self).calc_merge(cr, uid, obj, name=name, b_dest=b_dest, b_src=b_src, context=context)

class integer(_column):
    _type = 'integer'
    _sql_type = 'integer'
    _symbol_c = '%s'
    _symbol_f = _symbol_set_long
    _symbol_set = (_symbol_c, _symbol_f)
    _symbol_get = lambda self,x: x or 0

    def calc_group(self, cr, uid, obj, lefts, right, context):
        if len(lefts) > 1:
            raise NotImplementedError("Cannot use %s yet" % ('.'.join(lefts)))
        full_field = '"%s".%s' % (obj._table, lefts[0])
        if right is True:
            right = self.group_operator or 'sum'
        if isinstance(right, basestring) and right.lower() in ('min', 'max', 'sum', 'avg', 'count', 'stddev', 'variance'):
            aggregate = '%s(%s)' % (right.upper(), lefts[0])
        else:
            raise ValueError("Invalid aggregate function: %r", right)
        return '.'.join(lefts), { 'group_by': full_field, 'order_by': full_field,
                'field_expr': full_field, 'field_aggr': aggregate }


class integer_big(integer):
    _type = 'integer_big'
    _sql_type = 'bigint'
    # do not reference the _symbol_* of integer class, as that would possibly
    # unbind the lambda functions
    _symbol_c = '%s'
    _symbol_f = _symbol_set_integer
    _symbol_set = (_symbol_c, _symbol_f)

class _string_field(_column):
    """ Common baseclass for char and text fields
    """
    STRING_OPS = ('=', '!=', '<>', '<', '<=', '>', '>=', '=?',
                    '~', '!~',
                    'like', 'ilike', 'not like', 'not ilike',
                    '=like', '=ilike',
                    'in', 'not in')

    def _ext_length(self, cr, uid, obj, lefts, operator, right):
        if len(lefts) != 2:
            raise eu.DomainLeftError(obj, lefts, operator, right)
        if operator not in ('=', '!=', '<>', '<', '<=', '>', '>='):
            raise eu.DomainInvalidOperator(obj, lefts, operator, right)
        return eu.function_expr('char_length(%s)', lefts[0], operator, right)

    def _ext_chgcase(self, cr, uid, obj, lefts, operator, right):
        if operator not in ('=', '!=', '<>', 'like', '=like', '~', '!~'):
            raise eu.DomainInvalidOperator(obj, lefts, operator, right)
        if operator == 'like':
            right = '%%%s%%' % right
        elif operator == '=like':
            operator = 'like'
        func = lefts[1]
        if lefts[1] == 'title':
            func = 'initcap'
        return eu.function_expr(func +'(%s)', lefts[0], operator, right)

    extension_fns = { 'len': _ext_length, 'length': _ext_length,
                    'upper': _ext_chgcase, 'lower': _ext_chgcase,
                    'title': _ext_chgcase}

    def expr_eval(self, cr, uid, obj, lefts, operator, right, pexpr, context):
        """ transform the expression, in case this field is translatable
        """
        if self.translate and context and 'lang' in context:
            assert len(lefts) == 1, lefts # we don't support anything else yet
            if operator in ('like', 'ilike', 'not like', 'not ilike'):
                right = '%%%s%%' % right
            elif operator in ('=like', '=ilike'):
                operator = operator[1:]
            elif operator not in self.STRING_OPS:
                raise eu.DomainInvalidOperator(obj, lefts, operator, right)

            query1 = '( SELECT res_id'          \
                        '    FROM ir_translation'  \
                        '   WHERE name = %s'       \
                        '     AND lang = %s'       \
                        '     AND type = %s'
            instr = ' %s'
            #Covering in,not in operators with operands (%s,%s) ,etc.
            if operator in ['in','not in']:
                instr = ','.join(['%s'] * len(right))
                query1 += '     AND value ' + operator +  ' ' +" (" + instr + ")"   \
                        ') UNION ('                \
                        '  SELECT id'              \
                        '    FROM "' + obj._table + '"'       \
                        '   WHERE "' + lefts[0] + '" ' + operator + ' ' +" (" + instr + "))"
                right = list(right)
            else:
                query1 += '     AND value ' + operator + instr +   \
                        ') UNION ('                \
                        '  SELECT id'              \
                        '    FROM "' + obj._table + '"'       \
                        '   WHERE "' + lefts[0] + '" ' + operator + instr + ")"
                right = [right,]

            query2 = [obj._name + ',' + lefts[0],
                        context.get('lang', False) or 'en_US',
                        'model',
                        ] + right + right

            return ('id', 'inselect', (query1, query2))
        elif len(lefts) > 1:
            if lefts[1] in self.extension_fns:
                fn = self.extension_fns[lefts[1]]
                return fn(self, cr, uid, obj, lefts, operator, right)
            else:
                raise eu.DomainLeftError(obj, lefts, operator, right)
        else:
            if right is False:
                if operator not in ('=', '!=', '<>'):
                    raise eu.DomainInvalidOperator(obj, lefts, operator, right)
                return (lefts[0], operator, None)
            if operator not in self.STRING_OPS:
                raise eu.DomainInvalidOperator(obj, lefts, operator, right)
            return None

    def calc_merge(self, cr, uid, obj, name, b_dest, b_src, context):
        """
            For text fields, 'join' merge-op means to concatenate the text,
            *if* the text is different. `merge_param` may contain the delimiter
        """
        if b_dest:
            if self.merge_op == 'join':
                if not b_src[name]:
                    return None
                elif not b_dest.get(name, False):
                    return b_src[name]
                else:
                    if isinstance(self.merge_param, basestring):
                        # also works for empty string
                        delim = self.merge_param
                    else:
                        delim = ' '
                    if b_dest[name] == b_src[name]:
                        return None
                    else:
                        return b_dest[name] + delim + b_src[name]
        return super(_string_field, self).calc_merge(cr, uid, obj, name, b_dest=b_dest, b_src=b_src, context=context)

    def calc_group(self, cr, uid, obj, lefts, right, context):
        if len(lefts) > 1:
            raise NotImplementedError("Cannot use %s yet" % ('.'.join(lefts)))
        full_field = '"%s".%s' % (obj._table, lefts[0])
        if right is True:
            right = self.group_operator or 'count'
        if isinstance(right, basestring) and right.lower() in ('min', 'max', 'array_agg', 'count'):
            aggregate = '%s(%s)' % (right.upper(), lefts[0])
        else:
            raise ValueError("Invalid aggregate function: %r", right)
        return '.'.join(lefts), { 'group_by': full_field, 'order_by': full_field,
                'field_expr': full_field, 'field_aggr': aggregate }

class char(_string_field):
    """ Limited characters string type
        Like text, but have a size bound
    """
    _type = 'char'
    _sql_type = 'varchar'
    merge_op = 'eq'

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

    _copy_numbered_re = re.compile(r'\(([0-9]+)\) *$')

    def copy_numbered(self, cr, uid, obj, id, f, data, context):
        m = self._copy_numbered_re.search(data[f])
        if m:
            num = int(m.group(1))
            s,e = m.span(1)
            return m.string[:s] + str(num+1) + m.string[e:]
        else:
            return data[f] + ' (1)'

class text(_string_field):
    _type = 'text'
    _sql_type = 'text'
    merge_op = 'join'
    merge_param = '\n'

class float(_column):
    _type = 'float'
    _sql_type = 'double precision'
    _symbol_c = '%s'
    _symbol_f = _symbol_set_float
    _symbol_set = (_symbol_c, _symbol_f)
    _symbol_get = None
    merge_op = 'eq'

    def __init__(self, string='unknown', digits=None, digits_compute=None, **args):
        _column.__init__(self, string=string, **args)
        self.digits = digits
        if digits or digits_compute:
            # with digits_compute, we are pretty sure we will need 'numeric',
            # but cannot tell the exact size at this stage
            self._sql_type = 'numeric'
        self.digits_compute = digits_compute

    def copy(self):
        kw = {}
        for k, v in self.__dict__.items():
            if k.startswith('__'):
                continue
            if k.startswith('_'):
                kw[k[1:]] = v
            else:
                kw[k] = v
        return self.__class__(**kw)

    def post_init(self, cr, name, obj):
        super(float, self).post_init(cr, name, obj)
        if self.digits_compute:
            nf = self.copy()
            nf._sql_type = 'numeric'
            nf.digits_change(cr)
            return nf

    def digits_change(self, cr):
        if self.digits_compute:
            t = self.digits_compute(cr)
            def __sset(x):
                if x is None or x is False:
                    return None
                # TODO Decimal
                if isinstance(x, basestring):
                    x = __builtin__.float(x)
                return __builtin__.round(x, t[1])
            self._symbol_set=('%s', __sset)
            self.digits = t

    def calc_group(self, cr, uid, obj, lefts, right, context):
        if len(lefts) > 1:
            raise NotImplementedError("Cannot use %s yet" % ('.'.join(lefts)))
        full_field = '"%s".%s' % (obj._table, lefts[0])
        if right is True:
            right = self.group_operator or 'sum'
        if isinstance(right, basestring) and right.lower() in ('min', 'max', 'sum', 'avg', 'count', 'stddev', 'variance'):
            aggregate = '%s(%s)' % (right.upper(), lefts[0])
        else:
            raise ValueError("Invalid aggregate function: %r", right)
        return '.'.join(lefts), { 'group_by': full_field, 'order_by': full_field,
                'field_expr': full_field, 'field_aggr': aggregate }

def _date_domain(field_expr, group_trunc, out_fmt):
    leval = lazy_date_eval(' +1%s' % group_trunc)
    return lambda row: [(field_expr, '>=', row[field_expr]), \
                        (field_expr, '<', leval(cur_time=to_datetime(row[field_expr])))]

class _date_column_mixin:
    def calc_group(self, cr, uid, obj, lefts, right, context):
        """ Calculate aggregates for date/time fields
        """
        group_trunc = 'day'
        if len(lefts) > 2:
            raise NotImplementedError("Cannot use %s yet" % ('.'.join(lefts)))
        elif len(lefts) == 2:
            group_trunc = lefts[1]
        else:
            if context.get('mode_API') == '6.0':
                group_trunc = 'month'
        full_field = '"%s".%s' % (obj._table, lefts[0])
        if right is True:
            right = self.group_operator or 'min'
        if isinstance(right, basestring) and right.lower() in ('min', 'max'):
            aggregate = '%s(%s)' % (right.upper(), lefts[0])
        else:
            raise ValueError("Invalid aggregate function: %r" % right)
        ret = {'order_by': full_field, 'field_aggr': aggregate}
        ret_key = '.'.join(lefts)
        if group_trunc in self._group_trunc_periods:
            ret['group_by'] = "date_trunc('%s', %s) " % (group_trunc, full_field)
            ret['field_expr'] = ret['group_by'] + (':: %s' % self._sql_type)
            ret['domain_fn'] = _date_domain(ret_key, group_trunc, self._type)
        else:
            raise ValueError("Invalid group period: %s" % group_trunc)
        return ret_key, ret


class date(_date_column_mixin, _column):
    _type = 'date'
    _sql_type = 'date'
    merge_op = '|eq'
    _symbol_c = '%s'
    _symbol_f = to_date
    _symbol_set = (_symbol_c, _symbol_f)
    _group_trunc_periods = ('day', 'week', 'month', 'quarter', 'year', 'decade', 'century')
    _part_fns = {
            'day': 'extract(DAY FROM %s)',
            'decade': 'extract(DECADE FROM %s)',
            'dow': 'extract(DOW FROM %s)',
            'doy': 'extract(DOY FROM %s)',
            'epoch': 'extract(EPOCH FROM %s)',
            'month': 'extract(MONTH FROM %s)',
            'second': 'extract(SECOND FROM %s)',
            'week': 'extract(WEEK FROM %s)',
            'year': 'extract(YEAR FROM %s)',
        }

    @staticmethod
    def today(*args):
        """ Returns the current date in a format fit for being a
        default value to a ``date`` field.

        This method should be provided as is to the _defaults dict, it
        should not be called.
        """
        return DT.date.today()

    @staticmethod
    def lazy_eval(estr):
        """ Returns a callable that performs date_eval(estr) for a date

            Example usage is in column _defaults , like::

                _defaults = { 'date': lazy_eval('yesterday'), }
        """
        return lazy_date_eval(estr, out_fmt='date')

    def expr_eval(self, cr, uid, obj, lefts, operator, right, pexpr, context):
        """ Adds less-than, greater-than support
        
            Since the datetime API, also supports lefts like .month, .day etc!
        """
        if len(lefts) == 2:
            fn = self._part_fns.get(lefts[1], None)
            if fn is None:
                raise eu.DomainLeftError(obj, lefts, operator, right)
            if operator not in ('=', '!=', '<>'):
                raise eu.DomainInvalidOperator(obj, lefts, operator, right)
            return  eu.function_expr(fn, lefts[0], operator, right)
        elif len(lefts) == 1:
            if right is False:
                if operator not in ('=', '!=', '<>'):
                    raise eu.DomainInvalidOperator(obj, lefts, operator, right)
                return (lefts[0], operator, None)
            elif operator not in self._SCALAR_OPS:
                raise eu.DomainInvalidOperator(obj, lefts, operator, right)
            else:
                return None
        else:
            raise eu.DomainLeftError(obj, lefts, operator, right)

class datetime(_date_column_mixin, _column):
    _type = 'datetime'
    _sql_type = 'timestamp'
    merge_op = '|eq'
    _symbol_c = '%s'
    _symbol_f = to_datetime
    _symbol_set = (_symbol_c, _symbol_f)
    _group_trunc_periods = ('second', 'minute', 'hour', 'day', 'week',
                            'month', 'quarter', 'year', 'decade', 'century')

    _part_fns = {
            'day': 'extract(DAY FROM %s)',
            'decade': 'extract(DECADE FROM %s)',
            'dow': 'extract(DOW FROM %s)',
            'doy': 'extract(DOY FROM %s)',
            'epoch': 'extract(EPOCH FROM %s)',
            'hour': 'extract(HOUR FROM %s)',
            'milliseconds': 'extract(MILLISECONDS FROM %s)',
            'month': 'extract(MONTH FROM %s)',
            'minute': 'extract(MINUTE FROM %s)',
            'second': 'extract(SECOND FROM %s)',
            'week': 'extract(WEEK FROM %s)',
            'year': 'extract(YEAR FROM %s)',
        }

    @staticmethod
    def now(*args):
        """ Returns the current datetime in a format fit for being a
        default value to a ``datetime`` field.

        This method should be provided as is to the _defaults dict, it
        should not be called.
        """
        return DT.datetime.now()

    @staticmethod
    def lazy_eval(estr):
        """ Returns a callable that performs date_eval(estr)

            Example usage is in column _defaults , like::

                _defaults = { 'cur_tstamp': lazy_eval('now -5min'), }
        """
        return lazy_date_eval(estr)

    def expr_eval(self, cr, uid, obj, lefts, operator, right, pexpr, context):
        """ In order to keep the 5.0/6.0 convention, we consider timestamps
            to match the full day of some date, eg:
                ( '2011-05-30 13:30:00' < '2011-05-30')

            Since the datetime API, also supports lefts like .month, .day etc!
        """
        if len(lefts) == 2:
            fn = self._part_fns.get(lefts[1], None)
            if fn is None:
                raise eu.DomainLeftError(obj, lefts, operator, right)
            if operator not in ('=', '!=', '<>'):
                raise eu.DomainInvalidOperator(obj, lefts, operator, right)
            return  eu.function_expr(fn, lefts[0], operator, right)
        elif len(lefts) == 1:
            if right and isinstance(right, basestring) and len(right) < 11:
                if operator in ('<', '<='):
                    return (lefts[0], operator, right + ' 23:59:59')
                elif operator in ('>', '>='):
                    return (lefts[0], operator, right + ' 00:00:00')
            elif right is False:
                if operator not in ('=', '!=', '<>'):
                    raise eu.DomainInvalidOperator(obj, lefts, operator, right)
                return (lefts[0], operator, None)
            elif operator not in self._SCALAR_OPS:
                raise eu.DomainInvalidOperator(obj, lefts, operator, right)
            else:
                return None
        else:
            raise eu.DomainLeftError(obj, lefts, operator, right)

class time(_column):
    _type = 'time'
    _sql_type = 'time'
    merge_op = '|eq'
    _symbol_c = '%s'
    _symbol_f = to_time
    _symbol_set = (_symbol_c, _symbol_f)

    @staticmethod
    def now( *args):
        """ Returns the current time in a format fit for being a
        default value to a ``time`` field.

        This method should be proivided as is to the _defaults dict,
        it should not be called.
        """
        return DT.datetime.now().time()

    @staticmethod
    def lazy_eval(estr):
        """ Returns a callable that performs date_eval(estr) for a time

            Example usage is in column _defaults , like::

                _defaults = { 'dtime': lazy_eval('-1hour'), }
        """
        return lazy_date_eval(estr, out_fmt='time')

register_field_classes(boolean, integer, integer_big, char, text,
        float, date, datetime, time)

#eof