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

#.apidoc title: Function fields

""" Fields that do not directly store into the DB, but compute their
    value instead
"""
# TODO move the story here.

from fields import _column, get_nice_size, sanitize_binary_value, \
    register_field_classes, get_field_class
#from tools.translate import _
import logging
import __builtin__
from fields_simple import boolean, integer, float
from tools import config
from tools import sql_model
from tools.translate import _
from tools import expr_utils as eu

_logger = logging.getLogger('orm')

def _m2o_cmp(a, b):
    if a is False:
        if b:
            return -1
        return 0
    else:
        if not b:
            return 1
        elif isinstance(b, (int, long)):
            return cmp(a[0], b)
        elif isinstance(b, basestring):
            return cmp(a[1], b)
        else:
            # Arbitrary: unknown b is greater than all record values
            return -1

class function(_column):
    """
    A field whose value is computed by a function (rather
    than being read from the database).

    :param fnct: the callable that will compute the field value.
    :param arg: arbitrary value to be passed to ``fnct`` when computing the value.
    :param fnct_inv: the callable that will allow writing values in that field
                     (if not provided, the field is read-only).
    :param fnct_inv_arg: arbitrary value to be passed to ``fnct_inv`` when
                         writing a value.
    :param str type: type of the field simulated by the function field
    :param fnct_search: the callable that allows searching on the field
                        (if not provided, search will not return any result).
    :param store: store computed value in database
                  (see :ref:`The *store* parameter <field-function-store>`).
    :type store: True or dict specifying triggers for field computation
    :param multi: name of batch for batch computation of function fields.
                  All fields with the same batch name will be computed by
                  a single function call. This changes the signature of the
                  ``fnct`` callable.

    .. _field-function-fnct: The ``fnct`` parameter

    .. rubric:: The ``fnct`` parameter

    The callable implementing the function field must have the following signature:

    .. function:: fnct(model, cr, uid, ids, field_name(s), arg, context)

        Implements the function field.

        :param orm_template model: model to which the field belongs (should be ``self`` for
                                   a model method)
        :param field_name(s): name of the field to compute, or if ``multi`` is provided,
                              list of field names to compute.
        :type field_name(s): str | [str]
        :param arg: arbitrary value passed when declaring the function field
        :rtype: dict
        :return: mapping of ``ids`` to computed values, or if multi is provided,
                 to a map of field_names to computed values

    The values in the returned dictionary must be of the type specified by the type
    argument in the field declaration.

    Here is an example with a simple function ``char`` function field::

        # declarations
        def compute(self, cr, uid, ids, field_name, arg, context):
            result = {}
            # ...
            return result
        _columns['my_char'] = fields.function(compute, type='char', size=50)

        # when called with ``ids=[1,2,3]``, ``compute`` could return:
        {
            1: 'foo',
            2: 'bar',
            3: False # null values should be returned explicitly too
        }

    If ``multi`` is set, then ``field_name`` is replaced by ``field_names``: a list
    of the field names that should be computed. Each value in the returned
    dictionary must then be a dictionary mapping field names to values.

    Here is an example where two function fields (``name`` and ``age``)
    are both computed by a single function field::

        # declarations
        def compute(self, cr, uid, ids, field_names, arg, context):
            result = {}
            # ...
            return result
        _columns['name'] = fields.function(compute_person_data, type='char',\
                                           size=50, multi='person_data')
        _columns[''age'] = fields.function(compute_person_data, type='integer',\
                                           multi='person_data')

        # when called with ``ids=[1,2,3]``, ``compute_person_data`` could return:
        {
            1: {'name': 'Bob', 'age': 23},
            2: {'name': 'Sally', 'age': 19},
            3: {'name': 'unknown', 'age': False}
        }

    .. _field-function-fnct-inv:

    .. rubric:: The ``fnct_inv`` parameter

    This callable implements the write operation for the function field
    and must have the following signature:

    .. function:: fnct_inv(model, cr, uid, ids, field_name, field_value, fnct_inv_arg, context)

        Callable that implements the ``write`` operation for the function field.

        :param orm_template model: model to which the field belongs (should be ``self`` for
                                   a model method)
        :param str field_name: name of the field to set
        :param fnct_inv_arg: arbitrary value passed when declaring the function field
        :return: True

    When writing values for a function field, the ``multi`` parameter is ignored.

    .. _field-function-fnct-search:

    .. rubric:: The ``fnct_search`` parameter

    This callable implements the search operation for the function field
    and must have the following signature:

    .. function:: fnct_search(model, cr, uid, model_again, field_name, criterion, context)

        Callable that implements the ``search`` operation for the function field by expanding
        a search criterion based on the function field into a new domain based only on
        columns that are stored in the database.

        :param orm_template model: model to which the field belongs (should be ``self`` for
                                   a model method)
        :param orm_template model_again: same value as ``model`` (seriously! this is for backwards
                                         compatibility)
        :param str field_name: name of the field to search on
        :param list criterion: domain component specifying the search criterion on the field.
        :rtype: list
        :return: domain to use instead of ``criterion`` when performing the search.
                 This new domain must be based only on columns stored in the database, as it
                 will be used directly without any translation.

        The returned value must be a domain, that is, a list of the form [(field_name, operator, operand)].
        The most generic way to implement ``fnct_search`` is to directly search for the records that
        match the given ``criterion``, and return their ``ids`` wrapped in a domain, such as
        ``[('id','in',[1,3,5])]``.

    .. _field-function-store:

    .. rubric:: The ``store`` parameter

    The ``store`` parameter allows caching the result of the field computation in the
    database, and defining the triggers that will invalidate that cache and force a
    recomputation of the function field.
    When not provided, the field is computed every time its value is read.
    The value of ``store`` may be either ``True`` (to recompute the field value whenever
    any field in the same record is modified), or a dictionary specifying a more
    flexible set of recomputation triggers.

    A trigger specification is a dictionary that maps the names of the models that
    will trigger the computation, to a tuple describing the trigger rule, in the
    following form::

        store = {
            'trigger_model': (mapping_function,
                              ['trigger_field1', 'trigger_field2'],
                              priority),
        }

    A trigger rule is defined by a 3-item tuple where:

        * The ``mapping_function`` is defined as follows:

            .. function:: mapping_function(trigger_model, cr, uid, trigger_ids, context)

                Callable that maps record ids of a trigger model to ids of the
                corresponding records in the source model (whose field values
                need to be recomputed).

                :param orm_template model: trigger_model
                :param list trigger_ids: ids of the records of trigger_model that were
                                         modified
                :rtype: list
                :return: list of ids of the source model whose function field values
                         need to be recomputed

        * The second item is a list of the fields who should act as triggers for
          the computation. If an empty list is given, all fields will act as triggers.
        * The last item is the priority, used to order the triggers when processing them
          after any write operation on a model that has function field triggers. The
          default priority is 10.

    In fact, setting store = True is the same as using the following trigger dict::

        store = {
              'model_itself': (lambda self, cr, uid, ids, context: ids,
                               [],
                               10)
        }

    """
    _classic_read = False
    _classic_write = False
    _prefetch = False
    _type = 'function'
    _properties = True
    merge_op = True # ignore them, that is

    FALLBACK_OPS = {'=': lambda a, b: bool(a == b),
            '!=': lambda a, b: bool(a != b),
            '<>': lambda a, b: bool(a != b),
            '<=': lambda a, b: bool(a <= b),
            '<': lambda a, b: bool(a < b),
            '>': lambda a, b: bool(a > b),
            '>=': lambda a, b: bool(a >= b),
            '=?': lambda a, b: b is None or b is False or bool(a == b),
            #'=like': lambda a, b: , need regexp?
            #'=ilike': lambda a, b: ,
            'like': lambda a, b: bool(b in a),
            'not like': lambda a, b: bool(b not in a),
            'ilike': lambda a, b: (not b) or (a and bool(b.lower() in a.lower())),
            'not ilike': lambda a, b: b and ((not a) or bool(b.lower() not in a.lower())),
            'in': lambda a, b: bool(a in b),
            'not in': lambda a, b: bool(a not in b),
            }

    FALLBACK_OPS_M2O = {'=': lambda a, b: _m2o_cmp(a,b) == 0,
            '!=': _m2o_cmp ,
            '<>': _m2o_cmp,
            '<=': lambda a, b: _m2o_cmp(a,b) <= 0,
            '<': lambda a, b: _m2o_cmp(a, b) < 0,
            '>': lambda a, b: _m2o_cmp(a, b) > 0,
            '>=': lambda a, b: _m2o_cmp(a, b) >= 0,
            '=?': lambda a, b: b is None or b is False or _m2o_cmp(a, b) == 0,
            #'=like': lambda a, b: , need regexp?
            #'=ilike': lambda a, b: ,
            'like': lambda a, b: (not b) or (a and bool(b in a[1])),
            'not like': lambda a, b: b and ((not a) or (b not in a[1])),
            'ilike': lambda a, b: (not b) or (a and bool(b.lower() in a[1].lower())),
            'not ilike': lambda a, b: b and ((not a) or bool(b.lower() not in a[1].lower())),
            'in': lambda a, b: a and bool(a[1] in b),
            'not in': lambda a, b: (not a) or bool(a[1] not in b),
            }

    def __init__(self, fnct, arg=None, fnct_inv=None, fnct_inv_arg=None, type='float', fnct_search=None, obj=None, method=False, store=False, multi=False, **args):
        """
            @param multi compute several fields in one call
            @param relation name of ORM model for relational functions,
                synonym for `obj` for historical reasons. Please prefer to
                use `obj` instead in new code
        """
        digits = args.pop('digits', None)
        _column.__init__(self, **args)
        self._obj = obj
        self._method = method
        self._fnct = fnct
        self._fnct_inv = fnct_inv
        self._arg = arg
        self._multi = multi
        if 'relation' in args: # unfortunate old API
            self._obj = args['relation']

        self._fnct_inv_arg = fnct_inv_arg
        if not fnct_inv:
            self.readonly = 1
        self._type = type
        self._fnct_search = fnct_search
        self.store = store

        if not fnct_search and not store:
            self.selectable = False

        assert type not in ('function', 'related', 'dummy')
        # The trick: keep a regular field of the target type, so
        # that we can redirect procedures to it
        args.setdefault('string', '')
        if self._type == 'one2many':
            args['fields_id'] = False
        elif self._type == 'char':
            args.setdefault('size', 16)
        if digits is not None:
            args['digits'] = digits # put it back
        self._shadow = get_field_class(self._type)(obj=self._obj, shadow=True, **args)

        if store:
            if self._type != 'many2one':
                # m2o fields need to return tuples with name_get, not just foreign keys
                self._classic_read = True
            self._classic_write = True
            if type=='binary':
                self._symbol_get=lambda x:x and str(x)

        if type == 'float':
            self._symbol_c = float._symbol_c
            self._symbol_f = float._symbol_f
            self._symbol_set = float._symbol_set

        if type == 'boolean':
            self._symbol_c = boolean._symbol_c
            self._symbol_f = boolean._symbol_f
            self._symbol_set = boolean._symbol_set

        if type in ['integer','integer_big']:
            self._symbol_c = integer._symbol_c
            self._symbol_f = integer._symbol_f
            self._symbol_set = integer._symbol_set

    def digits_change(self, cr):
        self._shadow.digits_change(cr)

    @property
    def digits(self):
        return self._shadow.digits

    def post_init(self, cr, name, obj):

        super(function, self).post_init(cr, name, obj)
        self._shadow.post_init(cr, name, obj)
        if not self.store:
            return
        if self.store is True:
            sm = {obj._name:(lambda obj,cr, uid, ids, c={}: ids, None, 10, None)}
        else:
            sm = self.store

        pool_fnstore = obj.pool._store_function
        for object, aa in sm.items():
            if len(aa)==4:
                (fnct,fields2,order,length)=aa
            elif len(aa)==3:
                (fnct,fields2,order)=aa
                length = None
            else:
                raise RuntimeError('Invalid function definition %s in object %s !\n' \
                                'You must use the definition: ' \
                                'store={object:(fnct, fields, priority, time length)}.' % \
                                (name, obj._name))
            pool_fnstore.setdefault(object, [])
            ok = True
            for x,y,z,e,f,l in pool_fnstore[object]:
                if (x == obj._name) and (y == name) and (e==fields2):
                    if f == order:
                        ok = False
            if ok:
                pool_fnstore[object].append( (obj._name, name, fnct, fields2, order, length))
                pool_fnstore[object].sort(lambda x,y: cmp(x[4],y[4]))


    def search(self, cr, uid, obj, name, args, context=None):
        """ Perform partial search for 'args' within a greater query
        
            @param obj the orm model object
            @param name the name of the column being searched, should be our field name
            @param args a list of /one/ domain expression item ``[(left, op, right),]``
            
            @return another expression item, to be evaluated in the expression
            
            If possible, this function should directly return a more complete query
            domain expression (inselect, possibly) to feed in the greater SQL one.
            """
        if not self._fnct_search:
            #CHECKME: should raise an exception
            return []
        return self._fnct_search(obj, cr, uid, obj, name, args, context=context)

    def get(self, cr, obj, ids, name, user=None, context=None, values=None):
        if context is None:
            context = {}
        if values is None:
            values = {}
        res = {}
        if self._method:
            res = self._fnct(obj, cr, user, ids, name, self._arg, context)
        else:
            res = self._fnct(cr, obj._table, ids, name, self._arg, context)

        if self._type == "many2one" :
            # Filtering only integer/long values if passed
            res_ids = [x for x in res.values() if x and isinstance(x, (int,long))]

            if res_ids:
                obj_model = obj.pool.get(self._obj)
                dict_names = dict(obj_model.name_get(cr, user, res_ids, context))
                for r in res.keys():
                    if res[r] and res[r] in dict_names:
                        res[r] = (res[r], dict_names[res[r]])

        if self._type == 'binary':
            if context.get('bin_size', False):
                # client requests only the size of binary fields
                res = dict(map(get_nice_size, res.items()))
            else:
                res = dict(map(sanitize_binary_value, res.items()))

        if self._type == "integer":
            for r in res.keys():
                # Converting value into string so that it does not affect XML-RPC Limits
                if isinstance(res[r],dict): # To treat integer values with _multi attribute
                    for record in res[r].keys():
                        res[r][record] = str(res[r][record])
                else:
                    res[r] = str(res[r])
        return res
    get_memory = get

    def set(self, cr, obj, id, name, value, user=None, context=None):
        if not context:
            context = {}
        if self._fnct_inv:
            self._fnct_inv(obj, cr, user, id, name, value, self._fnct_inv_arg, context)
    set_memory = set

    def _auto_init_sql(self, name, obj, schema_table, context=None):
        
        todo = None
        if self.store:
            todo = []
            r = self._shadow._auto_init_sql(name, obj, schema_table, context=context)

            if isinstance(r, list):
                todo += r
            elif r:
                todo.append(r)

            if schema_table._state != sql_model.CREATE:
                
                order = 10
                if self.store is not True: #is dict
                    order = self.store[self.store.keys()[0]][2]
                todo.append((order, obj._update_store, (self, name)))

        else: # not store
            if getattr(self, 'nodrop', False):
                _logger.info('column %s (%s) in table %s is obsolete, but data is preserved.',
                            name, self.string, obj._table)
            elif config.get_misc('debug', 'drop_guard', False):
                _logger.warning('column %s (%s) in table %s should be removed:' \
                            'please inspect and drop if appropriate !',
                            name, self.string, obj._table)
            elif name not in schema_table.columns:
                pass
            else:
                _logger.info('column %s (%s) in table %s removed: converted to a function !',
                    name, self.string, obj._table)
                schema_table.columns[name].drop()
        return todo

    def _get_field_def(self, cr, uid, name, obj, ret, context=None):
        super(function, self)._get_field_def(cr, uid, name, obj, ret, context=context)
        # This additional attributes for M2M and function field is added
        # because we need to display tooltip with this additional information
        # when client is started in debug mode.
        ret['function'] = self._fnct and self._fnct.func_name or False
        ret['store'] = self.store
        if isinstance(self.store, dict):
            ret['store'] = str(self.store)
        ret['fnct_search'] = self._fnct_search and self._fnct_search.func_name or False
        ret['fnct_inv'] = self._fnct_inv and self._fnct_inv.func_name or False
        ret['fnct_inv_arg'] = self._fnct_inv_arg or False
        ret['func_obj'] = self._obj or False
        ret['func_method'] = self._method

        ret2 = {}
        self._shadow._get_field_def(cr, uid, name, obj, ret2, context=context)
        for k, val in ret2.items():
            if k in ('states', 'required', 'change_default',
                    'select', 'selectable', 'invisible', 'filters'):
                continue
            if val is None:
                continue
            if k not in ret:
                ret[k] = val

    def _val2browse(self, val, name, parent_bro):
        return self._shadow._val2browse(val, name, parent_bro)

    def _browse2val(self, val, name):
        return self._shadow._browse2val(val, name)

    def expr_eval(self, cr, uid, obj, lefts, operator, right, pexpr, context):
        if self.store:
            # the value of the field is store in the database, so we can use
            # it as if it were a normal field (hopefully)
            return self._shadow.expr_eval(cr, uid, obj, lefts, operator, right, pexpr, context)
        else:
            # this is a function field that is not stored
            if self._fnct_search:
                assert len(lefts) == 1, lefts
                subexp = self.search(cr, uid, obj, lefts[0], [(lefts[0], operator, right)], context=context)
                # Reminder: the field.search() API returns an expression, not a dataset,
                # which means that [] => True clause
                if not subexp:
                    return True
                else:
                    return eu.dirty_expr(subexp)
            else:
                # we must compute this field in python :'(
                assert len(lefts) == 1, lefts
                do_fallback = None
                if hasattr(obj, '_fallback_search'):
                    do_fallback = obj._fallback_search
                else:
                    do_fallback = config.get_misc('orm', 'fallback_search', None)

                if obj._debug:
                    logging.getLogger('orm.expression').debug( \
                                "%s.%s expression (%s %s %r) will fallback to %r",
                                obj._name, lefts[0], '.'.join(lefts), operator,
                                right, do_fallback)
                if do_fallback is None:
                    # the function field doesn't provide a search function and doesn't store
                    # values in the database, so we must ignore it : we generate a dummy leaf
                    return True
                elif do_fallback:
                    # Do the slow fallback.
                    # Try to see if the expression so far is a straight (ANDed)
                    # combination. In that case, we can restrict the query
                    # TODO: move to these fields
                    if self._type == 'many2one':
                        op_fn = self.FALLBACK_OPS_M2O.get(operator, None)
                    else:
                        op_fn = self.FALLBACK_OPS.get(operator, None)
                    if not op_fn:
                        raise eu.DomainMsgError(msg=_('Cannot fallback with operator "%s" !') % operator)
                    
                    # defer it ;)
                    ids_so_far = obj.search(cr, uid, [], context=context)
                    if not ids_so_far:
                        return False
                    else:
                        ids2 = []
                        if self._multi:
                            fget_name = [lefts[0],]
                        else:
                            fget_name = lefts[0]
                        for res_id, rval in self.get(cr, obj, ids_so_far,
                                                    name=fget_name, user=uid,
                                                    context=context).items():
                            if self._multi:
                                rval = rval.get(lefts[0], None)
                            if rval is None or rval is False:
                                pass
                            elif self._type == 'integer':
                                # workaround the str() of fields.function.get() :(
                                rval = int(rval)
                            elif self._type == 'float':
                                assert isinstance(rval, float), "%s: %r" %(type(rval), rval)
                                if self.digits:
                                    rval = round(rval, self.digits[1]) # TODO: shadow!

                            # TODO: relational fields don't work here, must implement
                            # special operators between their (id, name) and right

                            if op_fn(rval, right):
                                ids2.append(res_id)
                        if pexpr._debug:
                            logging.getLogger('orm.expression').debug( \
                                        "%s.%s expression yielded %s of %s records",
                                        obj._name, lefts[0], len(ids2), len(ids_so_far))
                        return ( 'id', 'in', ids2 )
                else:
                    raise NotImplementedError("Cannot compute %s.%s field for filtering" % \
                                (obj._name, lefts[0]))

        raise RuntimeError("unreachable code")

    def _move_refs(self, cr, uid, obj, name, dest_id, src_ids, context):
        # Would we ever need to act when moving records (for merge)?
        return None

# ---------------------------------------------------------
# Related fields
# ---------------------------------------------------------

class related(function):
    """Field that points to some data inside another field of the current record.

    Example::

       _columns = {
           'foo_id': fields.many2one('my.foo', 'Foo'),
           'bar': fields.related('foo_id', 'frol', type='char', string='Frol of Foo'),
        }
    """

    def _fnct_search(self, tobj, cr, uid, obj=None, name=None, domain=None, context=None):
        self._field_get2(cr, uid, obj, context)
        i = len(self._arg)-1
        sarg = name
        while i>0: # TODO: reduce queries (for pg84?)
            if type(sarg) in [type([]), type( (1,) )]:
                where = [(self._arg[i], 'in', sarg)]
            else:
                where = [(self._arg[i], '=', sarg)]
            if domain:
                where = map(lambda x: (self._arg[i],x[1], x[2]), domain)
                domain = []
            sarg = obj.pool.get(self._relations[i]['object']).search(cr, uid, where, context=context)
            i -= 1
        return [(self._arg[0], 'in', sarg)]

    def _fnct_write(self,obj,cr, uid, ids, field_name, values, args, context=None):
        self._field_get2(cr, uid, obj, context=context)
        if type(ids) != type([]):
            ids=[ids]
        objlst = obj.browse(cr, uid, ids)
        for data in objlst:
            t_id = data.id
            t_data = data
            for i, sarg in enumerate(self._arg):
                if not t_data: break
                field_detail = self._relations[i]
                if not t_data[sarg]:
                    if self._type not in ('one2many', 'many2many'):
                        t_id = t_data['id']
                    t_data = False
                elif field_detail['type'] in ('one2many', 'many2many'):
                    if self._type != "many2one":
                        t_id = t_data.id
                        t_data = t_data[sarg][0]
                    else:
                        t_data = False
                else:
                    t_id = t_data['id']
                    t_data = t_data[sarg]
            else:
                model = obj.pool.get(self._relations[-1]['object'])
                model.write(cr, uid, [t_id], {args[-1]: values}, context=context)

    def _fnct_read(self, obj, cr, uid, ids, field_name, args, context=None):
        from orm import only_ids
        self._field_get2(cr, uid, obj, context)
        if not ids: return {}
        if self._type in ('one2many', 'many2many'):
            res = dict([(i, []) for i in only_ids(ids)])
        else:
            res = {}.fromkeys(only_ids(ids), False)

        objlst = obj.browse(cr, 1, ids, context=context)
        for data in objlst:
            if not data:
                continue
            t_data = data
            for i, sarg in enumerate(self._arg):
                field_detail = self._relations[i]
                try:
                    if not t_data[sarg]:
                        t_data = False
                        break
                except:
                    t_data = False
                    break
                if field_detail['type'] in ('one2many', 'many2many') and i != len(self._arg) - 1:
                    t_data = t_data[sarg][0]
                elif t_data:
                    t_data = t_data[sarg]
            if type(t_data) == type(objlst[0]):
                res[data.id] = t_data.id
            elif t_data:
                res[data.id] = t_data
        if self._type=='many2one':
            ids = filter(None, res.values())
            if ids:
                ng = dict(obj.pool.get(self._obj).name_get(cr, 1, ids, context=context))
                if not ng:
                    logging.getLogger('orm').warning(
                            "Couldn't get %s %s for field %s." % \
                            (self._obj, ids, field_name))
                    # just go on and have a KeyError below:
                for r in res:
                    if res[r]:
                        res[r] = (res[r], ng[res[r]])
        elif self._type in ('one2many', 'many2many'):
            for r in res:
                if res[r]:
                    res[r] = [x.id for x in res[r]]
        return res

    def __init__(self, *arg, **args):
        self._relations = []
        super(related, self).__init__(self._fnct_read, arg, self._fnct_write, fnct_inv_arg=arg, method=True, fnct_search=self._fnct_search, **args)
        if self.store is True:
            # TODO: improve here to change self.store = {...} according to related objects
            pass

    def _field_get2(self, cr, uid, obj, context=None):
        if self._relations:
            return
        obj_name = obj._name
        for i, sarg in enumerate(self._arg):
            f = obj.pool.get(obj_name).fields_get(cr, uid, [sarg,], context=context)[sarg]
            self._relations.append({
                'object': obj_name,
                'type': f['type']

            })
            if f.get('relation',False):
                obj_name = f['relation']
                self._relations[-1]['relation'] = f['relation']

    def _move_refs(self, cr, uid, obj, name, dest_id, src_ids, context):
        """Move references to [src_ids] to point to dest_id
        """
        return None

class dummy(function):
    """ Dummy fields
    """
    def _fnct_search(self, tobj, cr, uid, obj=None, name=None, domain=None, context=None):
        return []

    def _fnct_write(self, obj, cr, uid, ids, field_name, values, args, context=None):
        return False

    def _fnct_read(self, obj, cr, uid, ids, field_name, args, context=None):
        return {}

    def __init__(self, *arg, **args):
        self._relations = []
        super(dummy, self).__init__(self._fnct_read, arg, self._fnct_write, fnct_inv_arg=arg, method=True, fnct_search=None, **args)

register_field_classes(function, related, dummy)

#eof