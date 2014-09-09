# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP/F3, Open Source Management Solution
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

from osv import fields,osv
from tools.safe_eval import safe_eval, ExecContext
import tools
import time
# from tools.config import config
from tools.translate import _
import netsvc
import logging
import os
from tools.date_eval import date_eval
from report.report_sxw import report_sxw, report_rml
from tools.orm_utils import only_ids


def eval(*args, **kwargs):
    "We must always use safe_eval() here"
    raise RuntimeError()

class actions(osv.osv):
    _name = 'ir.actions.actions'
    _table = 'ir_actions'
    _order = 'name'
    _columns = {
        'name': fields.char('Action Name', required=True, size=64),
        'type': fields.char('Action Type', required=True, size=32,readonly=True),
        'usage': fields.char('Action Usage', size=32),
    }
    _defaults = {
        'usage': False,
    }
actions()


class report_xml(osv.osv):
    _function_field_browse = True

    def _report_content(self, cursor, user, ids, name, arg, context=None):
        res = {}
        for report in self.browse(cursor, user, ids, context=context):
            data = report[name + '_data']
            if not data and report[name[:-8]]:
                fp = None
                try:
                    fp = tools.file_open(report[name[:-8]], mode='rb')
                    data = fp.read()
                except Exception:
                    data = False
                finally:
                    if fp:
                        fp.close()
            res[report.id] = data
        return res

    def _report_content_inv(self, cursor, user, id, name, value, arg, context=None):
        self.write(cursor, user, id, {name+'_data': value}, context=context)

    def _report_sxw(self, cursor, user, ids, name, arg, context=None):
        res = {}
        for report in self.browse(cursor, user, ids, context=context):
            if report.report_rml:
                res[report.id] = report.report_rml.replace('.rml', '.sxw')
            else:
                res[report.id] = False
        return res

    def register_all(self, cr):
        """Report registration handler that may be overridden by subclasses to
           add their own kinds of report services.
           Loads all reports with no manual loaders (auto==True) and
           registers the appropriate services to implement them.
        """
        opj = os.path.join
        cr.execute("SELECT * FROM ir_act_report_xml WHERE auto=%s ORDER BY id", (True,))
        result = cr.dictfetchall()
        svcs = netsvc.Service._services
        # FIXME: there is a conflict if a newer module tries to upgrade with
        # a report moving from "auto" to a non-auto code.
        for r in result:
            if svcs.has_key('report.'+r['report_name']):
                continue
            if r['report_rml'] or r['report_rml_content_data']:
                report_sxw('report.'+r['report_name'], r['model'],
                        opj('addons',r['report_rml'] or '/'), header=r['header'])
            if r['report_xsl']:
                report_rml('report.'+r['report_name'], r['model'],
                        opj('addons',r['report_xml']),
                        r['report_xsl'] and opj('addons',r['report_xsl']))

    _name = 'ir.actions.report.xml'
    _table = 'ir_act_report_xml'
    _sequence = 'ir_actions_id_seq'
    _order = 'name'
    _columns = {
        'name': fields.char('Name', size=64, required=True, translate=True),
        'model': fields.char('Object', size=64, required=True),
        'type': fields.char('Action Type', size=32, required=True),
        'report_name': fields.char('Service Name', size=64, required=True),
        'usage': fields.char('Action Usage', size=32),
        'report_type': fields.char('Report Type', size=32, required=True, help="Report Type, e.g. pdf, html, raw, sxw, odt, html2html, mako2html, ..."),
        'groups_id': fields.many2many('res.groups', 'res_groups_report_rel', 'uid', 'gid', 'Groups'),
        'multi': fields.boolean('On multiple doc.', help="If set to true, the action will not be displayed on the right toolbar of a form view."),
        'attachment': fields.char('Save As Attachment Prefix', size=128, help='This is the filename of the attachment used to store the printing result. Keep empty to not save the printed reports. You can use a python expression with the object and time variables.'),
        'attachment_use': fields.boolean('Reload from Attachment', help='If you check this, then the second time the user prints with same attachment name, it returns the previous report.'),
        'auto': fields.boolean('Custom python parser', required=True),

        'header': fields.boolean('Add RML header', help="Add or not the coporate RML header"),

        'report_xsl': fields.char('XSL path', size=256),
        'report_xml': fields.char('XML path', size=256, help=''),

        # Pending deprecation... to be replaced by report_file as this object will become the default report object (not so specific to RML anymore)
        'report_rml': fields.char('Main report file path', size=256, help="The path to the main report file (depending on Report Type) or NULL if the content is in another data field"),
        # temporary related field as report_rml is pending deprecation - this field will replace report_rml after v6.0
        'report_file': fields.related('report_rml', type="char", size=256, required=False, readonly=False, string='Report file', help="The path to the main report file (depending on Report Type) or NULL if the content is in another field", store=True),

        'report_sxw': fields.function(_report_sxw, method=True, type='char', string='SXW path'),
        'report_sxw_content_data': fields.binary('SXW content'),
        'report_rml_content_data': fields.binary('RML content'),
        'report_sxw_content': fields.function(_report_content, fnct_inv=_report_content_inv, method=True, type='binary', string='SXW content',),
        'report_rml_content': fields.function(_report_content, fnct_inv=_report_content_inv, method=True, type='binary', string='RML content'),

    }
    _defaults = {
        'type': 'ir.actions.report.xml',
        'multi': False,
        'auto': True,
        'header': True,
        'report_sxw_content': False,
        'report_type': 'pdf',
        'attachment': False,
    }

report_xml()

class act_window(osv.osv):
    _name = 'ir.actions.act_window'
    _table = 'ir_act_window'
    _sequence = 'ir_actions_id_seq'
    _order = 'name'
    _function_field_browse = True

    def _check_model(self, cr, uid, ids, context=None):
        for action in self.browse(cr, uid, ids, context):
            if not self.pool.get(action.res_model):
                return False
            if action.src_model and not self.pool.get(action.src_model):
                return False
        return True

    def _invalid_model_msg(self, cr, uid, ids, context=None):
        return _('Invalid model name in the action definition.')

    _constraints = [
        (_check_model, _invalid_model_msg, ['res_model','src_model'])
    ]

    def _views_get_fnc(self, cr, uid, ids, name, arg, context=None):
        res={}
        for act in self.browse(cr, uid, ids):
            res[act.id]=[(view.view_id.id, view.view_mode) for view in act.view_ids]
            modes = act.view_mode.split(',')
            if len(modes)>len(act.view_ids):
                find = False
                if act.view_id:
                    res[act.id].append((act.view_id.id, act.view_id.type))
                for t in modes[len(act.view_ids):]:
                    if act.view_id and (t == act.view_id.type) and not find:
                        find = True
                        continue
                    res[act.id].append((False, t))
        return res

    def _search_view(self, cr, uid, ids, name, arg, context=None):
        res = {}
        def encode(s):
            if isinstance(s, unicode):
                return s.encode('utf8')
            return s
        for act in self.browse(cr, uid, ids, fields_only=['res_model', 'search_view_id', 'view_mode'], context=context):
            assert act.res_model, act.id
            act_model = self.pool.get(act.res_model)
            assert act_model, 'No model %s for action #%d %s' % \
                                (act.res_model, act.id, act.name)
            fields_from_fields_get = act_model.fields_get(cr, uid, context=context)
            search_view_id = False
            if act.search_view_id:
                search_view_id = act.search_view_id.id
            elif act.view_mode == 'form':
                return res # avoid search view for a form-only action
            else:
                res_view = self.pool.get('ir.ui.view').search(cr, uid,
                        [('model','=',act.res_model),('type','=','search'),
                        ('inherit_id','=',False)], context=context)
                if res_view:
                    search_view_id = res_view[0]
            if True:
                field_get = act_model.fields_view_get(cr, uid, search_view_id or False,
                            'search', context)
                fields_from_fields_get.update(field_get['fields'])
                field_get['fields'] = fields_from_fields_get
                res[act.id] = str(field_get) # TODO: remove str() after client has adapted

        return res

    def _get_help_status(self, cr, uid, ids, name, arg, context=None):
        activate_tips = self.pool.get('res.users').browse(cr, uid, uid, context=context).menu_tips
        return dict.fromkeys(only_ids(ids), activate_tips)

    _columns = {
        'name': fields.char('Action Name', size=64, translate=True),
        'type': fields.char('Action Type', size=32, required=True),
        'view_id': fields.many2one('ir.ui.view', 'View Ref.', ondelete='cascade'),
        'domain': fields.char('Domain Value', size=250,
            help="Optional domain filtering of the destination data, as a Python expression"),
        'context': fields.char('Context Value', size=250, required=True,
            help="Context dictionary as Python expression, empty by default (Default: {})"),
        'res_model': fields.char('Object', size=64, required=True,
            help="Model name of the object to open in the view window"),
        'src_model': fields.char('Source Object', size=64,
            help="Optional model name of the objects on which this action should be visible"),
        'target': fields.selection([('current','Current Window'),('new','New Window')], 'Target Window'),
        'view_type': fields.selection((('tree','Tree'),('form','Form')), string='View Type', required=True,
            help="View type: set to 'tree' for a hierarchical tree view, or 'form' for other views"),
        'view_mode': fields.char('View Mode', size=250, required=True,
            help="Comma-separated list of allowed view modes, such as 'form', 'tree', 'calendar', etc. (Default: tree,form)"),
        'usage': fields.char('Action Usage', size=32),
        'view_ids': fields.one2many('ir.actions.act_window.view', 'act_window_id', 'Views'),
        'views': fields.function(_views_get_fnc, method=True, type='binary', string='Views'),
        'limit': fields.integer('Limit', help='Default limit for the list view'),
        'auto_refresh': fields.integer('Auto-Refresh',
            help='Add an auto-refresh on the view'),
        'groups_id': fields.many2many('res.groups', 'ir_act_window_group_rel',
            'act_id', 'gid', 'Groups'),
        'search_view_id': fields.many2one('ir.ui.view', 'Search View Ref.'),
        'filter': fields.boolean('Filter'),
        'auto_search':fields.boolean('Auto Search'),
        'search_view' : fields.function(_search_view, type='text', method=True, string='Search View'),
        'menus': fields.char('Menus', size=4096),
        'help': fields.text('Action description',
            help='Optional help text for the users with a description of the target view, such as its usage and purpose.',
            translate=True),
        'display_menu_tip':fields.function(_get_help_status, type='boolean', method=True, string='Display Menu Tips',
            help='It gives the status if the tip has to be displayed or not when a user executes an action'),
        'multi': fields.boolean('Action on Multiple Doc.', help="If set to true, the action will not be displayed on the right toolbar of a form view"),
    }

    _defaults = {
        'type': 'ir.actions.act_window',
        'view_type': 'form',
        'view_mode': 'tree,form',
        'context': '{}',
        'limit': 80,
        'target': 'current',
        'auto_refresh': 0,
        'auto_search': True,
        'multi': False,
    }

    def for_xml_id(self, cr, uid, module, xml_id, context=None):
        """ Returns the act_window object created for the provided xml_id

        :param module: the module the act_window originates in
        :param xml_id: the namespace-less id of the action (the @id
                       attribute from the XML file)
        :return: A read() view of the ir.actions.act_window

        Deprecated! you can use the generic [('id.ref', '=', ...)] domain instead.
        """
        dataobj = self.pool.get('ir.model.data')
        model, res_id = dataobj.get_object_reference(cr, 1, module, xml_id)
        return self.read(cr, uid, res_id, [], context)

act_window()

class act_window_view(osv.osv):
    _name = 'ir.actions.act_window.view'
    _table = 'ir_act_window_view'
    _rec_name = 'view_id'
    _columns = {
        'sequence': fields.integer('Sequence'),
        'view_id': fields.many2one('ir.ui.view', 'View'),
        'view_mode': fields.selection((
            ('tree', 'Tree'),
            ('form', 'Form'),
            ('graph', 'Graph'),
            ('calendar', 'Calendar'),
            ('gantt', 'Gantt')), string='View Type', required=True),
        'act_window_id': fields.many2one('ir.actions.act_window', 'Action', ondelete='cascade'),
        'multi': fields.boolean('On Multiple Doc.',
            help="If set to true, the action will not be displayed on the right toolbar of a form view."),
    }
    _defaults = {
        'multi': False,
    }
    _order = 'sequence'
act_window_view()

class act_wizard(osv.osv):
    _name = 'ir.actions.wizard'
    _inherit = 'ir.actions.actions'
    _table = 'ir_act_wizard'
    _sequence = 'ir_actions_id_seq'
    _order = 'name'
    _columns = {
        'name': fields.char('Wizard Info', size=64, required=True, translate=True),
        'type': fields.char('Action Type', size=32, required=True),
        'wiz_name': fields.char('Wizard Name', size=64, required=True),
        'multi': fields.boolean('Action on Multiple Doc.', help="If set to true, the wizard will not be displayed on the right toolbar of a form view."),
        'groups_id': fields.many2many('res.groups', 'res_groups_wizard_rel', 'uid', 'gid', 'Groups'),
        'model': fields.char('Object', size=64),
    }
    _defaults = {
        'type': 'ir.actions.wizard',
        'multi': False,
    }
act_wizard()

class act_url(osv.osv):
    _name = 'ir.actions.url'
    _table = 'ir_act_url'
    _sequence = 'ir_actions_id_seq'
    _order = 'name'
    _columns = {
        'name': fields.char('Action Name', size=64, translate=True),
        'type': fields.char('Action Type', size=32, required=True),
        'url': fields.text('Action URL',required=True),
        'target': fields.selection((
            ('new', 'New Window'),
            ('self', 'This Window')),
            'Action Target', required=True
        )
    }
    _defaults = {
        'type': 'ir.actions.act_url',
        'target': 'new'
    }
act_url()

def model_get(self, cr, uid, context=None):
    wkf_pool = self.pool.get('workflow')
    osvs = wkf_pool.search_read(cr, uid, [], ['osv'])

    res = []
    mpool = self.pool.get('ir.model')
    for osv in osvs:
        model = osv.get('osv')
        name = mpool.search_read(cr, uid, [('model','=',model)])[0]['name']
        res.append((model, name))

    return res

class ir_model_fields(osv.osv):
    _inherit = 'ir.model.fields'
    _rec_name = 'field_description'
    _columns = {
        'complete_name': fields.char('Complete Name', size=64, select=1),
    }
ir_model_fields()

class server_object_lines(osv.osv):
    _name = 'ir.server.object.lines'
    _sequence = 'ir_actions_id_seq'
    _columns = {
        'server_id': fields.many2one('ir.actions.server', 'Object Mapping'),
        'col1': fields.many2one('ir.model.fields', 'Destination', required=True),
        'value': fields.text('Value', required=True),
        'type': fields.selection([
            ('value','Value'),
            ('equation','Formula')
        ], 'Type', required=True, size=32, change_default=True),
    }
    _defaults = {
        'type': 'equation',
    }
server_object_lines()

class ActionsExecContext(ExecContext):
    _name = 'ir.actions'
    _logger_name = 'orm.ir.actions.exec'

    def prepare_context(self, context):
        self._prepare_orm(context)
        self._prepare_logger(context)
        context['obj'] = self.obj
        context['dbname'] = self.cr.dbname
        context['date_eval'] = date_eval
        context['id'] = id
        context['hash'] = hash
        context['hex'] = hex

##
# Actions that are run on the server side
#
class actions_server(osv.osv):

    def _select_signals(self, cr, uid, context=None):
        cr.execute_prepared('actions_server_sel_signals', "SELECT distinct w.osv, t.signal FROM wkf w, wkf_activity a, wkf_transition t \
        WHERE w.id = a.wkf_id  AND ( t.act_from = a.id OR t.act_to = a.id ) AND t.signal!='' \
        AND t.signal IS NOT NULL", debug=self._debug)
        result = cr.fetchall() or []
        res = []
        for rs in result:
            if rs[0] is not None and rs[1] is not None:
                line = rs[0], "%s - (%s)" % (rs[1], rs[0])
                res.append(line)
        return res

    def _select_objects(self, cr, uid, context=None):
        model_pool = self.pool.get('ir.model')
        ids = model_pool.search(cr, uid, [('name','not ilike','.')])
        res = model_pool.read(cr, uid, ids, ['model', 'name'])
        return [(r['model'], r['name']) for r in res] +  [('','')]

    def change_object(self, cr, uid, ids, copy_object, state, context=None):
        if state == 'object_copy':
            model_pool = self.pool.get('ir.model')
            model = copy_object.split(',')[0]
            mid = model_pool.search(cr, uid, [('model','=',model)])
            return {
                'value':{'srcmodel_id':mid[0]},
                'context':context
            }
        else:
            return {}

    _name = 'ir.actions.server'
    _table = 'ir_act_server'
    _sequence = 'ir_actions_id_seq'
    _order = 'sequence,name'
    _columns = {
        'name': fields.char('Action Name', required=True, size=64, help="Easy to Refer action by name e.g. One Sales Order -> Many Invoices", translate=True),
        'condition' : fields.char('Condition', size=256, required=True, help="Condition that is to be tested before action is executed, e.g. object.list_price > object.cost_price"),
        'state': fields.selection([
            ('client_action','Client Action'),
            ('dummy','Dummy'),
            ('loop','Iteration'),
            ('code','Python Code'),
            ('scode', 'Secure code'),
            ('trigger','Trigger'),
            ('email','Email (obsolete)'),
            ('sms','SMS (obsolete)'),
            ('object_create','Create Object'),
            ('object_copy','Copy Object'),
            ('object_write','Write Object'),
            ('other','Multi Actions'),
        ], 'Action Type', required=True, size=32, help="Type of the Action that is to be executed"),
        'code':fields.text('Python Code', help="Python code to be executed"),
        'sequence': fields.integer('Sequence', help="Important when you deal with multiple actions, the execution order will be decided based on this, low number is higher priority."),
        'model_id': fields.many2one('ir.model', 'Object', required=True, help="Select the object on which the action will work (read, write, create)."),
        'action_id': fields.many2one('ir.actions.actions', 'Client Action', help="Select the Action Window, Report, Wizard to be executed."),
        'trigger_name': fields.selection(_select_signals, string='Trigger Name', size=128, help="Select the Signal name that is to be used as the trigger."),
        'wkf_model_id': fields.many2one('ir.model', 'Workflow On', help="Workflow to be executed on this model."),
        'trigger_obj_id': fields.many2one('ir.model.fields','Trigger On', help="Select the object from the model on which the workflow will executed."),
        'email': fields.char('Email Address', size=512),
        'subject': fields.char('Subject', size=1024, translate=True),
        'message': fields.text('Message', translate=True),
        'mobile': fields.char('Mobile No', size=512, ),
        'sms': fields.char('SMS', size=160, translate=True),
        'child_ids': fields.many2many('ir.actions.server', 'rel_server_actions', 'server_id', 'action_id', 'Other Actions'),
        'usage': fields.char('Action Usage', size=32),
        'type': fields.char('Action Type', size=32, required=True),
        'srcmodel_id': fields.many2one('ir.model', 'Model', help="Object in which you want to create / write the object. If it is empty then refer to the Object field."),
        'fields_lines': fields.one2many('ir.server.object.lines', 'server_id', 'Field Mappings.'),
        'record_id':fields.many2one('ir.model.fields', 'Create Id', help="Provide the field name where the record id is stored after the create operations. If it is empty, you can not track the new record."),
        'write_id':fields.char('Write Id', size=256, help="Provide the field name that the record id refers to for the write operation. If it is empty it will refer to the active id of the object."),
        'loop_action':fields.many2one('ir.actions.server', 'Loop Action', help="Select the action that will be executed. Loop action will not be avaliable inside loop."),
        'expression':fields.char('Loop Expression', size=512, help="Enter the field/expression that will return the list. E.g. select the sale order in Object, and you can have loop on the sales order line. Expression = `object.order_line`."),
        'copy_object': fields.reference('Copy Of', selection=_select_objects, size=256),
    }
    _defaults = {
        'state': 'dummy',
        'condition': 'True',
        'type': 'ir.actions.server',
        'sequence': 5,
        'code': """# You can use the following variables
#    - object or obj
#    - time
#    - cr (Insecure only)
#    - uid (Insecure only)
#    - ids
# If you plan to return an action, assign: action = {...}
""",
    }

    # Context should contain:
    #   ids : original ids
    #   id  : current id of the object
    # OUT:
    #   False : Finished correctly
    #   ACTION_ID : Action to launch

    def run(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        eval_ctx = ActionsExecContext.new(cr=cr, uid=uid, pool=self.pool, context=context)

        for action in self.browse(cr, uid, ids, context):
            if context.get('active_id') \
                    and context.get('active_model', False) == action.model_id.model:
                model_obj = self.pool.get(action.model_id.model)
                eval_ctx.update(obj=model_obj.browse(cr, uid, context['active_id'], context=context, cache=action._cache))
            else:
                model_obj = None
                eval_ctx.update(obj=None)
            if action.condition and action.condition != 'True':
                ctx = {}
                eval_ctx.prepare_context(ctx)
                if not safe_eval(str(action.condition), ctx):
                    continue

            action_fn = getattr(self, '_run_'+action.state) # will raise exception

            res = action_fn(cr, uid, action, model_obj, eval_ctx, context)
            if res is not None:
                return res

        return False

    def _run_client_action(self, cr, uid, action, model_obj, eval_ctx, context):
        if not action.action_id:
            raise osv.except_osv(_('Error'), _("Please specify an action to launch !"))
        return self.pool.get(action.action_id.type) \
                    .read(cr, uid, action.action_id.id, context=context)

    def _run_code(self, cr, uid, action, model_obj, eval_ctx, context):
        localdict = {
            'self': self.pool.get(action.model_id.model),
            'context': dict(context), # copy context to prevent side-effects of eval
            'time': time,
            'cr': cr,
            'uid': uid,
            'object': eval_ctx.obj,
            'obj': eval_ctx.obj,
            }
        safe_eval(action.code, localdict, mode="exec", nocopy=True) # nocopy allows to return 'action'
        if 'action' in localdict:
            return localdict['action']

    def _run_scode(self, cr, uid, action, model_obj, eval_ctx, context):
        localdict = {}
        eval_ctx.prepare_context(localdict)
        if 'active_ids' in context:
            localdict['objs'] = model_obj.browse(cr, uid, context['active_ids'], context=context)

        safe_eval(action.code, localdict, mode="exec", nocopy=True) # nocopy allows to return 'action'
        if localdict.get('action', None) is not None:
            return localdict['action']

    def _run_email(self, cr, uid, action, model_obj, eval_ctx, context):
        raise NotImplementedError("Email actions must be replaced by 'base_messaging' commands")

    def _run_trigger(self, cr, uid, action, model_obj, eval_ctx, context):
        wf_service = netsvc.LocalService("workflow")
        model = action.wkf_model_id.model
        res_id = model_obj.read(cr, uid, [context.get('active_id')], [action.trigger_obj_id.name])
        id = res_id [0][action.trigger_obj_id.name]
        wf_service.trg_validate(uid, model, int(id), action.trigger_name, cr)

    def _run_sms(self, cr, uid, action, model_obj, eval_ctx, context):
        raise NotImplementedError("SMS actions must be replaced by 'base_messaging' commands")

    def _run_other(self, cr, uid, action, model_obj, eval_ctx, context):
        res = []
        for act in action.child_ids:
            context['active_id'] = context['active_ids'][0]
            result = self.run(cr, uid, [act.id], context)
            if result:
                res.append(result)
        return res

    def _run_loop(self, cr, uid, action, model_obj, eval_ctx, context):
        cxt = {
            'context': dict(context), # copy context to prevent side-effects of eval
            'object': eval_ctx.obj,
            'time': time,
            'cr': cr,
            'pool' : self.pool,
            'uid' : uid
            }
        expr = safe_eval(str(action.expression), cxt)
        context['object'] = eval_ctx.obj
        ret = []
        for i in expr:
            context['active_id'] = i.id
            r = self.run(cr, uid, [action.loop_action.id], context)
            if r:
                ret.append(r)
        if ret:
            # last result
            return ret[-1]

    def _run_object_write(self, cr, uid, action, model_obj, eval_ctx, context):
        res = {}
        for exp in action.fields_lines:
            euq = exp.value
            if exp.type == 'equation':
                obj = eval_ctx.obj
                cxt = {
                    'context': dict(context), # copy context to prevent side-effects of eval
                    'object': obj,
                    'time': time,
                }
                expr = safe_eval(euq, cxt)
            else:
                expr = exp.value
            res[exp.col1.name] = expr

        if not action.write_id:
            if not action.srcmodel_id:
                model_obj.write(cr, uid, [context.get('active_id')], res)
            else:
                write_id = context.get('active_id')
                obj_pool = self.pool.get(action.srcmodel_id.model)
                obj_pool.write(cr, uid, [write_id], res)

        elif action.write_id:
            obj_pool = self.pool.get(action.srcmodel_id.model)
            id = safe_eval(action.write_id, {'object': eval_ctx.obj})
            try:
                id = int(id)
            except:
                raise osv.except_osv(_('Error'), _("Problem in configuration `Record Id` in Server Action!"))

            if type(id) != type(1):
                raise osv.except_osv(_('Error'), _("Problem in configuration `Record Id` in Server Action!"))
            write_id = id
            obj_pool.write(cr, uid, [write_id], res)

    def _run_object_create(self, cr, uid, action, model_obj, eval_ctx, context):
        res = {}
        for exp in action.fields_lines:
            euq = exp.value
            if exp.type == 'equation':
                obj = eval_ctx.obj
                expr = safe_eval(euq, { 'context': dict(context), 'object': obj, 'time': time, })
            else:
                expr = exp.value
            res[exp.col1.name] = expr

        obj_pool = None
        res_id = False
        obj_pool = self.pool.get(action.srcmodel_id.model)
        res_id = obj_pool.create(cr, uid, res)
        if action.record_id:
            model_obj.write(cr, uid, [context.get('active_id')], {action.record_id.name:res_id})

    def _run_object_copy(self, cr, uid, action, model_obj, eval_ctx, context):
        res = {}
        for exp in action.fields_lines:
            euq = exp.value
            if exp.type == 'equation':
                expr = safe_eval(euq, { 'context': dict(context), 'object': eval_ctx.obj, 'time': time, })
            else:
                expr = exp.value
            res[exp.col1.name] = expr

        obj_pool = None
        res_id = False

        model, cid = action.copy_object.split(',', 1)
        obj_pool = self.pool.get(model)
        res_id = obj_pool.copy(cr, uid, int(cid), res)
        if action.record_id:
            model_obj.write(cr, uid, [context.get('active_id')], {action.record_id.name:res_id})

    def write(self, cr, user, ids, vals, context=None):
        """Hard-code a restriction for potentially privilege-escalating actions

            This is not done through ir.rules or ACLs, because it must apply to
            all databases, all time.
        """
        if user != 1:
            state = vals.get('state', False)
            if state:
                states = [state,]
            else:
                if isinstance(ids, (int, long)):
                    ids2 = [ids]
                else:
                    ids2 = ids
                states = [x['state'] for x in self.read(cr, user, ids2, fields=['state'], context=context)]

            for state in states:
                if state not in ('scode', 'dummy', 'client_action'):
                    raise osv.orm.except_orm(_('Permission Error!'), _('Only the admin user is allowed to write server actions of advanced type!'))

        return super(actions_server, self).write(cr, user, ids, vals, context=context)

    def create(self, cr, uid, vals, context=None):
        if uid != 1 and vals.get('state', 'dummy') not in ('scode', 'dummy', 'client_action'):
            raise osv.orm.except_orm(_('Permission Error!'), _('Only the admin user is allowed to create server actions of advanced type!'))
        return super(actions_server, self).create(cr, uid, vals, context=context)

actions_server()

class act_window_close(osv.osv):
    _name = 'ir.actions.act_window_close'
    _inherit = 'ir.actions.actions'
    _table = 'ir_actions'
    _defaults = {
        # Need a lambda here, because otherwise the default will go
        # to SQL and change the parent 'ir_actions' table!
        'type': lambda *a: 'ir.actions.act_window_close',
    }
act_window_close()

# This model use to register action services.
TODO_STATES = [('open', 'To Do'),
               ('done', 'Done'),
               ('skip','Skipped'),
               ('cancel','Cancelled')]

class ir_actions_todo(osv.osv):
    _name = 'ir.actions.todo'
    _columns={
        'action_id': fields.many2one(
            'ir.actions.act_window', 'Action', select=True, required=True,
            ondelete='cascade'),
        'sequence': fields.integer('Sequence'),
        'state': fields.selection(TODO_STATES, string='State', required=True),
        'name':fields.char('Name', size=64),
        'restart': fields.selection([('onskip','On Skip'),('always','Always'),('never','Never')],'Restart',required=True),
        'groups_id':fields.many2many('res.groups', 'res_groups_action_rel', 'uid', 'gid', 'Groups'),
        'note':fields.text('Text', translate=True),
    }
    _defaults={
        'state': 'open',
        'sequence': 10,
        'restart': 'onskip',
    }
    _order="sequence,name,id"

    def action_launch(self, cr, uid, ids, context=None):
        """ Launch Action of Wizard"""
        if context is None:
            context = {}
        wizard_id = ids and ids[0] or False
        wizard = self.browse(cr, uid, wizard_id, context=context)
        res = self.pool.get('ir.actions.act_window').read(cr, uid, wizard.action_id.id, ['name', 'view_type', 'view_mode', 'res_model', 'context', 'views', 'type'], context=context)
        res.update(target='new', nodestroy=True, context={'active_action_todo': wizard.id})
        return res

    def action_open(self, cr, uid, ids, context=None):
        """ Sets configuration wizard in TODO state"""
        return self.write(cr, uid, ids, {'state': 'open'}, context=context)

ir_actions_todo()

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

