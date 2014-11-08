# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2011-2014 P. Christeas <xrg@hellug.gr>
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

""" The 'C' component in openobject's MVC

    Fields:
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

import warnings
import xmlrpclib

import tools
from tools.translate import _
from tools import expr_utils as eu
import __builtin__
from tools import sql_model
from tools import orm_utils # must be the full module, not contents

def _symbol_set(symb):
    if symb is None or symb is False:
        return None
    #elif isinstance(symb, unicode):
    #    return symb.encode('utf-8')
    #return str(symb)
    return symb


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

def is_empty(val):
    """Checks if `val` is an empty record (aka. Null)

        This *won't* work for booleans, since False is considered empty
        for all other types!
    """
    if val is None:
        return True
    elif val is False:
        return True
    elif isinstance(val, orm_utils.browse_null):
        return True
    else:
        return True

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
    merge_op = False
    merge_param = None
    _SCALAR_OPS = ('=', '!=', '<>', '<', '<=', '>', '>=', 'in', 'not in')

    @classmethod
    def from_manual(cls, field_dict, attrs):
        """create a field instance for manual fields (from DB)

            @param field_dict the corresponding line in ir.model.fields
            @param attrs pre-processed attributes from that line, may
                    be passed directly to class constructor
        """
        return cls(**attrs)

    def __init__(self, string='unknown', required=False, readonly=False,
                    domain=None, context=None, states=None, priority=0,
                    change_default=False, size=None, ondelete=None,
                    translate=False, select=False, **args):
        """
            @param string The human-readable name of the field
            @param required won't allow the field to be saved empty
            @param readonly will not allow user to edit the field
            @param translate will convert the field to a translatable string
            @param select If true, will have an index on the field
            @param merge_op control behavior of the field when merging records:
                False means forbid the merge.
                'or' use any non-empty value, require that one exists
                'xor' use one non-empty value, require others to be empty
                'eq' require all records to have the same value
                'min' use minimum of all values
                'max' use maximum of all values
                'any' use any non-empty value. Similar to 'or', but no requirement
                'join' concatenate strings, lists. /May/ suppress identical ones
                'sum' arithmetic sum. May behave like 'join' for non-scalar
                'mustbe' require all records to have a specific value
                callable: call that fn TODO
            @param merge_param Params to some merge operators
            @param split_op Callable when split is requested TODO think about it
        """
        if domain is None:
            domain = []
        if context is None:
            context = {}
        self.states = states or {}
        self.string = string
        self.readonly = readonly
        self.required = required
        self.size = size
        self.help = args.pop('help', '')
        self.priority = priority
        self.change_default = change_default
        self.ondelete = ondelete or (required and "restrict") or "set null"
        self.translate = translate
        self._domain = domain
        self._context = context
        self.read = False
        self.view_load = 0
        self.select = select
        self.selectable = True
        self.group_operator = args.pop('group_operator', False)
        self._split_op = args.pop('split_op', None)
        if 'write' in args:
            raise AttributeError("Don't use 'write' any more!")
        for a in args:
            if args[a]:
                setattr(self, a, args[a])

    def post_init(self, cr, name, obj):
        """ Called when the ORM model `obj` is being initialized

            May involve any operations that are dependent on the db and/or
            the ORM model.
            In cases of fields that must be configured per-database, it may
            return a *copy* of this field (self), which will replace the
            original in the ORM model instance.
        """
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

        schema_table.column_or_renamed(name, getattr(self, 'oldname', None))

        r = schema_table.check_column(name, self._sql_type, not_null=self.required,
                default=self._sql_default_for(name,obj, context=context),
                select=self.select, size=self.size,
                references=False, comment=self.string)
        assert r

    def _sql_default_for(self, name, obj, context=None):
        """returns the default SQL value for this column, if available

            If this column has a scalar, stable, default, this will be returned.
            May also work for some special functions (like "now()")

            @param context may be passed to the default-computing function
        """

        obj_def = obj._defaults.get(name, None)
        if obj_def is None:
            return None
        elif callable(obj_def):
            if getattr(obj_def, 'no_auto', False):
                return None
            ss = self._symbol_set
            query = 'UPDATE "%s" SET "%s"=%s WHERE "%s" is NULL' % (obj._table, name, ss[0], name)
            prepare_fn = lambda cr: (ss[1](obj_def(obj, cr, 1, context)),)
            return sql_model.SQLCommand(query, prepare_fn=prepare_fn, debug=obj._debug)
        else:
            return self._symbol_set[1](obj_def)

    def _get_field_def(self, cr, uid, name, obj, ret, context=None):
        """ Fill 'ret' with field definition, for orm.fields_get()
        """
        for arg in ('string', 'readonly', 'states', 'size', 'required',
                    'group_operator', 'change_default', 'translate',
                    'help', 'select', 'selectable','digits', 'invisible',
                    'filters'):
            if getattr(self, arg, False):
                ret[arg] = getattr(self, arg)

    def _val2browse(self, val, name, parent_bro):
        """ Convert raw value to browse_record() format

            Only meaningful for non-scalar fields

            @return the value
            @param val input value
            @param name this field's name in parent object
            @param parent_bro container browse_record. Contains references
                    to cr, uid, context etc.
        """
        return val

    def _browse2val(self, bro, name):
        """ Convert browse value to scalar one
        """
        return bro

    def expr_eval(self, cr, uid, obj, lefts, operator, right, pexpr, context):
        """Evaluate an expression on this field

            This is the place where each field type can customize its behavior
            on domain expressions.

            Typically, the expression will be (lefts[0], operator, right) and
            must be processed here to provide another, compatible expression.

            @param obj the model on which the field belongs (applies)
            @param lefts a list of strings composing the left part. See below
            @param operator a string, the operator
            @param right any value to match against
            @param pexpr parent expression object calling us

            @return None if the input expression can be used as-is,
                tuple-3 for for a classic replacement expression or any
                expr_utils.sub_expr() object for complex evaluations

                Note1: list-3 is no longer allowed as the output of this fn
                Note2: False only means false. Return None for "empty", this
                    helps the next stage a lot.

            Use of lefts::

            In the simplest case, they contain only one element, the name of this
            field. Used to actually reference the field in the db.
            On relational fields, may be 'fld1_id','fld2' where it means to
            operate on `fld2` of the related table.
            On other fields, lefts[1] may be pseydo-modifiers that allow special
            expressions, like 'comment', 'len' meaning `len(comment)'.
        """
        assert len(lefts) == 1, lefts
        if right is False:
            if operator not in ('=', '!=', '<>'):
                raise eu.DomainInvalidOperator(obj, lefts, operator, right)
            return (lefts[0], operator, None)
        elif operator not in self._SCALAR_OPS:
            raise eu.DomainInvalidOperator(obj, lefts, operator, right)
        return None # as-is

    def _verify_model(self, dcol, mname, cname):
        """Verify that `dcol` is a _column like this one

            This is used to enforce rules of an abstact model on implementing
            ones. Any significant properties of this column should be checked
            against `dcol`.
        """

        if isinstance(dcol, globals()['function']):
            dcol = dcol._shadow

        if self._type != dcol._type:
            raise TypeError('Incorrect type %s.%s: not %s' % (mname, cname, self._type))

        if self.required and not dcol.required:
            raise TypeError('Column %s.%s must be set to required' % (mname, cname))

        #if self.size and (dcol.size > self.size):
        #    warning ...
        # etc..

    def calc_merge(self, cr, uid, obj, name, b_dest, b_src, context):
        """ Compute the merge of 2 records (id1, id2), return it or bork

        Try to find some value that would allow id_src to be merged into id_dest.
        Typically, id_dest will be lower.


        @param obj The parent orm object
        @param b_dest The object (browse record) of the record to keep
            May be None if id_src is the first record to consider
        @param b_src record to merge and discard
        @param name The name of the field
        @return The raw value, or None, if this field needs no change. If merge
            cannot proceed, should raise an exception
        """

        if self.merge_op == 'mustbe':
            if isinstance(self._merge_param, tuple):
                if b_src[name] not in self._merge_param:
                    raise ValueError(_("Cannot merge a record when %s = %r") % \
                        (name, b_src[name]))
            elif b_src[name] != self._merge_param:
                    raise ValueError(_("Cannot merge a record when %s = %r") % \
                        (name, b_src[name]))
            return None

        if not (b_dest and b_dest.get('id', False)):
            # the first iteration, save values and continue
            return b_src[name]

        elif callable(self.merge_op):
            return self.merge_op(obj, name, b_dest, b_src, context=context)
        else:
            if self.merge_op is True:
                return None
            elif self.merge_op is False:
                raise ValueError(_("Cannot merge %s, because %s field cannot merge") %\
                        (obj._name, name))
            elif self.merge_op == 'empty':
                if b_src[name]:
                    raise ValueError(_("Cannot merge because field %s is not empty.") % \
                        (name,))
            elif self.merge_op in ('any', 'or'):
                if b_dest[name]: # will also discard 0.0 value
                    return None
                elif not is_empty(b_src[name]): # here, 0.0 counts as valid
                    return b_src[name]
                elif self.merge_op == 'any':
                    return False
                else: # or
                    raise ValueError(_("Must have at least one value "
                                        "in field %s to proceed with merge of %s.") % \
                                        (name, obj._name)) # TODO translate them!

            elif self.merge_op == '+':
                if is_empty(b_src[name]):
                    return None
                elif is_empty(b_dest[name]):
                    return b_src[name]
                else:
                    return b_dest[name] + b_src[name]
            elif self.merge_op == 'min':
                if is_empty(b_src[name]):
                    return None
                elif is_empty(b_dest[name]):
                    return b_src[name]
                elif b_dest[name] > b_src[name]:
                    return b_src[name]
                else:
                    return None
            elif self.merge_op == 'max':
                if is_empty(b_src[name]):
                    return None
                elif is_empty(b_dest[name]):
                    return b_src[name]
                elif b_dest[name] < b_src[name]:
                    return b_src[name]
                else:
                    return None
            elif self.merge_op == 'eq':
                if (not b_dest[name]) and ( not b_src[name]):
                    # both are empty
                    return None
                elif b_dest[name] and b_src[name] and (b_dest[name] == b_src[name]):
                    return None
                else:
                    raise ValueError(_("All records must have an equal %s, in order to merge") % \
                            (name,))
            elif self.merge_op == '|eq':
                if not b_dest[name]:
                    return b_src[name]
                elif not b_src[name]:
                    return None
                elif b_dest[name] == b_src[name]:
                    return None
                else:
                    raise ValueError(_("All records must have an equal %s, in order to merge") % \
                            (name,))
            else:
                raise NotImplementedError("Why does %s need to merge as %s?"  % \
                    (self.__class__.__name__, self.merge_op))

    def calc_group(self, cr, uid, obj, lefts, right, context):
        """ Calculate SQL columns for read_group()

            @param obj the parent model
            @param lefts list of strings for the left part, like in `expr_eval()`
            @param right string, aggregate function expression. It is entirely up
                    to this field to parse it

            @return (key, dict)
                [* table: table string] in expression
                * group_by: group-by expression
                * order_by: ORDER BY expression
                * field_expr : SQL field expression (display)
                * field_aggr : SQL aggregate expression
                * field_post: field post-processing python function
                * field_aggr_post: groupped aggregate post-processing function
                * domain_fn: function on data row, returning domain for that
                        cumulative group
        """
        return None, None

def get_nice_size(a):
    (x,y) = a
    if isinstance(y, (int,long)):
        size = y
    elif y:
        size = len(y)
    else:
        size = 0
    return (x, tools.human_size(size))

def sanitize_binary_value(dict_item):
    """ Binary fields should be 7-bit ASCII base64-encoded data,
        but we do additional sanity checks to make sure the values
        are not something else that won't pass via XML-RPC
    """
    index, value = dict_item
    if isinstance(value, (xmlrpclib.Binary, tuple, list, dict)):
        # these builtin types are meant to pass untouched
        return index, value

    # For all other cases, handle the value as a binary string:
    # it could be a 7-bit ASCII string (e.g base64 data), but also
    # any 8-bit content from files, with byte values that cannot
    # be passed inside XML!
    # See for more info:
    #  - http://bugs.python.org/issue10066
    #  - http://www.w3.org/TR/2000/REC-xml-20001006#NT-Char
    #
    # One solution is to convert the byte-string to unicode,
    # so it gets serialized as utf-8 encoded data (always valid XML)
    # If invalid XML byte values were present, tools.ustr() uses
    # the Latin-1 codec as fallback, which converts any 8-bit
    # byte value, resulting in valid utf-8-encoded bytes
    # in the end:
    #  >>> unicode('\xe1','latin1').encode('utf8') == '\xc3\xa1'
    # Note: when this happens, decoding on the other endpoint
    # is not likely to produce the expected output, but this is
    # just a safety mechanism (in these cases base64 data or
    # xmlrpc.Binary values should be used instead)
    return index, tools.ustr(value)

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

class column_info(object):
    """Struct containing details about an osv column, either one local to
       its model, or one inherited via _inherits.

       :attr name: name of the column
       :attr column: column instance, subclass of osv.fields._column
       :attr parent_model: if the column is inherited, name of the model
                           that contains it, None for local columns.
       :attr parent_column: the name of the column containing the m2o
                            relationship to the parent model that contains
                            this column, None for local columns.
       :attr original_parent: if the column is inherited, name of the original
                            parent model that contains it i.e in case of multilevel
                            inheritence, None for local columns.
    """
    def __init__(self, name, column, parent_model=None, parent_column=None, original_parent=None):
        self.name = name
        self.column = column
        self.parent_model = parent_model
        self.parent_column = parent_column
        self.original_parent = original_parent

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

