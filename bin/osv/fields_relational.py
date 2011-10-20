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

from fields import _column, register_field_classes
from tools.translate import _
import warnings
from tools import sql_model
from tools import expr_utils as eu

#.apidoc title: Relationals fields

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
        from orm import browse_null, browse_record, except_orm
        if val:
            obj = parent_bro._table.pool.get(self._obj)
            if isinstance(val, (list, tuple)):
                value = val[0]
            else:
                value = val
        else:
            value = False

        if value:
            assert not isinstance(value, browse_record)
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
                raise except_orm(_('Error'), _('%s: ORM model %s cannot be found for %s field!') % \
                            (parent_bro._table_name, self._obj, name))
            ret = browse_record(parent_bro._cr,
                        parent_bro._uid, value, obj, parent_bro._cache,
                        context=parent_bro._context,
                        list_class=parent_bro._list_class,
                        fields_process=parent_bro._fields_process)
        else:
            ret = browse_null()
        return ret


class _rel2many(_relational):
    """ common baseclass for -2many relation fields
    """
    def _val2browse(self, val, name, parent_bro):
        from orm import browse_record

        obj = parent_bro._table.pool.get(self._obj)
        return parent_bro._list_class([
                    browse_record(parent_bro._cr, parent_bro._uid, id, obj,
                                parent_bro._cache, context=parent_bro._context, list_class=parent_bro._list_class,
                                fields_process=parent_bro._fields_process) \
                    for id in val],
                    parent_bro._context)

    def expr_eval(self, cr, uid, obj, lefts, operator, right, pexpr, context):
        if len(lefts) > 1: # TODO pg84 reduce
            field_obj = obj.pool.get(self._obj)
            # Making search easier when there is a left operand as field.o2m or field.m2m
            assert len(lefts) == 2, lefts
            right = field_obj.search(cr, uid, [(lefts[1], operator, right)], context=context)
            right1 = obj.search(cr, uid, [(lefts[0],'in', right)], context=dict(context, active_test=False))
            if right1 == []:
                return False
            else:
                return ('id', 'in', right1)
        else:
            return None

    def _expr_rev_lookup(self, cr, sid1, reltable, sid2, ids, op, debug=False):
        """ Lookup the remote model expression
            
            _rel2many have reverse-reference functionality, so we may
            need to lookup the remote table.
            
            @param s sid1 id referring to the left side of the expression
            @param f reltable Table that implements the relation (think m2m)
            @param w sid2 referring to the right side of the expression
            
            @return (qrystring, params) or (None, ids)
        """
        # todo: merge into parent query as sub-query
        res = []
        qry = None
        params = []
        if ids:
            if op in ['<','>','>=','<=']:
                qry = 'SELECT "%s" FROM "%s"'    \
                          ' WHERE "%s" %s %%s' % (sid1, reltable, sid2, op)
                params = [ids[0], ]
            elif cr.pgmode in eu.PG_MODES:
                if isinstance(ids, eu.placeholder):
                    dwc = '= %s' % ids.expr
                    params = []
                elif isinstance(ids, list) and isinstance(ids[0], eu.placeholder):
                    dwc = '= %s' % ids[0].expr
                    params = []
                elif isinstance(ids, (int, long)):
                    dwc = '= %s'
                    params = [ids,]
                else:
                    dwc = '= ANY(%s)'
                    params = [ids,]
                qry = 'SELECT "%s"'    \
                           '  FROM "%s"'    \
                           ' WHERE "%s" %s' % (sid1, reltable, sid2, dwc)
            else:
                cr.execute('SELECT "%s"'    \
                            '  FROM "%s"'    \
                            ' WHERE "%s" = ANY(%%s) ' % (sid1, reltable, sid2),
                            (ids,),
                            debug=debug)
                res.extend([r[0] for r in cr.fetchall()])
                # we return early, with the bare results
                return None, res

        else:
            # FIXME: shouldn't depend on operator???
            print "operator:", op
            qry = 'SELECT distinct("%s")' \
                           '  FROM "%s" where "%s" is not null'  % (sid1, reltable, sid1)
            params = []
           
        if cr.pgmode in eu.PG_MODES:
            return qry, params
        else:
            cr.execute(qry, params, debug=debug)
            res.extend([r[0] for r in cr.fetchall()])
            return None, res
        # unreachable code
        return None, None

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
        if not context:
            context = {}
        obj = obj_src.pool.get(self._obj)
        self._table = obj_src.pool.get(self._obj)._table
        if type(values) == type([]):
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
                and isinstance(right[0], (tuple, list)) \
                and len(right[0]) == 3:
            # That's a nested expression

            assert(operator == 'in') # others not implemented
            # Note, we don't actually check access permissions for the
            # intermediate object yet. That wouldn't still let us read
            # the forbidden record, but just use its id

            wquery = field_obj._where_calc(cr, uid, right, context)
            field_obj._apply_ir_rules(cr, uid, wquery, 'read', context=context)
            from_clause, qu1, qu2 = wquery.get_sql()
            
            if qu1:
                qu1 = "WHERE " + qu1

            qry = "SELECT id FROM %s %s " %( from_clause, qu1)

            return (lefts[0],'inselect', (qry, tuple(qu2)))

        elif operator == 'child_of' or operator == '|child_of' :
            if isinstance(right, basestring):
                # TODO: sql version?
                ids2 = [x[0] for x in field_obj.name_search(cr, uid, right, [], 'like', limit=None)]
            elif isinstance(right, (int, long)):
                ids2 = list([right])
            else:
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
                    right = map(lambda x: x[0], res_ids)
                    return (lefts[0], 'in', right)
            else:
                return (lefts[0], operator, right)

class one2many(_rel2many):
    _classic_write = False
    _prefetch = False
    _type = 'one2many'

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

        ids2 = obj.pool.get(self._obj).search(cr, user, self._domain + [(self._fields_id, 'in', ids)], limit=self._limit, context=context)
        for r in obj.pool.get(self._obj)._read_flat(cr, user, ids2, [self._fields_id], context=context, load='_classic_write'):
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
                ids3 = map(lambda x:x[0], cr.fetchall())
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
            for diname in dest_obj.inherits:
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

    def expr_eval(self, cr, uid, obj, lefts, operator, right, pexpr, context):
                # Applying recursivity on field(one2many)
        ret = super(one2many, self).expr_eval(cr, uid, obj, lefts, operator, right, pexpr, context)
        if ret is not None:
            return ret
        
        field_obj = obj.pool.get(self._obj)
        assert len(lefts) == 1, lefts
        if operator == 'child_of' or operator == '|child_of':
            if isinstance(right, basestring):
                ids2 = [x[0] for x in obj.name_search(cr, uid, right, [], 'like', context=context, limit=None)]
            else:
                ids2 = list(right)
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
            call_null = True

            if right is not False:
                if isinstance(right, basestring):
                    ids2 = [x[0] for x in obj.name_search(cr, uid, right, [], operator, context=context, limit=None)]
                    if ids2:
                        operator = 'in'
                else:
                    if not isinstance(right, list):
                        ids2 = [right]
                    else:
                        ids2 = right
                if not ids2:
                    if operator in ['like','ilike','in','=']:
                        #no result found with given search criteria
                        call_null = False
                        return False
                    else:
                        call_null = True
                        operator = 'in' # operator changed because ids are directly related to main object
                else:
                    call_null = False
                    o2m_op = 'in'
                    if operator in  ['not like','not ilike','not in','<>','!=']:
                        o2m_op = 'not in'
                    erqu, erpa = self._expr_rev_lookup(cr, self._fields_id,
                                            field_obj._table, 'id', ids2, operator, debug=pexpr._debug)
                    if not erqu:
                        return ('id', o2m_op, erpa )
                    else:
                        if o2m_op in ('in', '='):
                            o2m_op = 'inselect'
                        elif o2m_op in ('not in', '!=', '<>'):
                            o2m_op = 'not inselect'
                        else:
                            raise NotImplementedError('operator: %s' % o2m_op)
                        return ('id', o2m_op, (erqu, erpa))

            if call_null:
                o2m_op = 'not in'
                if operator in  ['not like','not ilike','not in','<>','!=']:
                    o2m_op = 'in'

                erqu, erpa = self._expr_rev_lookup(cr, self._fields_id, 
                                field_obj._table, 'id', [], operator, debug=pexpr._debug)
                if not erqu:
                    return ('id', o2m_op, erpa )
                else:
                    if o2m_op in ('in', '='):
                        o2m_op = 'inselect'
                    elif o2m_op in ('not in', '!=', '<>'):
                        o2m_op = 'not inselect'
                    else:
                        raise NotImplementedError('operator: %s' % o2m_op)
                    return ('id', o2m_op, (erqu, erpa))


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
            if not (isinstance(act, list) or isinstance(act, tuple)) or not act:
                continue
            if act[0] == 0:
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
                    d1 = ' and ' + ' and '.join(d1)
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
        for act in values:
            # TODO: use constants instead of these magic numbers
            if act[0] == 0:
                raise _('Not Implemented')
            elif act[0] == 1:
                raise _('Not Implemented')
            elif act[0] == 2:
                raise _('Not Implemented')
            elif act[0] == 3:
                raise _('Not Implemented')
            elif act[0] == 4:
                raise _('Not Implemented')
            elif act[0] == 5:
                raise _('Not Implemented')
            elif act[0] == 6:
                obj.datas[id][name] = act[2]

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

    def expr_eval(self, cr, uid, obj, lefts, operator, right, pexpr, context):
        ret = super(many2many, self).expr_eval(cr, uid, obj, lefts, operator, right, pexpr, context)
        if ret is not None:
            return ret

        m2m_rel, m2m_id1, m2m_id2 = self._sql_names(obj)
        field_obj = obj.pool.get(self._obj)
        if operator == 'child_of' or operator == '|child_of':
            if isinstance(right, basestring):
                ids2 = [x[0] for x in field_obj.name_search(cr, uid, right, [], 'like', context=context, limit=None)]
            else:
                ids2 = list(right)

            dom = pexpr._rec_get(cr, uid, field_obj, ids2, null_too=(operator == '|child_of'), context=context)
            ids2 = field_obj.search(cr, uid, dom, context=context)
            if m2m_rel != obj._name:
                erqu, erpa = self._expr_rev_lookup(cr, m2m_id1, m2m_rel, m2m_id2, ids2, operator, debug=pexpr._debug)
                assert (not erqu) # TODO
                ids2 = erpa
            
            return ('id', 'in', ids2)
        else:
            call_null_m2m = True
            if right is not False:
                if isinstance(right, basestring):
                    # TODO sql version?
                    res_ids = [x[0] for x in field_obj.name_search(cr, uid, right, [], operator, context=context)]
                    if res_ids:
                        operator = 'in'
                else:
                    if isinstance(right, tuple):
                        res_ids = list(map(int, right))
                    elif not isinstance(right, list):
                        res_ids = [ int(right) ]
                    else:
                        res_ids = map(int, right)
                if not res_ids:
                    if operator in ['like','ilike','in','=']:
                        #no result found with given search criteria
                        call_null_m2m = False
                        return False
                    else:
                        call_null_m2m = True
                        operator = 'in' # operator changed because ids are directly related to main object
                else:
                    call_null_m2m = False
                    m2m_op = 'in'
                    if operator in  ['not like','not ilike','not in','<>','!=']:
                        m2m_op = 'not in'

                    erqu, erpa = self._expr_rev_lookup(cr, m2m_id1, m2m_rel, m2m_id2, res_ids, operator, debug=pexpr._debug)
                    if not erqu:
                        return ('id', m2m_op, erpa )
                    else:
                        if m2m_op in ('in', '='):
                            m2m_op = 'inselect'
                        elif m2m_op in ('not in', '!=', '<>'):
                            m2m_op = 'not inselect'
                        else:
                            raise NotImplementedError('operator: %s' % m2m_op)
                        return ('id', m2m_op, (erqu, erpa))
            if call_null_m2m:
                m2m_op = 'not in'
                if operator in  ['not like','not ilike','not in','<>','!=']:
                    m2m_op = 'in'
                erqu, erpa = self._expr_rev_lookup(cr, m2m_id1, m2m_rel, m2m_id2, [], operator, debug=pexpr._debug)
                if not erqu:
                    return ('id', m2m_op, erpa )
                else:
                    if m2m_op in ('in', '='):
                        m2m_op = 'inselect'
                    elif m2m_op in ('not in', '!=', '<>'):
                        m2m_op = 'not inselect'
                    else:
                        raise NotImplementedError('operator: %s' % m2m_op)
                    return ('id', m2m_op, (erqu, erpa))

        raise RuntimeError("unreachable code")

class reference(_column):
    _type = 'reference'
    _sql_type = 'varchar'

    @classmethod
    def from_manual(cls, field_dict, attrs):
        return cls(selection=eval(field_dict['selection']), **attrs)

    def __init__(self, string, selection, size=64, **args):
        _column.__init__(self, string=string, size=size, selection=selection, **args)

    def _val2browse(self, val, name, parent_bro):
        from orm import browse_null, browse_record
        if not val:
            return browse_null()
        ref_obj, ref_id = val.split(',')
        ref_id = long(ref_id)
        if ref_id:
            obj = parent_bro._table.pool.get(ref_obj)
            return browse_record(parent_bro._cr, parent_bro._uid, ref_id, obj, parent_bro._cache,
                                context=parent_bro._context, list_class=parent_bro._list_class,
                                fields_process=parent_bro._fields_process)

        return browse_null()

register_field_classes(one2one, many2one, one2many, many2many, reference)

#eof