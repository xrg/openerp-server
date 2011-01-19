# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2010, 2011 OpenERP SA. (http://www.openerp.com)
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


from osv import fields, osv
from locale import localeconv
import tools
from tools.translate import _
from tools.amount_to_text_en import amount_to_text as a2text_orig

class amount_to_text(osv.osv_memory):
    """ The amount-to-text processor object.
    
        This is a temporary object, that can return a textual representation of
        (hopefully) any arbitrary amount.
        The logic behind this object is a compromise between the OpenERP ORM and
        the need to inherit some object for every localization. Our purpose is
        to provide an object that can clearly be extended to all countries.
        
        Usage in code:
    """
    _name = 'res.amount_to_text'
    _description = 'Convert amount to text'
    
    def _amount_to_text_en(self, cr, uid, ids, name, arg, context=None):
        abo = self.browse(cr, uid, ids, context=context)
        res = {}
        for a in abo:
            res[a.id] = a2text_orig(a.name, a.currency_id and a.currency_id.name or '')
        return res

    _columns = {
        'name': fields.float('Value', required=True, digits=None),
        'currency_id': fields.many2one('res.currency', 'Currency', required=False,
                            help="If specified, the value is amount of that currency"),
        # the 'product' module shall also define an 'uom_id' column here ;)

        'en': fields.function(_amount_to_text_en, 'English', method=True),
        }
        
    def this_to_text(self, cr, uid, ids, lang=None, context=None):
        """ Convert the 'value' of this instance into text.
        Respect either the explicit 'lang' argument or context['lang'], if any
        
        returns list of dicts: { 'id': , 'value':, lang:, 'text': =lang }
        """
        if not lang:
            lang = (context or {}).get('lang', 'en')
        
        if lang not in self._columns:
            if '_' in lang:
                lang = lang.split('_',1)[0]
        
        if lang not in self._columns:
            raise osv.orm.except_orm(_('Error!'), _("Cannot convert amount to %s, no language function present!") % lang)

        res = self.read(cr, uid, ids, ['name', lang])
        for r in res:
            r['text'] = r[lang]
        return res

    def amount_to_text(self, cr, uid, amount, lang=None, currency_id=None, uom_id=None, context=None):
        """ The full amount-to-text process.
        
            :param uom_id: is provided for 'product' module to override
        """
        
        if uom_id and 'uom_id' not in self._columns:
            raise NotImplementedError("The base implementation cannot handle UoM")
        
        vals = { 'name': amount }
        if currency_id:
            vals['currency_id'] = currency_id
        elif uom_id:
            vals['uom_id'] = uom_id
        
        id = self.create(cr, uid, vals, context=context)
        res = self.this_to_text(cr, uid, [id], lang=lang, context=context)
        return res[0]['text']

amount_to_text()


# This is how to extend it!
from tools.amount_to_text import amount_to_text as a2text_frnl_orig

class amount_to_text_frnl(osv.osv_memory):
    _inherit = 'res.amount_to_text'

    def _amount_to_text_fr_nl(self, cr, uid, ids, name, arg, context=None):
        abo = self.browse(cr, uid, ids, context=context)
        res = {}
        for a in abo:
            res[a.id] = a2text_frnl_orig(a.name, lang=name, currency=(a.currency_id and a.currency_id.name or ''))
        return res

    _columns = {
        'fr': fields.function(_amount_to_text_fr_nl, 'French', method=True),
        'nl': fields.function(_amount_to_text_fr_nl, 'Dutch', method=True),
        }

amount_to_text_frnl()
#eof
