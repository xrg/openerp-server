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

import tools
from osv import osv, fields

class base_language_install(osv.osv_memory):
    """ Install Language"""

    _name = "base.language.install"
    _inherit = "ir.wizard.screen"
    _description = "Install Language"

    _columns = {
        'lang': fields.selection(tools.scan_languages(),'Language', required=True),
        'state':fields.selection([('init','init'),('done','done')], 'state', readonly=True),
    }

    _defaults = {  
        'state': 'init',
    }

    def lang_install(self, cr, uid, ids, context):
        language_obj = self.browse(cr, uid, ids)[0]
        lang = language_obj.lang
        if lang:
            modobj = self.pool.get('ir.module.module')
            mids = modobj.search(cr, uid, [('state', '=', 'installed')])
            modobj.update_translations(cr, uid, mids, lang)
            self.write(cr, uid, ids, {'state': 'done'}, context=context)
        return False

base_language_install()

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4: