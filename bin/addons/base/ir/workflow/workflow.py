# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2012 P. Christeas <xrg@hellug.gr>
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

from osv import fields, osv, index
import netsvc
from tools.orm_utils import only_ids

class workflow(osv.osv):
    _name = "workflow"
    _table = "wkf"
    _order = "name"
  #  _log_access = False
    _columns = {
        'name': fields.char('Name', size=64, required=True),
        'osv': fields.char('Resource Object', size=64, required=True,select=True),
        'on_create': fields.boolean('On Create', select=True),
        'activities': fields.one2many('workflow.activity', 'wkf_id', 'Activities'),
    }
    _defaults = {
        'on_create': True
    }

    def write(self, cr, user, ids, vals, context=None):
        if not context:
            context={}
        
        res = super(workflow, self).write(cr, user, ids, vals, context=context)
        
        rmodels = self.read(cr, user, only_ids(ids), fields=['osv'], context=context)
        if rmodels:
            wf_service = netsvc.LocalService("workflow")
            wf_service.reload_models(cr, [ r['osv'] for r in rmodels])
        return res

    def get_active_workitems(self, cr, uid, res, res_id, context=None):

        cr.execute('SELECT * FROM wkf WHERE osv=%s LIMIT 1',(res,), debug=self._debug)
        wkfinfo = cr.dictfetchone()
        workitems = []

        if wkfinfo:
            cr.execute('SELECT act_id, count(*) \
                    FROM wkf_workitem \
                    WHERE inst_id= (SELECT id FROM wkf_instance \
                            WHERE res_id=%s AND wkf_id=%s \
                            ORDER BY state LIMIT 1 ) \
                    GROUP BY act_id', (res_id, wkfinfo['id']), debug=self._debug)
            workitems = dict(cr.fetchall())

        return {'wkf': wkfinfo, 'workitems':  workitems}

    def create(self, cr, user, vals, context=None):
        if not context:
            context={}
        
        res = super(workflow, self).create(cr, user, vals, context=context)
        if 'osv' in vals: # must be
            wf_service = netsvc.LocalService("workflow")
            wf_service.reload_models(cr, [ vals['osv'],])
        return res
        
    def unlink(self, cr, uid, ids, context=None):
        rmodels = self.read(cr, uid, only_ids(ids), fields=['osv'], context=context)
        res = super(workflow, self).unlink(cr, uid, ids, context=context)
        if rmodels:
            wf_service = netsvc.LocalService("workflow")
            wf_service.reload_models(cr, [ r['osv'] for r in rmodels])
        return res

workflow()

class wkf_activity(osv.osv):
    _name = "workflow.activity"
    _table = "wkf_activity"
    _order = "name"
   # _log_access = False

    _columns = {
        'name': fields.char('Name', size=64, required=True),
        'wkf_id': fields.many2one('workflow', 'Workflow', required=True, select=True, ondelete='cascade'),
        'split_mode': fields.selection([('XOR', 'Xor'), ('OR','Or'), ('AND','And')], 'Split Mode', size=3, required=True),
        'join_mode': fields.selection([('XOR', 'Xor'), ('AND', 'And')], 'Join Mode', size=3, required=True),
        'kind': fields.selection([('dummy', 'Dummy'), ('function', 'Function'), ('subflow', 'Subflow'), ('stopall', 'Stop All')], 'Kind', size=64, required=True),
        'action': fields.text('Python Action'),
        'action_id': fields.many2one('ir.actions.server', 'Server Action', ondelete='set null'),
        'flow_start': fields.boolean('Flow Start'),
        'flow_stop': fields.boolean('Flow Stop'),
        'subflow_id': fields.many2one('workflow', 'Subflow'),
        'signal_send': fields.char('Signal (subflow.*)', size=32),
        'out_transitions': fields.one2many('workflow.transition', 'act_from', 'Outgoing Transitions'),
        'in_transitions': fields.one2many('workflow.transition', 'act_to', 'Incoming Transitions'),
    }

    _defaults = {
        'kind': 'dummy',
        'join_mode': 'XOR',
        'split_mode': 'XOR',
    }
    
    def create(self, cr, user, vals, context=None):
        res = super(wkf_activity, self).create(cr, user, vals, context=context)
        cr.execute('SELECT wkf.osv FROM wkf, wkf_activity '
            'WHERE wkf.id = wkf_activity.wkf_id AND wkf_activity.id = %s',
                (res,), debug=self._debug)
        r = cr.fetchone()
        wf_service = netsvc.LocalService("workflow")
        wf_service.reload_models(cr, [r[0],])
        return res

    def write(self, cr, user, ids, vals, context=None):
        res = super(wkf_activity, self).write(cr, user, ids, vals, context=context)
        cr.execute('SELECT DISTINCT wkf.osv FROM wkf, wkf_activity '
            'WHERE wkf.id = wkf_activity.wkf_id AND wkf_activity.id = ANY(%s)', 
                (ids,), debug=self._debug)
        wf_service = netsvc.LocalService("workflow")
        wf_service.reload_models(cr, [r[0] for r in cr.fetchall()])
        return res
        
    def unlink(self, cr, uid, ids, context=None):
        cr.execute('SELECT DISTINCT wkf.osv FROM wkf, wkf_activity '
            'WHERE wkf.id = wkf_activity.wkf_id AND wkf_activity.id = ANY(%s)',
                (ids,), debug=self._debug)
        models = [r[0] for r in cr.fetchall()]
        res = super(wkf_activity, self).unlink(cr, uid, ids, context=context)
        wf_service = netsvc.LocalService("workflow")
        wf_service.reload_models(cr, models)
        return res

wkf_activity()

class wkf_transition(osv.osv):
    _table = "wkf_transition"
    _name = "workflow.transition"
   # _log_access = False
    _rec_name = 'signal'

    _columns = {
        'trigger_model': fields.char('Trigger Object', size=128),
        'trigger_expr_id': fields.char('Trigger Expression', size=128),
        'signal': fields.char('Signal (button Name)', size=64,
                              help="When the operation of transition comes from a button pressed in the client form, "\
                              "signal tests the name of the pressed button. If signal is NULL, no button is necessary to validate this transition."),
        'group_id': fields.many2one('res.groups', 'Group Required',
                                   help="The group that a user must have to be authorized to validate this transition."),
        'condition': fields.char('Condition', required=True, size=128,
                                 help="Expression to be satisfied if we want the transition done."),
        'act_from': fields.many2one('workflow.activity', 'Source Activity', required=True, select=True, ondelete='cascade',
                                    help="Source activity. When this activity is over, the condition is tested to determine if we can start the ACT_TO activity."),
        'act_to': fields.many2one('workflow.activity', 'Destination Activity', required=True, select=True, ondelete='cascade',
                                  help="The destination activity."),
        'wkf_id': fields.related('act_from','wkf_id', type='many2one', relation='workflow', string='Workflow', select=True),
    }
    _defaults = {
        'condition': 'True',
    }
    
    def create(self, cr, user, vals, context=None):
        res = super(wkf_transition, self).create(cr, user, vals, context=context)
        cr.execute('SELECT wkf.osv FROM wkf, wkf_activity, wkf_transition ' \
            'WHERE wkf.id = wkf_activity.wkf_id AND wkf_activity.id = wkf_transition.act_from' \
            '  AND wkf_transition.id = %s',
                (res,), debug=self._debug)
        r = cr.fetchone()
        wf_service = netsvc.LocalService("workflow")
        wf_service.reload_models(cr, [r[0],])
        return res

    def write(self, cr, user, ids, vals, context=None):
        res = super(wkf_transition, self).write(cr, user, ids, vals, context=context)
        cr.execute('SELECT DISTINCT wkf.osv FROM wkf, wkf_activity, wkf_transition '
                    'WHERE wkf.id = wkf_activity.wkf_id AND wkf_activity.id = wkf_transition.act_from'
                    '  AND wkf_transition.id = ANY(%s)', 
                    (ids,), debug=self._debug)
        wf_service = netsvc.LocalService("workflow")
        wf_service.reload_models(cr, [r[0] for r in cr.fetchall()])
        return res

    def unlink(self, cr, uid, ids, context=None):
        cr.execute('SELECT DISTINCT wkf.osv FROM wkf, wkf_activity '
                    'WHERE wkf.id = wkf_activity.wkf_id AND wkf_activity.id = wkf_transition.act_from'
                    '  AND wkf_transition.id = ANY(%s)',
                    (ids,), debug=self._debug)
        models = [r[0] for r in cr.fetchall()]
        res = super(wkf_transition, self).unlink(cr, uid, ids, context=context)
        wf_service = netsvc.LocalService("workflow")
        wf_service.reload_models(cr, models)
        return res

wkf_transition()

class wkf_instance(osv.osv):
    _table = "wkf_instance"
    _name = "workflow.instance"
    _rec_name = 'res_type'
    _log_access = False

    _columns = {
        'wkf_id': fields.many2one('workflow', 'Workflow', ondelete='restrict'),
        'res_id': fields.integer('Resource ID', required=True),
        'res_type': fields.char('Resource Object', size=64, required=True),
        'state': fields.char('State', size=32, required=True),
    }
    _defaults = {
        'state': 'active',
    }

    _indices = {
        'res_type_res_id_state_index': index.plain('res_type', 'res_id', 'state'),
        'wkf_id_res_id_index': index.plain('wkf_id', 'res_id'),
    }

wkf_instance()

class wkf_workitem(osv.osv):
    _table = "wkf_workitem"
    _name = "workflow.workitem"
    _log_access = False
    _rec_name = 'state'

    _columns = {
        'act_id': fields.many2one('workflow.activity', 'Activity', required=True, ondelete="restrict", select=True),
        'wkf_id': fields.related('act_id','wkf_id', type='many2one', relation='workflow', string='Workflow'),
        'subflow_id': fields.many2one('workflow.instance', 'Subflow', ondelete="cascade", select=True),
        'inst_id': fields.many2one('workflow.instance', 'Instance', required=True, ondelete="cascade", select=True),
        'state': fields.char('State', size=64, select=True),
    }

    _defaults = {
        'state': 'blocked',
    }

wkf_workitem()

class wkf_triggers(osv.osv):
    _table = "wkf_triggers"
    _name = "workflow.triggers"
    _log_access = False

    _columns = {
        'res_id': fields.integer('Resource ID', size=128, required=True),
        'model': fields.char('Object', size=128, required=True),
        'instance_id': fields.many2one('workflow.instance', 'Destination Instance', ondelete="cascade"),
        'workitem_id': fields.many2one('workflow.workitem', 'Workitem', required=True, ondelete="cascade"),
    }

    _indices = {
        'res_id_model_index' : index.plain('res_id', 'model'),
    }

wkf_triggers()


# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
