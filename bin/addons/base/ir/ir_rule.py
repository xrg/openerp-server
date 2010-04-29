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

from osv import fields, osv, expression
import time
from operator import itemgetter
from functools import partial
import tools
from tools.safe_eval import safe_eval as eval
import logging

class ir_rule(osv.osv):
    _name = 'ir.rule'
    _MODES = ['read', 'write', 'create', 'unlink']

    def __domain_calc(self, rule_dic, eval_data):
        """ Calculate the domain expression for some rule.
        @rule_dic is a dictionary with rule{'domain_force', 'operand', 'operator', 'field_name'}
        @eval_data a dictionary with context for eval()
        """
            res[rule.id] = eval(rule.domain_force, eval_user_data)
        return res

    def _get_value(self, cr, uid, ids, field_name, arg, context={}):
        res = {}
        for rule in self.browse(cr, uid, ids, context):
            if not rule.groups:
                res[rule.id] = True
        else:
            opnd = rule_dic['operand']
            if opnd and opnd.startswith('user.') and opnd.count('.') > 1:
                #Need to check user.field.field1.field2(if field  is False,it will break the chain)
                op = opnd[5:]
                opnd = opnd[:5+len(op[:op.find('.')])] +' and '+ opnd + ' or False'
            
            if rule_dic['operator'] in ('in', 'child_of', '|child_of'):
                res = safe_eval("[('%s', '%s', [%s])]" % (rule_dic['field_name'], rule_dic['operator'],
                    safe_eval(opnd,eval_data)), eval_data)
            else:
                res = safe_eval("[('%s', '%s', %s)]" % (rule_dic['field_name'], 
                    rule_dic['operator'], opnd), eval_data)
        if self._debug:
            logging.getLogger('orm').debug("Domain calc: %s " % res)
        return res
        
        
    def _domain_force_get(self, cr, uid, ids, field_name, arg, context=None):
        res = {}
        eval_user_data = {'user': self.pool.get('res.users').browse(cr, 1, uid),
                'time':time}
        for rule in self.browse(cr, uid, ids, fields_only=['domain_force','operand','operator', 'field_id'], context=context):
            rule_dic = { 'domain_force': rule.domain_force, 'operand': rule.operand, 
                        'operator': rule.operator, 'field_name': rule.field_id.name }
            res[rule.id] = self.__domain_calc(rule_dic, eval_user_data)
        return res

    def _check_model_obj(self, cr, uid, ids, context={}):
        return not any(isinstance(self.pool.get(rule.model_id.model), osv.osv_memory) for rule in self.browse(cr, uid, ids, context))

    _columns = {
        'name': fields.char('Name', size=128, select=1),
        'model_id': fields.many2one('ir.model', 'Object',select=1, required=True),
        'global': fields.function(_get_value, method=True, string='Global', type='boolean', store=True, help="If no group is specified the rule is global and applied to everyone"),
        'groups': fields.many2many('res.groups', 'rule_group_rel', 'rule_group_id', 'group_id', 'Groups'),
        'domain_force': fields.char('Domain', size=250),
        'domain': fields.function(_domain_force_get, method=True, string='Domain', type='char', size=250),
        'perm_read': fields.boolean('Apply For Read'),
        'perm_write': fields.boolean('Apply For Write'),
        'perm_create': fields.boolean('Apply For Create'),
        'perm_unlink': fields.boolean('Apply For Delete')
    }

    _order = 'model_id DESC'

    _defaults = {
        'perm_read': True,
        'perm_write': True,
        'perm_create': True,
        'perm_unlink': True,
        'global': True,
    }
    _sql_constraints = [
        ('no_access_rights', 'CHECK (perm_read!=False or perm_write!=False or perm_create!=False or perm_unlink!=False)', 'Rule must have at least one checked access right'),
    ]
    _constraints = [
        (_check_model_obj, 'Rules are not supported for osv_memory objects !', ['model_id'])
    ]

    def domain_create(self, cr, uid, rule_ids):
        dom = ['&'] * (len(rule_ids)-1)
        for rule in self.browse(cr, uid, rule_ids):
            dom += rule.domain
        return dom
        
    def domain_get(self, cr, uid, model_name, mode='read', context=None):
        """ Retrieve the domain for some /model_name/.
            It will locate relevant rules (for uid != 1, aka admin), and put
            them together into an expression.
            
            Returns (where_clause, clause_params, tables) so that an SQL
            query can append the where_clause, feed it with clause_params.
            If needed, tables will contain any tables (including one for the
            model_name) needed in the FROM expression
        """
        if uid == 1:
            return [], [], ['"'+self.pool.get(model_name)._table+'"']

        cr.execute_prepared( 'ir_rule_domain_get_'+mode, """SELECT r.id, g.id AS group_id,
                        imf.name AS field_name, r.domain_force, r.operand, r.operator
                FROM ir_rule r JOIN (ir_rule_group g
                    JOIN ir_model m ON (g.model_id = m.id))
                    ON (g.id = r.rule_group)
                    LEFT JOIN ir_model_fields imf ON ( imf.id = r.field_id )
                WHERE m.model = %s
                AND r.perm_""" + mode + """
                AND (g.id IN (SELECT rule_group_id FROM group_rule_group_rel g_rel
                            JOIN res_groups_users_rel u_rel ON (g_rel.group_id = u_rel.gid)
                            WHERE u_rel.uid = %s) OR g.global)
                ORDER BY group_id""", (model_name, uid),
                                debug=self._debug)

        eval_user_data = {'user': self.pool.get('res.users').browse(cr, 1, uid),
                'time':time}
        
        dom = []
        last_gid = None
        for r in cr.dictfetchall():
            rdo = self.__domain_calc(r, eval_user_data)
            # Order by is important, because it will group the ir_rule_groups together
            if (last_gid is None) or last_gid == r['group_id']:
                dom += rdo
            else:
                dom = expression.or_join(dom, rdo)
            last_gid = r['group_id']
        
        if self._debug:
            logging.getLogger('orm').debug("Resulting domain: %s" % dom)
        d1,d2,tables = self.pool.get(model_name)._where_calc(cr, uid, dom, active_test=False)
        return d1, d2, tables
        
    domain_get = tools.cache()(domain_get)

    def unlink(self, cr, uid, ids, context=None):
        res = super(ir_rule, self).unlink(cr, uid, ids, context=context)
        # Restart the cache on the _compute_domain method of ir.rule
        self._compute_domain.clear_cache(cr.dbname)
        return res

    def create(self, cr, user, vals, context=None):
        res = super(ir_rule, self).create(cr, user, vals, context=context)
        # Restart the cache on the _compute_domain method of ir.rule
        self._compute_domain.clear_cache(cr.dbname)
        return res

    def write(self, cr, uid, ids, vals, context=None):
        if not context:
            context={}
        res = super(ir_rule, self).write(cr, uid, ids, vals, context=context)
        # Restart the cache on the _compute_domain method
        self._compute_domain.clear_cache(cr.dbname)
        return res

ir_rule()

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

