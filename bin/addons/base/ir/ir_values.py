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

from osv import osv,fields,index
from osv.orm import except_orm
from tools.misc import pickle
from tools.translate import _

EXCLUDED_FIELDS = set((
    'report_sxw_content', 'report_rml_content', 'report_sxw', 'report_rml',
    'report_sxw_content_data', 'report_rml_content_data', 'search_view', ))

class ir_values(osv.osv):
    _name = 'ir.values'
    _function_field_browse = True

    def _value_unpickle(self, cursor, user, ids, name, arg, context=None):
        res = {}
        for report in self.browse(cursor, user, ids, context=context):
            value = report[name[:-9]]
            if not report.object and value:
                try:
                    value = str(pickle.loads(value))
                except Exception:
                    pass
            res[report.id] = value
        return res

    def _value_pickle(self, cursor, user, id, name, value, arg, context=None):
        if context is None:
            context = {}
        ctx = context.copy()
        if self.CONCURRENCY_CHECK_FIELD in ctx:
            del ctx[self.CONCURRENCY_CHECK_FIELD]
        if not self.browse(cursor, user, id, context=context).object:
            value = pickle.dumps(value)
        self.write(cursor, user, id, {name[:-9]: value}, context=ctx)

    def onchange_object_id(self, cr, uid, ids, object_id, context=None):
        if not object_id: return {}
        act = self.pool.get('ir.model').browse(cr, uid, object_id, context=context)
        return {
                'value': {'model': act.model}
        }

    def onchange_action_id(self, cr, uid, ids, action_id, context=None):
        if not action_id: return {}
        act = self.pool.get('ir.actions.actions').browse(cr, uid, action_id, context=context)
        return {
                'value': {'value_unpickle': act.type+','+str(act.id)}
        }

    _columns = {
        'name': fields.char('Name', size=128),
        'model_id': fields.many2one('ir.model', 'Object', size=128,
            help="This field is not used, it only helps you to select a good model."),
        'model': fields.char('Object Name', size=128),
        'action_id': fields.many2one('ir.actions.actions', 'Action',
            help="This field is not used, it only helps you to select the right action."),
        'value': fields.text('Value'),
        'value_unpickle': fields.function(_value_unpickle, fnct_inv=_value_pickle,
            method=True, type='text', string='Value'),
        'object': fields.boolean('Is Object'),
        'key': fields.selection([('action','Action'),('default','Default')], 'Type', size=128),
        'key2' : fields.char('Event Type',help="The kind of action or button in the client side that will trigger the action.", size=128),
        'meta': fields.text('Meta Datas'),
        'meta_unpickle': fields.function(_value_unpickle, fnct_inv=_value_pickle,
            method=True, type='text', string='Metadata'),
        'res_id': fields.integer('Object ID', help="Keep 0 if the action must appear on all resources.", select=True),
        'user_id': fields.many2one('res.users', 'User', ondelete='cascade', select=True),
        'company_id': fields.many2one('res.company', 'Company', select=True)
    }
    _defaults = {
        'key': 'action',
        'key2': 'tree_but_open',
        'company_id': False
    }

    _indices = {
        'key_model_key2_res_id_user_id_idx': index.plain('key', 'model', 'key2', 'res_id', 'user_id'),
    }

    def set(self, cr, uid, key, key2, name, models, value, replace=True, isobject=False, meta=False, preserve_user=False, company=False):
        if isinstance(value, unicode):
            value = value.encode('utf8')
        if not isobject:
            value = pickle.dumps(value)
        if meta:
            meta = pickle.dumps(meta)
        assert isinstance(models, (tuple, list)), models
        if company is True:
            current_user_obj = self.pool.get('res.users').browse(cr, uid, uid, context={})
            company = current_user_obj.company_id.id
            
        ids_res = []
        for model in models:
            if isinstance(model, (list, tuple)):
                model,res_id = model
            else:
                res_id = False
            if replace:
                search_criteria = [
                    ('key', '=', key),
                    ('key2', '=', key2),
                    ('model', '=', model),
                    ('res_id', '=', res_id),
                    ('user_id', '=', preserve_user and uid),
                    ('company_id' ,'=', company)
                ]
                if key in ('meta', 'default'):
                    search_criteria.append(('name', '=', name))
                else:
                    search_criteria.append(('value', '=', value))

                self.unlink(cr, uid, self.search(cr, uid, search_criteria))
            vals = {
                'name': name,
                'value': value,
                'model': model,
                'object': isobject,
                'key': key,
                'key2': key2,
                'meta': meta,
                'user_id': preserve_user and uid,
                'company_id':company
            }
            if res_id:
                vals['res_id'] = res_id
            # Note that __ignore_ir_values means vals will not be appended with a recursive
            # lookup using self.ir_get(, model='ir.values') !
            ids_res.append(self.create(cr, uid, vals, context={'__ignore_ir_values': True}))
        return ids_res

    def get(self, cr, uid, key, key2, models, meta=False, context=None, res_id_req=False, without_user=True, key2_req=True):
        result = []
        assert isinstance(models, (list, tuple)), models

        for m in models:
            if isinstance(m, (list, tuple)):
                m, res_id = m
            else:
                res_id = False

            where = ['key=%s','model=%s']
            params = [key, str(m)]
            if key2:
                where.append('key2=%s')
                params.append(key2)
            elif key2_req and not meta:
                where.append('key2 IS NULL')
            if res_id_req and (models[-1][0] == m):
                if res_id:
                    where.append('res_id=%s')
                    params.append(res_id)
                else:
                    where.append('(res_id IS NULL)')
            elif res_id:
                if (models[-1][0]==m):
                    where.append('(res_id=%s or (res_id IS NULL))')
                    params.append(res_id)
                else:
                    where.append('res_id=%s')
                    params.append(res_id)

            where.append('(user_id=%s OR (user_id IS NULL))')
            params.append(uid)
            cr.execute('SELECT id,name,value,object,meta, key ' \
                'FROM ir_values ' \
                'WHERE ' + ' AND '.join(where) + \
                ' ORDER BY user_id, id', params, debug=self._debug)
            # Note: by default, ordering is "BY user_id NULLS LAST", and
            # we would only be allowed to explicitly use that in pg >= 8.3
            result = cr.fetchall()
            if result:
                break

        if not result:
            return []

        keys = []
        res = []
        fields_by_model = {}

        for x in result:
            if x[1] in keys:
                continue
            keys.append(x[1])
            if not x[2]:
                continue
            if x[3]:
                model,id = x[2].split(',')
                if id == 'False' or not id.isdigit():
                    continue
                # FIXME: It might be a good idea to opt-in that kind of stuff
                # FIXME: instead of arbitrarily removing random fields
                if model not in fields_by_model:
                    fields = [
                        field
                        for field in self.pool.get(model).fields_get_keys(cr, uid)
                        if field not in EXCLUDED_FIELDS]
                    fields_by_model[model] = fields

                fields = fields_by_model[model]

                try:
                    # FIXME: this is still sub-optimal, but we don't usually expect
                    # many ids at get() calls
                    datas = self.pool.get(model).read(cr, uid, [int(id)], fields, context)
                except except_orm:
                    continue
                datas = datas and datas[0]
                if not datas:
                    continue
                if model ==  'ir.actions.act_window' \
                        and 'search_view_id' in datas \
                        and datas['search_view_id']:
                    # GTK client has a bug, where it expects only the integer id
                    # rather than [id, name] (of many2one fields)
                    datas['search_view_id'] = datas['search_view_id'][0]
            else:
                datas = pickle.loads(x[2].encode('utf-8'))
            if meta:
                res.append( (x[0], x[1], datas, pickle.loads(x[4])) )
            else:
                res.append( (x[0], x[1], datas) )

        res2 = res[:]
        group_obj = self.pool.get('res.groups')
        for r in res:
            if isinstance(r[2], dict) and r[2].get('type') in ('ir.actions.report.xml','ir.actions.act_window','ir.actions.wizard'):
                groups = r[2].get('groups_id')
                if groups:
                    if not group_obj.check_user_groups(cr, uid, groups, context):
                        res2.remove(r)
                    if r[1] == 'Menuitem' and not res2:
                        raise osv.except_osv('Error !','You do not have the permission to perform this operation!')
        return res2
ir_values()
