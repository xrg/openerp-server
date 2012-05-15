# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2010 Tiny SPRL (<http://tiny.be>).
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

from osv import osv, fields, index
from tools.translate import _

class ir_filters(osv.osv):
    '''
    Filters
    '''
    _name = 'ir.filters'
    _description = 'Filters'

    def _list_all_models(self, cr, uid, context=None):
        cr.execute("SELECT model, name from ir_model")
        return cr.fetchall()

    def get_filters(self, cr, uid, model, context=None):
        act_ids = self.search_read(cr, uid, [('model_id','=',model),
                '|', ('user_id', '=', False),('user_id','=',uid)], context=context)
        return act_ids

    def create_or_replace(self, cr, uid, vals, context=None):
        filter_id = None
        can_haz = self.search(cr, uid, [('model_id', '=', vals['model_id']), \
                ('name', '=ilike', vals['name'].lower())], limit=1, context=context)
        if can_haz:
            self.write(cr, uid, can_haz[0], vals, context)
            return False
        return self.create(cr, uid, vals, context)

    _indices = {
        'name_model_uid_unique_index': index.unique('lower(name)', 'model_id', 'user_id'),
    }

    _columns = {
        'name': fields.char('Action Name', size=64, translate=True, required=True),
        'user_id':fields.many2one('res.users', 'User', help='False means for every user'),
        'domain': fields.text('Domain Value', required=True),
        'context': fields.text('Context Value', required=True),
        'model_id': fields.selection(_list_all_models, 'Object', size=64, required=True),
    }

ir_filters()

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
