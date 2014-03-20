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

from fields import _column, register_field_classes
from tools.translate import _
import warnings
from tools import sql_model
from tools import expr_utils as eu
from tools import orm_utils
from operator import itemgetter
import logging

#.apidoc title: Relational fields

#
# Values: (0, 0,  { fields })    create
#         (1, ID, { fields })    update
#         (2, ID)                remove (delete)
#         (3, ID)                unlink one (target id or target of relation)
#         (4, ID)                link
#         (5)                    unlink all (only valid for one2many)
#
#CHECKME: dans la pratique c'est quoi la syntaxe utilisee pour le 5? (5) ou (5, 0)?

class _relational(_column):
    _classic_read = False
    _classic_write = True

    def _get_field_def(self, cr, uid, name, obj, ret, context=None):
        super(_relational, self)._get_field_def(cr, uid, name, obj, ret, context=context)
        ret['relation'] = self._obj
        ret['domain'] = self._domain
        ret['context'] = self._context

    def _auto_init_prefetch(self, name, obj, prefetch_schema, context=None):
        _column._auto_init_prefetch(self, name, obj, prefetch_schema, context=context)
        dest_obj = obj.pool.get(self._obj)
        if not dest_obj:
            raise KeyError('There is no reference available for %s' % (self._obj,))
        prefetch_schema.hints['tables'].append(dest_obj._table)

class _rel2one(_relational):
    def _val2browse(self, val, name, parent_bro):
        if val:
            obj = parent_bro._table.pool.get(self._obj)
            if isinstance(val, (list, tuple)):
                value = val[0]
            else:
                value = val
        else:
            value = False

        if value:
            assert not isinstance(value, orm_utils.browse_record)
            if obj is None:
                # In some cases the target model is not available yet,
                # but this resolution is late enough to let the model
                # be required. Therefore it is an error
                # This situation can be caused by custom fields that
                # connect objects with m2o without respecting module
                # dependencies, causing relationships to be connected
                # to soon when the target is not loaded yet.
                cr = parent_bro._cr # for gettext
                context = parent_bro._context
                global __hush_pyflakes
                __hush_pyflakes= (cr, context)
                raise orm_utils.except_orm(_('Error'), _('%s: ORM model %s cannot be found for %s field!') % \
                            (parent_bro._table_name, self._obj, name))
            ret = orm_utils.browse_record(parent_bro._cr,
                        parent_bro._uid, value, obj, parent_bro._cache,
                        context=parent_bro._context,
                        list_class=parent_bro._list_class,
                        fields_process=parent_bro._fields_process)
        else:
            ret = orm_utils.browse_null()
        return ret

    def _browse2val(self, bro, name):
        return (bro and bro.id) or False

    def _move_refs(self, cr, uid, obj, name, dest_id, src_ids, context):
        """Move references to [src_ids] to point to dest_id
        """
        from orm import orm_memory
        if isinstance(obj, orm_memory):
            return None
        if not getattr(obj, '_auto', True):
            # FIXME! we assume that all non-auto models are Views, which
            # may be false...
            return None

        # BIG TODO: do stored fields at `obj` need to be recomputed?
        cr.execute("UPDATE \"%s\" SET %s = %%s WHERE %s = ANY(%%s)" % \
                (obj._table, name, name),
                (dest_id, list(src_ids)), debug=obj._debug)
        return None

    def calc_group(self, cr, uid, obj, lefts, right, context):
        if len(lefts) > 1:
            raise NotImplementedError("Cannot use %s yet" % ('.'.join(lefts)))
        full_field = '"%s".%s' % (obj._table, lefts[0])
        if right is True:
            right = self.group_operator or 'count'
        if isinstance(right, basestring) and right.lower() in ('min', 'max', 'count'):
            aggregate = '%s(%s)' % (right.upper(), lefts[0])
        else:
            raise ValueError("Invalid aggregate function: %r", right)
        return '.'.join(lefts), { 'group_by': full_field, 'order_by': full_field,
                'field_expr': full_field, 'field_aggr': aggregate }

class _rel2many(_relational):
    """ common baseclass for -2many relation fields
    """
    def _val2browse(self, val, name, parent_bro):

        obj = parent_bro._table.pool.get(self._obj)
        return parent_bro._list_class([
                    orm_utils.browse_record(parent_bro._cr, parent_bro._uid, id, obj,
                                parent_bro._cache, context=parent_bro._context, list_class=parent_bro._list_class,
                                fields_process=parent_bro._fields_process) \
                    for id in val],
                    parent_bro._context)

    def _browse2val(self, bro, name):
        return orm_utils.only_ids(bro)

    def _expr_rev_lookup(self, cr, uid, obj, pexpr, lefts, op, field_obj, right_expr, context=None):
        """ Translate remote `right_expr` into native query

            _rel2many have reverse-reference functionality, so we may
            need to lookup the remote table.

            @param obj model in which this field belongs
            @param pexpr parent expression
            @param lefts list of left operands
            @param op operator, either 'child_of' or 'in'
            @param field_obj remote ORM model
            @param right_expr domain expression to apply on field_obj

            @return (qrystring, params) or (None, ids)
        """

        raise NotImplementedError

    def expr_eval(self, cr, uid, obj, lefts, operator, right, pexpr, context):
        """ Expressions in one2many can be like:

            [('this', '=', 1)] [('this','=', 'name of 1')]
            [('this.name', '=', 'name of 1')] # but avoid name_search()
            ...
        """
        field_obj = obj.pool.get(self._obj)
        right_expr = None

        if len(lefts) > 1:
            # All operators here resolve to 'in' for lefts[0],
            # negation will apply to lefts[-1] only!
            right_expr = [('.'.join(lefts[1:]), operator, right)]
            operator = 'in'
        elif right is False:
            # False means to return those records that have no 2many entries
            right_expr = [] # = all records
            if operator in ('=', 'like',):
                operator = 'not in'
            elif operator in ('!=', '<>', 'not like'):
                operator = 'in'
            else:
                raise eu.DomainInvalidOperator(obj, lefts, operator, right)
        elif isinstance(right, eu.placeholder):
            right_expr = [('id', operator, right)]
            operator = 'in'
        elif right == []:
            # empty set of results
            # immediately return, no need to reverse lookup or anything
            return False
        elif isinstance(right, basestring):
            # First, push the operator away, in case of 'child_of'
            if operator in ('child_of', '|child_of'):
                popop = operator
                operator = '='
            else:
                popop = 'in'

            if getattr(field_obj._name_search, 'original_orm', False) \
                    and getattr(field_obj.name_search, 'original_orm', False):
                # Then, see if we can short-cut around expensive _name_search()
                right_expr = [(field_obj._rec_name, operator, right)]
            else:
                # has custom name_search, use it the expensive way
                ids2 = [x[0] for x in field_obj.name_search(cr, uid, right, [], operator, context=context, limit=None)]
                right_expr = [('id', 'in', ids2)]

            # Since 'wc' already contains the right logic, pop the operator
            operator = popop
        elif operator in ('in', 'not in') and right \
                and isinstance(right, (list, tuple)) \
                and all([isinstance(x, basestring) for x in right]):
            if getattr(field_obj._name_search, 'original_orm', False) \
                    and getattr(field_obj.name_search, 'original_orm', False):
                # Then, see if we can short-cut around expensive _name_search()
                right_expr = [(field_obj._rec_name, '=', right)]
            else:
                # has custom name_search, use it the expensive way
                ids2 = [x[0] for x in field_obj.name_search(cr, uid, right, [], '=', context=context, limit=None)]
                right_expr = [('id', 'in', ids2)]
        elif isinstance(right, list) and len(right) and not isinstance(right[0], (int, long)):
            # nested right expression
            if operator in ('=', 'in'):
                operator = 'in'
            elif operator in ('!=', '<>', 'not in'):
                operator = 'not in'
            else:
                raise eu.DomainInvalidOperator(obj, lefts, operator)
            right_expr = right
        else:
            if isinstance(right, (long, int)):
                right = [right,]
            if not isinstance(right, (list, tuple)):
                raise eu.DomainRightError(obj, lefts, operator, right)
            if operator in ('child_of', '|child_of'):
                pass
            elif operator in ('=', 'in'):
                operator = 'in'
            elif operator in ('!=', '<>', 'not in'):
                operator = 'not in'
            else:
                raise eu.DomainInvalidOperator(obj, lefts, operator)
            right_expr = [('id', 'in', right)]


        if pexpr._debug:
            logging.getLogger('fields.x2many').debug("%s.%s expression right: %r", \
                    obj._name, lefts[0], right_expr)
        erqu, erpa = self._expr_rev_lookup(cr, uid, obj, pexpr, lefts, operator,
                    field_obj, right_expr, context=context)
        if operator in ('child_of', '|child_of'):
            operator = 'in'

        if not erqu:
            return ('id', operator, erpa )
        else:
            operator += 'select'
            return ('id', operator, (erqu, erpa))

class one2one(_rel2one):
    _type = 'one2one'

    def __init__(self, obj, string='unknown', **args):
        warnings.warn("The one2one field doesn't work anymore", DeprecationWarning)
        _column.__init__(self, string=string, **args)
        assert obj
        self._obj = obj

        if 'copy_data' not in args:
            self.copy_data = 'deep_copy'

    def set(self, cr, obj_src, id, field, act, user=None, context=None):
        if not context:
            context = {}
        obj = obj_src.pool.get(self._obj)
        self._table = obj_src.pool.get(self._obj)._table
        if act[0] == 0:
            id_new = obj.create(cr, user, act[1])
            cr.execute('update '+obj_src._table+' set '+field+'=%s where id=%s', (id_new, id), debug=obj_src._debug)
        else:
            cr.execute('select '+field+' from '+obj_src._table+' where id=%s', (act[0],), debug=obj_src._debug)
            id = cr.fetchone()[0]
            obj.write(cr, user, [id], act[1], context=context)

    def search(self, cr, obj, args, name, value, offset=0, limit=None, uid=None, context=None):
        return obj.pool.get(self._obj).search(cr, uid, args+self._domain+[('name', 'like', value)], offset, limit, context=context)

    def deep_copy(self, cr, uid, obj, id, f, data, context):
        res = []
        rel = obj.pool.get(self._obj)
        if data[f]:
            # duplicate following the order of the ids
            # because we'll rely on it later for copying
            # translations in copy_translation()!
            data[f].sort()
            for rel_id in data[f]:
                # the lines are first duplicated using the wrong (old)
                # parent but then are reassigned to the correct one thanks
                # to the (0, 0, ...)
                d = rel.copy_data(cr, uid, rel_id, context=context)
                res.append((0, 0, d))
        return res

class many2one(_rel2one):
    _type = 'many2one'
    _symbol_c = '%s'
    _symbol_f = lambda x: x or None
    _symbol_set = (_symbol_c, _symbol_f)
    merge_op = '|eq'

    @classmethod
    def from_manual(cls, field_dict, attrs):
        return cls(field_dict['relation'], **attrs)

    def __init__(self, obj, string='unknown', **args):
        _relational.__init__(self, string=string, **args)
        assert obj
        self._obj = obj

    def set_memory(self, cr, obj, id, field, values, user=None, context=None):
        obj.datas.setdefault(id, {})
        obj.datas[id][field] = values

    def get_memory(self, cr, obj, ids, name, user=None, context=None, values=None):
        result = {}
        for id in ids:
            result[id] = obj.datas[id].get(name, False)
        return result

    def get(self, cr, obj, ids, name, user=None, context=None, values=None):
        if context is None:
            context = {}
        if values is None:
            values = {}

        res = {}
        for r in values:
            res[r['id']] = r[name]
        for id in ids:
            res.setdefault(id, '')
        obj = obj.pool.get(self._obj)

        # build a dictionary of the form {'id_of_distant_resource': name_of_distant_resource}
        # we use uid=1 because the visibility of a many2one field value (just id and name)
        # must be the access right of the parent form and not the linked object itself.
        records = dict(obj.name_get(cr, 1,
                                    list(set([x for x in res.values() if isinstance(x, (int,long))])),
                                    context=context))
        for id in res:
            if res[id] in records:
                res[id] = (res[id], records[res[id]])
            else:
                res[id] = False
        return res

    def set(self, cr, obj_src, id, field, values, user=None, context=None):
        # Inactive code! _classic_write == True means it won't be called
        if not context:
            context = {}
        obj = obj_src.pool.get(self._obj)
        self._table = obj_src.pool.get(self._obj)._table
        if isinstance(values, list):
            for act in values:
                if act[0] == 0:
                    id_new = obj.create(cr, act[2])
                    cr.execute('update '+obj_src._table+' set '+field+'=%s where id=%s', (id_new, id), debug=obj_src._debug)
                elif act[0] == 1:
                    obj.write(cr, [act[1]], act[2], context=context)
                elif act[0] == 2:
                    cr.execute('delete from '+self._table+' where id=%s', (act[1],), debug=obj_src._debug)
                elif act[0] == 3 or act[0] == 5:
                    cr.execute('update '+obj_src._table+' set '+field+'=null where id=%s', (id,), debug=obj_src._debug)
                elif act[0] == 4:
                    cr.execute('update '+obj_src._table+' set '+field+'=%s where id=%s', (act[1], id), debug=obj_src._debug)
        else:
            if values:
                cr.execute('update '+obj_src._table+' set '+field+'=%s where id=%s', (values, id), debug=obj_src._debug)
            else:
                cr.execute('update '+obj_src._table+' set '+field+'=null where id=%s', (id,), debug=obj_src._debug)

    def search(self, cr, obj, args, name, value, offset=0, limit=None, uid=None, context=None):
        return obj.pool.get(self._obj).search(cr, uid, args+self._domain+[('name', 'like', value)], offset, limit, context=context)

    def _auto_init_sql(self, name, obj, schema_table, context=None):
        assert self._obj, "%s.%s has no reference" %(obj._name, name)
        dest_obj = obj.pool.get(self._obj)
        if not dest_obj:
            raise KeyError('There is no reference available for %s' % (self._obj,))

        if self._obj != 'ir.actions.actions':
            references = {'table': dest_obj._table, 'on_delete': self.ondelete}
        else:
            # We cannot have a proper key for the sql-inherited 'ir_actions'
            # table, can we?
            references = False

        schema_table.column_or_renamed(name, getattr(self, 'oldname', None))

        r = schema_table.check_column(name, 'INTEGER', not_null=self.required,
                default=self._sql_default_for(name,obj, context=context),
                select=self.select, references=references, comment=self.string)

        assert r
        return None

    def expr_eval(self, cr, uid, obj, lefts, operator, right, pexpr, context):
        field_obj = obj.pool.get(self._obj)
        if len(lefts) > 1:
            wc = field_obj._where_calc(cr, uid,
                    [('.'.join(lefts[1:]), operator, right)],
                    active_test=False, context=context)
            if isinstance(wc, list):
                # :( this is an orm_memory object, try again
                wc = field_obj.search(cr, uid, [('.'.join(lefts), operator, right)], context=context)
                if not wc:
                    return False
                else:
                    return  (lefts[0], 'in', wc)
            else:
                field_obj._apply_ir_rules(cr, uid, wc, 'read', context=context)
                from_clause, qu1, qu2 = wc.get_sql()
                qry = 'SELECT "%s".id FROM %s ' % (field_obj._table, from_clause)
                if qu1:
                    qry += "WHERE " + qu1
                return (lefts[0], 'inselect', (qry, qu2))

        if isinstance(right, list) and len(right) \
                and any([isinstance(x, (list, tuple)) for x in right]):
            # That's a nested expression

            if operator in ('in', '='):
                op2 = 'inselect'
            #elif operator in ('not in', '!=', '<>'):
            #    op2 = 'not inselect'
            #    Won't work: we have to inverse the 'right' expression alone, rather
            #    than the inner query, because the access rules need apply transparently.
            else:
                # others not implemented
                raise eu.DomainRightError(obj, lefts, operator, right)
            # Note, we don't actually check access permissions for the
            # intermediate object yet. That wouldn't still let us read
            # the forbidden record, but just use its id

            wquery = field_obj._where_calc(cr, uid, right, context)
            field_obj._apply_ir_rules(cr, uid, wquery, 'read', context=context)
            from_clause, qu1, qu2 = wquery.get_sql()

            if qu1:
                qu1 = "WHERE " + qu1

            qry = 'SELECT "%s".id FROM %s %s ' %( field_obj._table, from_clause, qu1)

            return (lefts[0], op2, (qry, tuple(qu2)))

        elif operator == 'child_of' or operator == '|child_of' :
            if isinstance(right, basestring):
                ids2 = [x[0] for x in field_obj.name_search(cr, uid, right, [], 'like', limit=None)]
            elif isinstance(right, (int, long)):
                ids2 = list([right])
            elif not right:
                ids2 = [] # most likely to yield empty result set
            else:
                # Here, right can still contain tuples, which make it a
                # nested domain expression
                ids2 = list(right)

            # TODO verify!
            null_too = (operator == '|child_of')
            if self._obj != obj._name:
                dom = pexpr._rec_get(cr, uid, field_obj, ids2, left=lefts[0],
                        prefix=self._obj, null_too=null_too, context=context)
            else:
                dom = pexpr._rec_get(cr, uid, obj, ids2, parent=lefts[0],
                        null_too=null_too, context=context)
            if len(dom) == 0:
                return True
            elif len(dom) == 1:
                return dom[0]
            else:
                return eu.nested_expr(dom)
        else:
            do_name = False
            op2 = operator
            if isinstance(right, basestring):
                    # and not isinstance(field, fields.related):
                do_name = True
                if operator == 'in':
                    op2 = '='
                elif operator == 'not in':
                    op2 = '!='
                else:
                    op2 = operator
            elif right is False:
                return (lefts[0], operator, None)
            elif right == []:
                do_name = False
                if operator in ('not in', '!=', '<>'):
                    # (many2one not in []) should return all records
                    return True
                else:
                    return False
            elif isinstance(right, (list, tuple)) and operator in ('in', 'not in'):
                do_name = True
                for r in right:
                    if not isinstance(r, basestring):
                        do_name = False
                        break
                if do_name and isinstance(right, tuple):
                        right = list(right)

            if do_name:
                ctx = context.copy()
                ctx['active_test'] = False
                res_ids = field_obj.name_search(cr, uid, right, [], op2, limit=None, context=ctx)
                if not res_ids:
                    return False
                else:
                    right = map(itemgetter(0), res_ids)
                    return (lefts[0], 'in', right)
            else:
                return (lefts[0], operator, right)

class one2many(_rel2many):
    _classic_write = False
    _prefetch = False
    _type = 'one2many'
    merge_op = True # it's the other side we care about

    @classmethod
    def from_manual(cls, field_dict, attrs):
        return cls(field_dict['relation'], field_dict['relation_field'], **attrs)

    def __init__(self, obj, fields_id, string='unknown', limit=None, **args):
        _relational.__init__(self, string=string, **args)
        assert obj
        self._obj = obj
        self._fields_id = fields_id
        self._limit = limit
        #one2many can't be used as condition for defaults
        assert(self.change_default != True)

        if 'copy_data' not in args:
            self.copy_data = 'deep_copy'

    def get_memory(self, cr, obj, ids, name, user=None, offset=0, context=None, values=None):
        if context is None:
            context = {}
        if self._context:
            context = context.copy()
            context.update(self._context)
        if not values:
            values = {}
        res = {}
        for id in ids:
            res[id] = []
        ids2 = obj.pool.get(self._obj).search(cr, user, [(self._fields_id, 'in', ids)], limit=self._limit, context=context)
        for r in obj.pool.get(self._obj).read(cr, user, ids2, [self._fields_id], context=context, load='_classic_write'):
            if r[self._fields_id] in res:
                res[r[self._fields_id]].append(r['id'])
        return res

    def set_memory(self, cr, obj, id, field, values, user=None, context=None):
        if not context:
            context = {}
        if self._context:
            context = context.copy()
        context.update(self._context)
        if not values:
            return
        obj = obj.pool.get(self._obj)
        for act in values:
            if act[0] == 0:
                act[2][self._fields_id] = id
                obj.create(cr, user, act[2], context=context)
            elif act[0] == 1:
                obj.write(cr, user, [act[1]], act[2], context=context)
            elif act[0] == 2:
                obj.unlink(cr, user, [act[1]], context=context)
            elif act[0] == 3:
                obj.datas[act[1]][self._fields_id] = False
            elif act[0] == 4:
                obj.datas[act[1]][self._fields_id] = id
            elif act[0] == 5:
                for o in obj.datas.values():
                    if o[self._fields_id] == id:
                        o[self._fields_id] = False
            elif act[0] == 6:
                for id2 in (act[2] or []):
                    obj.datas[id2][self._fields_id] = id

    def search_memory(self, cr, obj, args, name, value, offset=0, limit=None, uid=None, operator='like', context=None):
        raise _('Not Implemented')

    def get(self, cr, obj, ids, name, user=None, offset=0, context=None, values=None):
        if context is None:
            context = {}
        if self._context:
            context = context.copy()
        context.update(self._context)
        if values is None:
            values = {}

        res = {}
        for id in ids:
            res[id] = []

        for r in obj.pool.get(self._obj).search_read(cr, user,
                    self._domain + [(self._fields_id, 'in', ids)],
                    fields=[self._fields_id], load='_classic_write',
                    limit=self._limit, context=context):
            if r[self._fields_id] in res:
                res[r[self._fields_id]].append(r['id'])
        return res

    def set(self, cr, obj, id, field, values, user=None, context=None):
        result = []
        if not context:
            context = {}
        if self._context:
            context = context.copy()
        context.update(self._context)
        context['no_store_function'] = True
        if not values:
            return
        _table = obj.pool.get(self._obj)._table
        obj = obj.pool.get(self._obj)
        for act in values:
            if act[0] == 0:
                act[2][self._fields_id] = id
                if act[2].get('_vptr', False):
                    obj_v = obj.pool.get(act[2]['_vptr'])
                    obj_col = obj_v._inherits.get(self._obj, False)
                    if not obj_col:
                        raise ValueError("Data for %s came, which does not inherit %s" % (act[2]['_vptr'], self._obj))
                    vals_cpy = act[2].copy()
                    vals_cpy.pop('_vptr')
                    id_v_new = obj_v.create(cr, user, vals_cpy, context=context)
                    id_new = obj_v.read(cr, user, id_v_new, fields=[obj_col], load='_classic_write')[obj_col]
                else:
                    id_new = obj.create(cr, user, act[2], context=context)
                result += obj._store_get_values(cr, user, [id_new], act[2].keys(), context)
            elif act[0] == 1:
                obj.write(cr, user, [act[1]], act[2], context=context)
            elif act[0] == 2:
                obj.unlink(cr, user, [act[1]], context=context)
            elif act[0] == 3:
                cr.execute('update '+_table+' set '+self._fields_id+'=null where id=%s', (act[1],), debug=obj._debug)
            elif act[0] == 4:
                cr.execute('update '+_table+' set '+self._fields_id+'=%s where id=%s', (id, act[1]), debug=obj._debug)
            elif act[0] == 5:
                cr.execute('update '+_table+' set '+self._fields_id+'=null where '+self._fields_id+'=%s', (id,), debug=obj._debug)
            elif act[0] == 6:
                obj.write(cr, user, act[2], {self._fields_id:id}, context=context or {})
                ids2 = act[2] or [0]
                cr.execute('select id from '+_table+' where '+self._fields_id+'=%s and id <> ALL (%s)', (id,ids2), debug=obj._debug)
                ids3 = map(itemgetter(0), cr.fetchall())
                obj.write(cr, user, ids3, {self._fields_id:False}, context=context or {})
        return result

    def search(self, cr, obj, args, name, value, offset=0, limit=None, uid=None, operator='like', context=None):
        return obj.pool.get(self._obj).name_search(cr, uid, value, self._domain, operator, context=context,limit=limit)

    def deep_copy(self, cr, uid, obj, id, f, data, context):
        res = []
        rel = obj.pool.get(self._obj)
        if data[f]:
            # duplicate following the order of the ids
            # because we'll rely on it later for copying
            # translations in copy_translation()!
            data[f].sort()
            for rel_id in data[f]:
                # the lines are first duplicated using the wrong (old)
                # parent but then are reassigned to the correct one thanks
                # to the (0, 0, ...)
                d = rel.copy_data(cr, uid, rel_id, context=context)
                if d:
                    res.append((0, 0, d))
        return res

    def _auto_init_sql(self, name, obj, schema_table, context=None):
        # Treat like many2one, don't care about other->self being limited
        assert self._obj, "%s.%s has no reference" %(obj._name, name)
        dest_obj = obj.pool.get(self._obj)
        if not dest_obj:
            raise KeyError('There is no reference available for %s' % (self._obj,))

        rev_column = None
        # Try to locate if remote object already has the corresponding
        # many2one column
        if self._fields_id in dest_obj._columns:
            rev_column = dest_obj._columns[self._fields_id]
        else:
            for diname in dest_obj._inherits:
                dobj2 = obj.pool.get(diname)
                if self._fields_id in dobj2._columns:
                    rev_column = dobj2._columns[self._fields_id]

        if rev_column:
            if rev_column._type != 'many2one' or rev_column._obj != obj._name:
                # This happens in mail_gateway, crm. Keep this warning until they're fixed
                warnings.warn("%s.%s is one2many of %s.%s, but latter is not the inverse many2one!" % \
                        (obj._name, name, self._obj, self._fields_id), FutureWarning)
                #raise RuntimeError()
        else:
            # Dirty job, define the column implicitly
            assert obj._name != 'ir.actions.actions'
            assert not self.required
            assert not self._sql_default_for(name,obj), self._sql_default_for(name,obj)

            dest_table = schema_table.parent()[dest_obj._table]
            references = {'table': obj._table }

            r = dest_table.check_column(self._fields_id, 'INTEGER',
                    select=self.select,
                    references=references, comment=self.string)
                    # not_null=? , default=self._sql_default_for(name,obj),

            assert r
        return None


    def _expr_rev_lookup(self, cr, uid, obj, pexpr, lefts, op, field_obj, right_expr, context=None):

        if op == 'child_of' or op == '|child_of':
            null_too = (op == '|child_of')
            if self._obj != field_obj._name:
                dom = pexpr._rec_get(cr, uid, obj, right_expr, left=lefts[0],
                        prefix=self._obj, null_too=null_too, context=context)
            else:
                dom = pexpr._rec_get(cr, uid, field_obj, right_expr, parent=lefts[0],
                        null_too=null_too, context=context)
            right_expr = dom
            op = 'in'

        wc = field_obj._where_calc(cr, uid, right_expr, context=context)
        assert not isinstance(wc, list), field_obj # :( this is an orm_memory object
        field_obj._apply_ir_rules(cr, uid, wc, 'read', context=context)

        from_clause, where_c, params = wc.get_sql()
        qry = 'SELECT "%s".%s FROM %s WHERE "%s".%s IS NOT NULL' % \
                (field_obj._table, self._fields_id, from_clause, field_obj._table, self._fields_id)
        if where_c:
            qry += ' AND ' + where_c

        if pexpr._debug:
            logging.getLogger('fields.many2many').debug("Resulting query: %s", qry)

        if cr.pgmode in eu.PG_MODES:
            return qry, params
        else:
            cr.execute(qry, params, debug=pexpr._debug)
            res = map(itemgetter(0), cr.fetchall())
            return None, res


class many2many(_rel2many):
    """ many-to-many bidirectional relationship

       It handles the low-level details of the intermediary relationship
       table transparently.

       :param obj destination model
       :param rel optional name of the intermediary relationship table. If not specified,
                a canonical name will be derived based on the alphabetically-ordered
                model names of the source and destination (in the form: ``amodel_bmodel_rel``).
                Automatic naming is not possible when the source and destination are
                the same, for obvious ambiguity reasons.
       :param id1 optional name for the column holding the foreign key to the current
                model in the relationship table. If not specified, a canonical name
                will be derived based on the model name (in the form: `src_model_id`).
       :param id2 optional name for the column holding the foreign key to the destination
                model in the relationship table. If not specified, a canonical name
                will be derived based on the model name (in the form: `dest_model_id`)
       :param string field label

    ::
            Values: (0, 0,  { fields })    create
                (1, ID, { fields })    update (write fields to ID)
                (2, ID)                remove (calls unlink on ID, that will also delete the relationship because of the ondelete)
                (3, ID)                unlink (delete the relationship between the two objects but does not delete ID)
                (4, ID)                link (add a relationship)
                (5, ID)                unlink all
                (6, ?, ids)            set a list of links
    """
    _classic_write = False
    _prefetch = False
    _type = 'many2many'
    merge_op = 'join'

    @classmethod
    def from_manual(cls, field_dict, attrs):
        _rel1 = field_dict['relation'].replace('.', '_')
        _rel2 = field_dict['model'].replace('.', '_')
        _rel_name = 'x_%s_%s_%s_rel' %(_rel1, _rel2, field_dict['name'])
        return cls(field_dict['relation'], _rel_name, 'id1', 'id2', **attrs)

    def __init__(self, obj, rel=None, id1=None, id2=None, string='unknown', limit=None, **args):
        """
            @param obj  the foreign model to relate to
            @param rel  a name for the table to hold the relation data
            @param id1  column name for /our/ id in `rel` table
            @param id2  column name for obj's id in `rel` table

            In fact, `rel`, `id1` and `id2` are not limited to any names, but
            usually follow the naming convention:

                rel:  like '%s_%s_rel' %(our_model->name, rel->name)
                id1:  our_model+'_id'
                id2:  rel._table_name + '_id'
        """
        _relational.__init__(self, string=string, **args)
        assert obj
        self._obj = obj
        if rel and '.' in rel:
            raise Exception(_('The second argument of the many2many field %s must be a SQL table !'\
                'You used %s, which is not a valid SQL table name.')% (string,rel))
        self._rel = rel
        self._id1 = id1
        self._id2 = id2
        self._limit = limit

        if 'copy_data' not in args:
            self.copy_data = 'shallow_copy'

    def _sql_names(self, source_model):
        """Return the SQL names defining the structure of the m2m relationship table

            Note: by default, a m2m is symmetrical among source and destination models.
            This means that if a m2m field is declared at both models, it will use
            the same relation table and keep the same data entered from either end.

            :return: (m2m_table, local_col, dest_col) where m2m_table is the table name,
                     local_col is the name of the column holding the current model's FK, and
                     dest_col is the name of the column holding the destination model's FK, and
        """
        tbl, col1, col2 = self._rel, self._id1, self._id2
        if not all((tbl, col1, col2)):
            # the default table name is based on the stable alphabetical order of tables
            dest_model = source_model.pool.get(self._obj)
            tables = tuple(sorted([source_model._table, dest_model._table]))
            if not (tbl or getattr(self, 'shadow', False)):
                assert tables[0] != tables[1], 'Implicit/Canonical naming of m2m relationship '\
                                               '"%s" of model %s table "%s" to %s "%s" '\
                                               'is not possible when source and destination models are '\
                                               'the same' % \
                                               (self.string, source_model._name, source_model._table,
                                                dest_model._name, dest_model._table)
                tbl = '%s_%s_rel' % tables
            if not col1:
                col1 = '%s_id' % source_model._table
            if not col2:
                col2 = '%s_id' % dest_model._table
        return (tbl, col1, col2)

    def get(self, cr, model, ids, name, user=None, offset=0, context=None, values=None):
        if not context:
            context = {}
        if not values:
            values = {}
        res = {}
        if not ids:
            return res
        for id in ids:
            res[id] = []
        if offset:
            warnings.warn("Specifying offset at a many2many.get() may produce unpredictable results.",
                      DeprecationWarning, stacklevel=2)
        obj = model.pool.get(self._obj)
        rel, id1, id2 = self._sql_names(model)

        # static domains are lists, and are evaluated both here and on client-side, while string
        # domains supposed by dynamic and evaluated on client-side only (thus ignored here)
        # FIXME: make this distinction explicit in API!
        domain = isinstance(self._domain, list) and self._domain or []

        wquery = obj._where_calc(cr, user, domain, context=context)
        obj._apply_ir_rules(cr, user, wquery, 'read', context=context)
        from_c, where_c, where_params = wquery.get_sql()
        if where_c:
            where_c = ' AND ' + where_c

        if offset or self._limit:
            order_by = ' ORDER BY "%s".%s' %(obj._table, obj._order.split(',')[0])
        else:
            order_by = ''

        limit_str = ''
        if self._limit is not None:
            limit_str = ' LIMIT %d' % self._limit

        query = 'SELECT %(rel)s.%(id2)s, %(rel)s.%(id1)s \
                   FROM %(rel)s, %(from_c)s \
                  WHERE %(rel)s.%(id1)s = ANY(%%s) \
                    AND %(rel)s.%(id2)s = %(tbl)s.id \
                 %(where_c)s  \
                 %(order_by)s \
                 %(limit)s \
                 OFFSET %(offset)d' \
            % {'rel': rel,
               'from_c': from_c,
               'tbl': obj._table,
               'id1': id1,
               'id2': id2,
               'where_c': where_c,
               'limit': limit_str,
               'order_by': order_by,
               'offset': offset,
              }
        cr.execute(query, [ids,] + where_params, debug=obj._debug)
        for r in cr.fetchall():
            res[r[1]].append(r[0])
        return res

    def set(self, cr, model, id, name, values, user=None, context=None):
        if not context:
            context = {}
        if not values:
            return
        rel, id1, id2 = self._sql_names(model)
        obj = model.pool.get(self._obj)
        for act in values:
            if not (isinstance(act, (list, tuple)) and act):
                continue
            if act[0] == 0:
                if act[2].get('_vptr', False):
                    obj_v = obj.pool.get(act[2]['_vptr'])
                    obj_col = obj_v._inherits.get(self._obj, False)
                    if not obj_col:
                        raise ValueError("Data for %s came, which does not inherit %s" % (act[2]['_vptr'], self._obj))
                    vals_cpy = act[2].copy()
                    vals_cpy.pop('_vptr')
                    id_v_new = obj_v.create(cr, user, vals_cpy, context=context)
                    idnew = obj_v.read(cr, user, id_v_new, fields=[obj_col], load='_classic_write')[obj_col]
                else:
                    idnew = obj.create(cr, user, act[2], context=context)
                cr.execute('insert into '+rel+' ('+id1+','+id2+') values (%s,%s)', (id, idnew), debug=obj._debug)
            elif act[0] == 1:
                obj.write(cr, user, [act[1]], act[2], context=context)
            elif act[0] == 2:
                obj.unlink(cr, user, [act[1]], context=context)
            elif act[0] == 3:
                cr.execute('delete from '+rel+' where ' + id1 + '=%s and '+ id2 + '=%s', (id, act[1]), debug=obj._debug)
            elif act[0] == 4:
                # following queries are in the same transaction - so should be relatively safe
                cr.execute('SELECT 1 FROM '+rel+' WHERE '+id1+' = %s and '+id2+' = %s', (id, act[1]), debug=obj._debug)
                if not cr.fetchone():
                    cr.execute('insert into '+rel+' ('+id1+','+id2+') values (%s,%s)', (id, act[1]), debug=obj._debug)
            elif act[0] == 5:
                cr.execute('delete from '+rel+' where ' + id1 + ' = %s', (id,), debug=obj._debug)
            elif act[0] == 6:

                # FIXME: it is safer to call _apply_ir_rules() than domain_get()
                d1, d2,tables = obj.pool.get('ir.rule').domain_get(cr, user, obj._name, context=context)
                if d1:
                    d1 = ' and (%s)' % ' and '.join(d1)
                else:
                    d1 = ''
                cr.execute('delete from '+rel+' where '+id1+'=%s AND '+id2+' IN (SELECT '+rel+'.'+id2+' FROM '+rel+', '+','.join(tables)+' WHERE '+rel+'.'+id1+'=%s AND '+rel+'.'+id2+' = '+obj._table+'.id '+ d1 +')', [id, id]+d2, debug=obj._debug)

                for act_nbr in act[2]:
                    cr.execute('insert into '+rel+' ('+id1+','+id2+') values (%s, %s)', (id, act_nbr), debug=obj._debug)

    #
    # TODO: use a name_search
    #
    def search(self, cr, obj, args, name, value, offset=0, limit=None, uid=None, operator='like', context=None):
        return obj.pool.get(self._obj).search(cr, uid, args+self._domain+[('name', operator, value)], offset, limit, context=context)

    def get_memory(self, cr, obj, ids, name, user=None, offset=0, context=None, values=None):
        result = {}
        for id in ids:
            result[id] = obj.datas[id].get(name, [])
        return result

    def set_memory(self, cr, obj, id, name, values, user=None, context=None):
        if not values:
            return
        inobj = obj.pool.get(self._obj)
        if name not in obj.datas[id]:
            obj.datas[id][name] = []
        for act in values:
            # TODO: use constants instead of these magic numbers
            if act[0] == 0:
                idnew = inobj.create(cr, user, act[2], context=context)
                obj.datas[id][name].append(idnew)
            elif act[0] == 1:
                inobj.write(cr, user, [act[1]], act[2], context=context)
            elif act[0] == 2:
                inobj.unlink(cr, user, [act[1]], context=context)
            elif act[0] == 3:
                try:
                    obj.datas[id][name].remove(act[1])
                except ValueError:
                    pass
            elif act[0] == 4:
                if (act[1] not in obj.datas[id][name]):
                    obj.datas[id][name].append(act[1])
            elif act[0] == 5:
                obj.datas[id][name] = []
            elif act[0] == 6:
                obj.datas[id][name] = list(act[2])

    def shallow_copy(self, cr, uid, obj, id, f, data, context):
        return [(6, 0, data[f])]
    # TODO: a deep_copy

    def _auto_init_prefetch(self, name, obj, prefetch_schema, context=None):
        _relational._auto_init_prefetch(self, name, obj, prefetch_schema, context=context)
        rel, id1, id2 = self._sql_names(obj)
        prefetch_schema.hints['tables'].append(rel)

    def _auto_init_sql(self, name, obj, schema_table, context=None):
        rel, id1, id2 = self._sql_names(obj)

        dest_obj = obj.pool.get(self._obj)
        if not dest_obj:
            raise KeyError('There is no reference available for %s' % (self._obj,))

        schema_alltables = schema_table.parent_schema().tables
        if not rel in schema_alltables:
            table = schema_alltables.append(sql_model.Table(rel,
                        comment='Relation between %s and %s' % \
                                    (obj._table, dest_obj._table)))
        else:
            table = schema_alltables[rel]

        has_constraint = False
        is_id1_first = True
        for con in table.constraints:
            if isinstance(con, sql_model.PlainUniqueConstraint) \
                        and set(con.columns) == set([id1, id2]):
                has_constraint = True
                is_id1_first = (con.columns[0] == id1 )
            else:
                con.set_state('drop')

        # Note: since the UNIQUE constraint will create an implicit index
        # for the first column, we need only to create another index for
        # the second column.

        c1 = table.check_column(id1,'INTEGER', not_null=True,
                references=dict(table=obj._table, on_delete="cascade"),
                select=not is_id1_first)
        c2 = table.check_column(id2,'INTEGER', not_null=True,
                references=dict(table=dest_obj._table, on_delete="cascade"),
                select=is_id1_first)

        assert c1 and c2

        if not has_constraint:
            con = table.constraints.append(sql_model.PlainUniqueConstraint(
                name="%s_ids_uniq" % rel, columns=[id1, id2]))
            con.set_depends(table.columns[id1])
            con.set_depends(table.columns[id2])

        return None

    def _get_field_def(self, cr, uid, name, obj, ret, context=None):
        super(many2many, self)._get_field_def(cr, uid, name, obj, ret, context=context)
        # This additional attributes for M2M and function field is added
        # because we need to display tooltip with this additional information
        # when client is started in debug mode.
        m2m_rel, m2m_id1, m2m_id2 = self._sql_names(obj)
        ret['related_columns'] = list((m2m_id1, m2m_id2))
        ret['third_table'] = m2m_rel

    def _expr_rev_lookup(self, cr, uid, obj, pexpr, lefts, op, field_obj, right_expr, context=None):
        m2m_rel, m2m_id1, m2m_id2 = self._sql_names(obj)
        if op == 'child_of' or op == '|child_of':
            right_expr = pexpr._rec_get(cr, uid, field_obj, right_expr,
                    null_too=(op == '|child_of'), context=context)

        wc = field_obj._where_calc(cr, uid, right_expr, context=context)
        assert not isinstance(wc, list), field_obj # :( this is an orm_memory object
        field_obj._apply_ir_rules(cr, uid, wc, 'read', context=context)

        wc.join((field_obj._table, m2m_rel, 'id', m2m_id2))
        from_clause, where_c, params = wc.get_sql()
        qry = 'SELECT "%s".%s FROM %s WHERE %s ' % (m2m_rel, m2m_id1, from_clause, where_c)

        if pexpr._debug:
            logging.getLogger('fields.many2many').debug("Resulting query: %s", qry)

        if cr.pgmode in eu.PG_MODES:
            return qry, params
        else:
            cr.execute(qry, params, debug=pexpr._debug)
            res = map(itemgetter(0), cr.fetchall())
            return None, res

    def calc_merge(self, cr, uid, obj, name, b_dest, b_src, context):
        if b_dest and self.merge_op == 'join':
            if not b_dest[name]:
                return b_src[name]
            elif not b_src[name]:
                return None
            else:
                # print "type:", type(b_dest[name]), type(b_src[name])
                return b_dest[name] + b_src[name]
        return super(many2many, self).calc_merge(cr, uid, obj, name, b_dest=b_dest, b_src=b_src, context=context)

    def _move_refs(self, cr, uid, obj, name, dest_id, src_ids, context):
        """Move references to [src_ids] to point to dest_id

        """
        from orm import orm_memory
        if isinstance(obj, orm_memory):
            return None
        rel, id1, id2 = self._sql_names(obj)
        # BIG TODO: do stored fields at `obj` need to be recomputed?

        # remove duplicates
        cr.execute("DELETE FROM \"%s\" WHERE %s = ANY(%%s) " \
               "AND %s in (SELECT %s FROM \"%s\" WHERE %s = %%s) " % \
                   (rel, id2, id1, id1, rel, id2),
               (list(src_ids), dest_id), debug=obj._debug)
        cr.execute("UPDATE \"%s\" SET %s = %%s WHERE %s = ANY(%%s)" % \
                (rel, id2, id2),
                (dest_id, list(src_ids)), debug=obj._debug)
        return None

class reference(_column):
    _type = 'reference'
    _sql_type = 'varchar'
    _classic_read = False
    merge_op = True

    @classmethod
    def from_manual(cls, field_dict, attrs):
        return cls(selection=eval(field_dict['selection']), **attrs)

    def __init__(self, string, selection, size=64, **args):
        _column.__init__(self, string=string, size=size, selection=selection, **args)

    def _val2browse(self, val, name, parent_bro):
        if not val:
            return orm_utils.browse_null()
        ref_obj, ref_id = val.split(',')
        ref_id = long(ref_id)
        if ref_id:
            obj = parent_bro._table.pool.get(ref_obj)
            return orm_utils.browse_record(parent_bro._cr, parent_bro._uid, ref_id, obj, parent_bro._cache,
                                context=parent_bro._context, list_class=parent_bro._list_class,
                                fields_process=parent_bro._fields_process)

        return orm_utils.browse_null()

    def get(self, cr, obj, ids, name, uid=None, context=None, values=None):
        """
            A late check for referential integrity. Since the DB can't do that
            for us, we do filter the records ourselves
        """
        result = {}
        # copy initial values fetched previously.
        for value in values:
            result[value['id']] = value[name]
            if value[name]:
                model, res_id = value[name].split(',')
                if not obj.pool.get(model).exists(cr, uid, [int(res_id)], context=context):
                    result[value['id']] = False
        return result

    def _get_field_def(self, cr, uid, name, obj, ret, context=None):
        # Function copied from fields.selection
        super(reference, self)._get_field_def(cr, uid, name, obj, ret, context=context)
        if isinstance(self.selection, (tuple, list)):
            translation_obj = obj.pool.get('ir.translation')
            # translate each selection option
            sel_vals = []
            sel2 = []
            for (key, val) in self.selection:
                if val:
                    sel_vals.append(val)

            if context and context.get('lang', False):
                sel_dic =  translation_obj._get_multisource(cr, uid,
                            obj._name + ',' + name, 'selection',
                            context['lang'], sel_vals)
            else:
                sel_dic = {}

            for key, val in self.selection:
                sel2.append((key, sel_dic.get(val, val)))
            ret['selection'] = sel2
        else:
            # call the 'dynamic selection' function
            ret['selection'] = self.selection(obj, cr, uid, context)

    @staticmethod
    def orm_all_selection(this, cr, uid, context=None):
        """ Selection of /all/ ORM models

            @param this the parent model object

            Note: it will only yield regular orm classes, not orm_memory or
            abstract ones
        """
        import orm
        return [ (r['model'], r['name']) for r in this.pool.get('ir.model').\
                search_read(cr, uid, [], fields=['model', 'name'], \
                        order='name', context=context) \
                if isinstance(this.pool.get(r['model']), orm.orm)]

    _orm_still_include = [ 'res.partner', 'res.partner.address', 'res.request']

    @classmethod
    def orm_user_selection(cls, this, cr, uid, context=None):
        """User-ORM models. That is, not the framework ones

            This fn() uses some crude heuristics to guess which models are
            actually of 'user' interest rather than secondary/hidden objects.

            Also skips orm_memory models, reports.
        """
        import orm
        return [ (r['model'], r['name']) for r in this.pool.get('ir.model').\
                search_read(cr, uid, ['|', '&', '&', '!', ('model', '=like', 'res.%'),
                    '!', ('model', '=like', 'ir.%'), '!', ('model', '=like', 'workflow.%'),
                    ('model', 'in', cls._orm_still_include)], \
                    fields=['model', 'name'], order='name', context=context) \
                if isinstance(this.pool.get(r['model']), orm.orm) \
                    and not isinstance(this.pool.get(r['model']), orm.orm_memory)
                    and r['model'] != r['name'] ]
            # Note: Due to (osv.osv) habit, some models inherit both orm
            # and orm_memory. We have to take these out.

register_field_classes(one2one, many2one, one2many, many2many, reference)

#eof