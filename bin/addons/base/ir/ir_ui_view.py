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

from osv import fields, osv, index, views
from tools import graph
from tools.safe_eval import safe_eval as eval
from tools.translate import _
import tools
import os

def _check_xml(self, cr, uid, ids, context=None):
    for view in self.browse(cr, uid, ids, context):
        if not views.oo_view[view.type].check_xml(view.arch, errors=None):
            return False
    return True

class view_custom(osv.osv):
    _name = 'ir.ui.view.custom'
    _columns = {
        'ref_id': fields.many2one('ir.ui.view', 'Original View', select=True),
        'user_id': fields.many2one('res.users', 'User', select=True),
        'arch': fields.text('View Architecture', required=True),
    }

    _indices = {
        'user_id_ref_id': index.plain('user_id', 'ref_id'),
    }

view_custom()

class view(osv.osv):
    _name = 'ir.ui.view'
    _columns = {
        'name': fields.char('View Name',size=64,  required=True),
        'model': fields.char('Object', size=64, required=True, select=True),
        'priority': fields.integer('Sequence', required=True),
        'type': fields.selection(views.oo_view.get_view_types, 'View Type', required=True, select=True),
        'arch': fields.text('View Architecture', required=True),
        'inherit_id': fields.many2one('ir.ui.view', 'Inherited View', ondelete='cascade', select=True),
        'field_parent': fields.char('Child Field',size=64),
        'xml_id': fields.function(osv.osv.get_xml_id, type='char', size=128, string="XML ID",
                                  method=True, help="ID of the view defined in xml file"),
    }
    _defaults = {
        'arch': '<?xml version="1.0"?>\n<tree string="My view">\n\t<field name="name"/>\n</tree>',
        'priority': 16,
        'type': 'form',
    }
    def _invalid_xml_message(self, cr, uid, ids, context=None):
        """ Only print the message, through a callable
        We want this msg to come through a callable, so that the
        translation engine will not repeat it for each view registered
        """
        return _('Invalid XML for View Architecture!'), ()

    _order = "priority,name"
    
    _constraints = [
        (_check_xml, _invalid_xml_message , ['arch'])
    ]

    _indices = {
        'model_type_idx': index.plain('model', 'type'),
    }

    def graph_get(self, cr, uid, id, model, node_obj, conn_obj, src_node, des_node,label,scale,context=None):
        if not label:
            label = []
        nodes=[]
        nodes_name=[]
        transitions=[]
        start=[]
        tres={}
        labels={}
        no_ancester=[]
        blank_nodes = []

        _Model_Obj=self.pool.get(model)
        _Node_Obj=self.pool.get(node_obj)
        _Arrow_Obj=self.pool.get(conn_obj)

        for model_key,model_value in _Model_Obj._columns.items():
                if model_value._type=='one2many':
                    if model_value._obj==node_obj:
                        _Node_Field=model_key
                        _Model_Field=model_value._fields_id
                    flag=False
                    for node_key,node_value in _Node_Obj._columns.items():
                        if node_value._type=='one2many':
                             if node_value._obj==conn_obj:
                                 if src_node in _Arrow_Obj._columns and flag:
                                    _Source_Field=node_key
                                 if des_node in _Arrow_Obj._columns and not flag:
                                    _Destination_Field=node_key
                                    flag = True

        datas = _Model_Obj.read(cr, uid, id, [],context)
        for a in _Node_Obj.read(cr,uid,datas[_Node_Field],[]):
            if a[_Source_Field] or a[_Destination_Field]:
                nodes_name.append((a['id'],a['name']))
                nodes.append(a['id'])
            else:
                blank_nodes.append({'id': a['id'],'name':a['name']})

            if a.has_key('flow_start') and a['flow_start']:
                start.append(a['id'])
            else:
                if not a[_Source_Field]:
                    no_ancester.append(a['id'])
            for t in _Arrow_Obj.read(cr,uid, a[_Destination_Field],[]):
                transitions.append((a['id'], t[des_node][0]))
                tres[str(t['id'])] = (a['id'],t[des_node][0])
                label_string = ""
                if label:
                    for lbl in eval(label):
                        if t.has_key(str(lbl)) and str(t[lbl])=='False':
                            label_string = label_string + ' '
                        else:
                            label_string = label_string + " " + t[lbl]
                labels[str(t['id'])] = (a['id'],label_string)
        g  = graph(nodes, transitions, no_ancester)
        g.process(start)
        g.scale(*scale)
        result = g.result_get()
        results = {}
        for node in nodes_name:
            results[str(node[0])] = result[node[0]]
            results[str(node[0])]['name'] = node[1]
        return {'nodes': results,
                'transitions': tres,
                'label' : labels,
                'blank_nodes': blank_nodes,
                'node_parent_field': _Model_Field,}
view()

class view_sc(osv.osv):
    _name = 'ir.ui.view_sc'
    _columns = {
        'name': fields.char('Shortcut Name', size=64), # Kept for backwards compatibility only - resource name used instead (translatable)
        'res_id': fields.integer('Resource Ref.', help="Reference of the target resource, whose model/table depends on the 'Resource Name' field."),
        'sequence': fields.integer('Sequence'),
        'user_id': fields.many2one('res.users', 'User Ref.', required=True, ondelete='cascade', select=True),
        'resource': fields.char('Resource Name', size=64, required=True, select=True)
    }

    def get_sc(self, cr, uid, user_id, model='ir.ui.menu', context=None):
        ids = self.search(cr, uid, [('user_id','=',user_id),('resource','=',model)], context=context)
        results = self.read(cr, uid, ids, ['res_id','name'], context=context)
        available_menus = self.pool.get(model).search(cr, uid, [], context=context)
        # Make sure to return only shortcuts pointing to exisintg menu items.
        return filter(lambda result: result['res_id'] in available_menus, results)

    _order = 'sequence,name'
    _defaults = {
        'resource': 'ir.ui.menu',
        'user_id': lambda obj, cr, uid, context: uid,
    }
    _sql_constraints = [
        ('shortcut_unique', 'unique(res_id, resource, user_id)', 'Shortcut for this menu already exists!'),
    ]

    _indices = {
        'user_id_resource': index.plain('user_id', 'resource'),
    }

view_sc()

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

