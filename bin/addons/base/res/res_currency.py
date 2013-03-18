# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2013 P. Christeas <xrg@hellug.gr>
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

import time
import netsvc
from osv import fields, osv
import ir

from tools.misc import currency, to_date
from tools.translate import _

class res_currency(osv.osv):
    def _current_rate(self, cr, uid, ids, name, arg, context=None):
        if context is None:
            context = {}
        res = {}
        date = to_date(context.get('date', None)) or fields.date.today()
        for id in ids:
            cr.execute("SELECT currency_id, rate FROM res_currency_rate WHERE currency_id = %s AND name <= %s ORDER BY name desc LIMIT 1" ,(id, date))
            if cr.rowcount:
                id, rate = cr.fetchone()
                res[id] = rate
            else:
                # should we return anything at all?
                res[id] = False
        return res

    def _check_rounding(self, cr, uid, ids, context=None):
        for currency in self.browse(cr, uid, ids, context=context):
            if currency.rounding == 0.0:
                return False
        return True

    _name = "res.currency"
    _description = "Currency"
    _columns = {
        # Note: 'code' column was removed as of v6.0, the 'name' should now hold the ISO code.
        'name': fields.char('Currency', size=32, required=True, help="Currency Code (ISO 4217)"),
        'symbol': fields.char('Symbol', size=8, help="Currency sign, to be used when printing amounts"),
        'rate': fields.function(_current_rate, method=True, string='Current Rate', digits=(12,6),
            help='The rate of the currency to the currency of rate 1'),
        'rate_ids': fields.one2many('res.currency.rate', 'currency_id', 'Rates'),
        'accuracy': fields.integer('Computational Accuracy'),
        'rounding': fields.float('Rounding factor', digits=(12,6)),
        'active': fields.boolean('Active'),
        'company_id':fields.many2one('res.company', 'Company'),
        'date': fields.date('Date'),
        'base': fields.boolean('Base')

    }
    _defaults = {
        'active': True,
        'company_id': lambda self,cr,uid,c: self.pool.get('res.company')._company_default_get(cr, uid, 'res.currency', context=c),
        'rounding': 0.01,
    }

    _sql_constraints = [
        ('currency_name_uniq', 'unique (name, company_id)', 'The currency code must be unique !'),
    ]
    _order = "name"

    _constraints = [(_check_rounding, "The rounding factor cannot be 0 !", ['rounding'])]

    def read(self, cr, user, ids, fields=None, context=None, load='_classic_read'):
        select = ids
        if isinstance(ids, (int, long)):
            select = [select]
        res = super(osv.osv, self).read(cr, user, select, fields, context, load)
        for r in res:
            if 'rate_ids' in r:
                rates=r['rate_ids']
                if rates:
                    currency_rate_obj=  self.pool.get('res.currency.rate')
                    currency_date = currency_rate_obj.read(cr,user,rates[0],['name'])['name']
                    r['date'] = currency_date
        if isinstance(ids, (int, long)):
            return res and res[0] or False
        return res

    def round(self, cr, uid, currency, amount):
        if currency.rounding == 0:
            return 0.0
        else:
            # /!\ First member below must be rounded to full unit!
            # Do not pass a rounding digits value to round()
            return round(amount / currency.rounding) * currency.rounding

    def is_zero(self, cr, uid, currency, amount):
        return abs(self.round(cr, uid, currency, amount)) < currency.rounding

    def _get_conversion_rate(self, cr, uid, from_currency, to_currency, context=None):
        if context is None:
            context = {}
        if from_currency['rate'] == 0 or to_currency['rate'] == 0:
            date = to_date(context.get('date', fields.date.today()))
            if from_currency['rate'] == 0:
                currency_symbol = from_currency.symbol
            else:
                currency_symbol = to_currency.symbol
            raise osv.except_osv(_('Error'), _('No rate found \n' \
                    'for the currency: %s \n' \
                    'at the date: %s') % (currency_symbol, str(date))) # TODO: l18n date format
        return to_currency.rate/from_currency.rate

    def compute(self, cr, uid, from_currency_id, to_currency_id, from_amount, round=True, context=None):
        if not from_currency_id:
            from_currency_id = to_currency_id
        if not to_currency_id:
            to_currency_id = from_currency_id
        xc = self.browse(cr, uid, [from_currency_id,to_currency_id], context=context)
        from_currency = (xc[0].id == from_currency_id and xc[0]) or xc[1]
        to_currency = (xc[0].id == to_currency_id and xc[0]) or xc[1]
        if to_currency_id == from_currency_id:
            if round:
                return self.round(cr, uid, to_currency, from_amount)
            else:
                return from_amount
        else:
            rate = self._get_conversion_rate(cr, uid, from_currency, to_currency, context=context)
            if round:
                return self.round(cr, uid, to_currency, from_amount * rate)
            else:
                return (from_amount * rate)

    def name_search(self, cr, uid, name, args=[], operator='ilike', context=None, limit=100):
        args = args[:]
        if name:
            args += [('name', operator, name)]
        ids = self.search(cr, uid, args, limit=limit)
        res = self.name_get(cr, uid, ids, context)
        return res
res_currency()

class res_currency_rate(osv.osv):
    _name = "res.currency.rate"
    _description = "Currency Rate"
    _columns = {
        'name': fields.date('Date', required=True, select=True),
        'rate': fields.float('Rate', digits=(12,6), required=True,
            help='The rate of the currency to the currency of rate 1'),
        'currency_id': fields.many2one('res.currency', 'Currency', readonly=True),
    }
    _defaults = {
        'name': fields.date.today,
    }
    _order = "name desc"
res_currency_rate()

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

