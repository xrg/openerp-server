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

_logger = logging.getLogger('orm')

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

    def __init__(self, fnct, arg=None, fnct_inv=None, fnct_inv_arg=None, type='float', fnct_search=None, obj=None, method=False, store=False, multi=False, **args):
        """
            @param multi compute several fields in one call
        """
        _column.__init__(self, **args)
        self._obj = obj
        self._method = method
        self._fnct = fnct
        self._fnct_inv = fnct_inv
        self._arg = arg
        self._multi = multi
        if 'relation' in args:
            self._obj = args['relation']

        self.digits = args.get('digits', (16,2))
        self.digits_compute = args.get('digits_compute', None)

        self._fnct_inv_arg = fnct_inv_arg
        if not fnct_inv:
            self.readonly = 1
        self._type = type
        self._fnct_search = fnct_search
        self.store = store

        if not fnct_search and not store:
            self.selectable = False

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
        if self.digits_compute:
            t = self.digits_compute(cr)
            self._symbol_set=('%s', lambda x: ('%.'+str(t[1])+'f') % (__builtin__.float(x or 0.0),))
            self.digits = t


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

            if self._type == 'many2one':
                rtype = 'integer'
                rrefs = None
                assert self._obj, "%s.%s has no reference" %(obj._name, name)
                dest_obj = obj.pool.get(self._obj)
                if not dest_obj:
                    raise KeyError('There is no reference available for %s' % (self._obj,))

                if self._obj != 'ir.actions.actions':
                    # on delete/update just remove the stored value and
                    # cause computation
                    rrefs = {'table': dest_obj._table, 'on_delete': 'cascade', 'on_update': 'cascade'}
            else:
                rfield = get_field_class(self._type)

                rtype = rfield._sql_type
                rrefs = None
                if not rfield._sql_type:
                    raise NotImplementedError("Why called function<stored>._auto_init_sql() on %s (%s) ?" % \
                        (name, rfield.__class__.__name__))

            schema_table.column_or_renamed(name, getattr(self, 'oldname', None))

            r = schema_table.check_column(name, rtype, not_null=self.required,
                    default=False, select=self.select, size=self.size,
                    references=rrefs, comment=self.string)

            assert r

            if schema_table._state != 'create':
                todo = []
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
            for i in range(len(self.arg)):
                if not t_data: break
                field_detail = self._relations[i]
                if not t_data[self.arg[i]]:
                    if self._type not in ('one2many', 'many2many'):
                        t_id = t_data['id']
                    t_data = False
                elif field_detail['type'] in ('one2many', 'many2many'):
                    if self._type != "many2one":
                        t_id = t_data.id
                        t_data = t_data[self.arg[i]][0]
                    else:
                        t_data = False
                else:
                    t_id = t_data['id']
                    t_data = t_data[self.arg[i]]
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
            for i in range(len(self.arg)):
                field_detail = self._relations[i]
                try:
                    if not t_data[self.arg[i]]:
                        t_data = False
                        break
                except:
                    t_data = False
                    break
                if field_detail['type'] in ('one2many', 'many2many') and i != len(self.arg) - 1:
                    t_data = t_data[self.arg[i]][0]
                elif t_data:
                    t_data = t_data[self.arg[i]]
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
        self.arg = arg
        self._relations = []
        super(related, self).__init__(self._fnct_read, arg, self._fnct_write, fnct_inv_arg=arg, method=True, fnct_search=self._fnct_search, **args)
        if self.store is True:
            # TODO: improve here to change self.store = {...} according to related objects
            pass

    def _field_get2(self, cr, uid, obj, context=None):
        if self._relations:
            return
        obj_name = obj._name
        for i in range(len(self._arg)):
            f = obj.pool.get(obj_name).fields_get(cr, uid, [self._arg[i]], context=context)[self._arg[i]]
            self._relations.append({
                'object': obj_name,
                'type': f['type']

            })
            if f.get('relation',False):
                obj_name = f['relation']
                self._relations[-1]['relation'] = f['relation']

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
        self.arg = arg
        self._relations = []
        super(dummy, self).__init__(self._fnct_read, arg, self._fnct_write, fnct_inv_arg=arg, method=True, fnct_search=None, **args)

register_field_classes(function, related, dummy)

#eof