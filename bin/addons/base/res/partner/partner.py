# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2011-2012 P. Christeas <xrg@hellug.gr>
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

import math

from osv import fields,osv
import tools
import pooler
from tools.translate import _
from tools.orm_utils import copy_empty

class res_payterm(osv.osv):
    _description = 'Payment term'
    _name = 'res.payterm'
    _order = 'name'
    _columns = {
        'name': fields.char('Payment Term (short name)', size=64),
    }
res_payterm()

class res_partner_category(osv.osv):
    def name_get(self, cr, uid, ids, context=None):
        if not len(ids):
            return []
        reads = self.read(cr, uid, ids, ['name','parent_id'], context=context)
        res = []
        for record in reads:
            name = record['name']
            if record['parent_id']:
                name = record['parent_id'][1]+' / '+name
            res.append((record['id'], name))
        return res

    def name_search(self, cr, uid, name, args=None, operator='ilike', context=None, limit=100):
        if not args:
            args=[]
        if not context:
            context={}
        if name:
            # Be sure name_search is symetric to name_get
            name = name.split(' / ')[-1]
            ids = self.search(cr, uid, [('name', operator, name)] + args, limit=limit, context=context)
        else:
            ids = self.search(cr, uid, args, limit=limit, context=context)
        return self.name_get(cr, uid, ids, context)


    def _name_get_fnc(self, cr, uid, ids, prop, unknow_none, context=None):
        res = self.name_get(cr, uid, ids, context=context)
        return dict(res)

    _description='Partner Categories'
    _name = 'res.partner.category'
    _columns = {
        'name': fields.char('Category Name', required=True, size=64, translate=True),
        'parent_id': fields.many2one('res.partner.category', 'Parent Category', select=True, ondelete='cascade'),
        'complete_name': fields.function(_name_get_fnc, method=True, type="char", string='Full Name'),
        'child_ids': fields.one2many('res.partner.category', 'parent_id', 'Child Categories'),
        'active' : fields.boolean('Active', required=True, help="The active field allows you to hide the category without removing it."),
    }
    _constraints = [
        (osv.osv._check_recursion, 'Error ! You can not create recursive categories.', ['parent_id'])
    ]
    _defaults = {
        'active': True,
    }
    _order = 'parent_id,name'

res_partner_category()

class res_partner_title(osv.osv):
    _name = 'res.partner.title'
    _columns = {
        'name': fields.char('Title', required=True, size=46, translate=True),
        'shortcut': fields.char('Shortcut', required=True, size=16, translate=True),
        'domain': fields.selection([('partner','Partner'),('contact','Contact')], 'Domain', required=True, size=24)
    }
    _order = 'name'

res_partner_title()

def _lang_get(self, cr, uid, context=None):
    obj = self.pool.get('res.lang')
    res = obj.search_read(cr, uid, [], fields=['code', 'name'], context=context)
    return [(r['code'], r['name']) for r in res] + [('','')]


class res_partner(osv.osv):
    _description='Partner'
    _name = "res.partner"
    _order = "name"
    _columns = {
        'name': fields.char('Name', size=128, required=True, select=True, copy_data='copy_copy'),
        'date': fields.date('Date', select=1),
        'title': fields.many2one('res.partner.title','Partner Form'),
        'parent_id': fields.many2one('res.partner','Parent Partner', select=2),
        'child_ids': fields.one2many('res.partner', 'parent_id', 'Partner Ref.'),
        'ref': fields.char('Reference', size=64, select=True),
        'lang': fields.selection(_lang_get, 'Language', size=32, help="If the selected language is loaded in the system, all documents related to this partner will be printed in this language. If not, it will be english."),
        'user_id': fields.many2one('res.users', 'Salesman', help='The internal user that is in charge of communicating with this partner if any.'),
        'vat': fields.char('VAT', size=32, help="Value Added Tax number. Check the box if the partner is subjected to the VAT. Used by the VAT legal statement."),
        'bank_ids': fields.one2many('res.partner.bank', 'partner_id', 'Banks'),
        'website': fields.char('Website',size=64, help="Website of Partner"),
        'comment': fields.text('Notes'),
        'address': fields.one2many('res.partner.address', 'partner_id', 'Contacts'),
        'category_id': fields.many2many('res.partner.category', 'res_partner_category_rel', 'partner_id', 'category_id', 'Categories'),
        'events': fields.one2many('res.partner.event', 'partner_id', 'Events', copy_data=copy_empty),
        'credit_limit': fields.float(string='Credit Limit'),
        'ean13': fields.char('EAN13', size=13),
        'active': fields.boolean('Active', select=True, required=True),
        'customer': fields.boolean('Customer', help="Check this box if the partner is a customer."),
        'supplier': fields.boolean('Supplier', help="Check this box if the partner is a supplier. If it's not checked, purchase people will not see it when encoding a purchase order."),
        'city': fields.related('address', 'city', type='char', string='City'),
        'phone': fields.related('address', 'phone', type='char', string='Phone'),
        'mobile': fields.related('address', 'mobile', type='char', string='Mobile'),
        'country': fields.related('address', 'country_id', type='many2one', relation='res.country', string='Country'),
        'employee': fields.boolean('Employee', help="Check this box if the partner is an Employee."),
        'email': fields.related('address', 'email', type='char', size=240, string='E-mail'),
        'company_id': fields.many2one('res.company', 'Company', select=1),
    }

    def _default_category(self, cr, uid, context=None):
        if context is None:
            context = {}
        if 'category_id' in context and context['category_id']:
            return [context['category_id']]
        return []

    _defaults = {
        'active': True,
        'customer': True,
        'category_id': _default_category,
        'company_id': lambda s,cr,uid,c: s.pool.get('res.company')._company_default_get(cr, uid, 'res.partner', context=c),
    }

    def do_share(self, cr, uid, ids, *args):
        return True

    def _check_ean_key(self, cr, uid, ids, context=None):
        for partner_o in pooler.get_pool(cr.dbname).get('res.partner').read(cr, uid, ids, ['ean13',]):
            thisean=partner_o['ean13']
            if thisean and thisean!='':
                if len(thisean)!=13:
                    return False
                sum=0
                for i in range(12):
                    if not (i % 2):
                        sum+=int(thisean[i])
                    else:
                        sum+=3*int(thisean[i])
                if math.ceil(sum/10.0)*10-sum!=int(thisean[12]):
                    return False
        return True

#   _constraints = [(_check_ean_key, 'Error: Invalid ean code', ['ean13'])]

    def name_get(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        if not len(ids):
            return []
        if context and context.get('show_ref', False):
            rec_name = 'ref'
        else:
            rec_name = 'name'

        res = [(r['id'], r[rec_name]) for r in self.read(cr, uid, ids, [rec_name], context)]
        return res

    def name_search(self, cr, uid, name, args=None, operator='ilike', context=None, limit=100):
        if not args:
            args=[]
        if not context:
            context={}
        if name:
            ids = self.search(cr, uid, [('ref', '=', name)] + args, limit=limit, context=context)
            if not ids:
                ids = self.search(cr, uid, [('name', operator, name)] + args, limit=limit, context=context)
        else:
            ids = self.search(cr, uid, args, limit=limit, context=context)
        return self.name_get(cr, uid, ids, context)

    def _email_send(self, cr, uid, ids, email_from, subject, body, on_error=None):
        partners = self.browse(cr, uid, ids)
        for partner in partners:
            if len(partner.address):
                if partner.address[0].email:
                    tools.email_send(email_from, [partner.address[0].email], subject, body, on_error)
        return True

    def email_send(self, cr, uid, ids, email_from, subject, body, on_error=''):
        while len(ids):
            self.pool.get('ir.cron').create(cr, uid, {
                'name': 'Send Partner Emails',
                'user_id': uid,
#               'nextcall': False,
                'model': 'res.partner',
                'function': '_email_send',
                'args': repr([ids[:16], email_from, subject, body, on_error])
            })
            ids = ids[16:]
        return True

    def address_get(self, cr, uid, ids, adr_pref=None):
        if adr_pref is None:
            adr_pref = ['default']
        address_obj = self.pool.get('res.partner.address')
        address_rec = address_obj.search_read(cr, uid, [('partner_id', '=', ids)], fields=['type'])
        res = list(tuple(addr.values()) for addr in address_rec)
        adr = dict(res)
        # get the id of the (first) default address if there is one,
        # otherwise get the id of the first address in the list
        if res:
            default_address = adr.get('default', res[0][1])
        else:
            default_address = False
        result = {}
        for a in adr_pref:
            result[a] = adr.get(a, default_address)
        return result

    def _address_browse(self, cr, uid, ids, atype='default', afield=None, context=None):
        """Retrieve the best matching address for this partner, return browse rec.

            A partner may have multiple addresses. It's often the case that we
            want to get the best candidate for some use (eg. "email to the company"
            or "ship to the delivery site").

            It returns a browse object, so that it can be used in reporting expressions,
            like::

                Email: {{ order.partner_id._address_browse('contact', 'email') }}

            @param atype Type of address being requested. May return another type,
                if this one doesn't exist
            @param afield if specified, the field we want to use, eg. "email". Will
                skip addresses that have this field empty
        """

        res = {}

        for partner in self.browse(cr, uid, ids, context=context):
            cur_addr = None
            cur_valid = 0
            for addr in partner.address:
                valid = addr._table._usable_validities.get(addr.validity, False)
                if not valid:
                    continue
                if afield is not None and not getattr(addr, afield, False):
                    # it doesn't have the info we need (is empty)
                    continue
                if addr.type == atype:
                    valid = 10 + valid
                if valid > cur_valid:
                    cur_addr = addr
            if cur_addr is None:
                cur_addr = osv.orm.browse_null()
            res[partner.id] = cur_addr

        if len(ids) > 1:
            return res
        else:
            return res.values()[0]

    def gen_next_ref(self, cr, uid, ids):
        if len(ids) != 1:
            return True

        # compute the next number ref
        cr.execute("SELECT ref FROM res_partner WHERE ref IS NOT NULL ORDER BY char_length(ref) DESC, ref DESC LIMIT 1")
        res = cr.dictfetchall()
        ref = res and res[0]['ref'] or '0'
        try:
            nextref = int(ref)+1
        except ValueError:
            raise osv.except_osv(_('Warning'), _("Couldn't generate the next id because some partners have an alphabetic id !"))

        # update the current partner
        cr.execute("update res_partner set ref=%s where id=%s", (nextref, ids[0]))
        return True

    def view_header_get(self, cr, uid, view_id, view_type, context):
        res = super(res_partner, self).view_header_get(cr, uid, view_id, view_type, context)
        if res: return res
        if (not context.get('category_id', False)):
            return False
        return _('Partners: ')+self.pool.get('res.partner.category').browse(cr, uid, context['category_id'], context).name

    def main_partner(self, cr, uid):
        ''' Return the id of the main partner
        '''
        model_data = self.pool.get('ir.model.data')
        return model_data.get_object_reference(cr, uid, 'base','main_partner')[1]
res_partner()

class res_partner_address(osv.osv):
    _description ='Partner Addresses'
    _name = 'res.partner.address'
    _order = 'type, name'
    _usable_validities = {'unknown': 1, 'verified': 2, 'preferred' : 3 }
    _columns = {
        'partner_id': fields.many2one('res.partner', 'Partner Name', ondelete='set null', select=True, help="Keep empty for a private address, not related to partner."),
        'type': fields.selection( [ ('default','Default'),('invoice','Invoice'), ('delivery','Delivery'), ('contact','Contact'), ('other','Other') ],'Address Type', help="Used to select automatically the right address according to the context in sales and purchases documents."),
        'function': fields.char('Function', size=64),
        'title': fields.many2one('res.partner.title','Title'),
        'name': fields.char('Contact Name', size=64, select=1),
        'street': fields.char('Street', size=128),
        'street2': fields.char('Street2', size=128),
        'zip': fields.char('Zip', change_default=True, size=24),
        'city': fields.char('City', size=128),
        'state_id': fields.many2one("res.country.state", 'Fed. State', domain="[('country_id','=',country_id)]"),
        'country_id': fields.many2one('res.country', 'Country'),
        'email': fields.char('E-Mail', size=240),
        'phone': fields.char('Phone', size=64),
        'fax': fields.char('Fax', size=64),
        'mobile': fields.char('Mobile', size=64),
        'birthdate': fields.char('Birthdate', size=64),
        'is_customer_add': fields.related('partner_id', 'customer', type='boolean', string='Customer'),
        'is_supplier_add': fields.related('partner_id', 'supplier', type='boolean', string='Supplier'),
        'active': fields.boolean('Active', required=True, select=True, 
                    help="Uncheck the active field to hide the contact."),
#        'company_id': fields.related('partner_id','company_id',type='many2one',relation='res.company',string='Company', store=True),
        'company_id': fields.many2one('res.company', 'Company',select=1),
        'validity': fields.selection([( 'unknown', 'Unknown'),
                ('verified','Verified'), ('preferred','Preferred'),
                ('invalid', 'Invalid'), ('deprecated', 'Deprecated')],
                'Validity', required=True,
                help="Has this address been verified?"),

    }
    _defaults = {
        'active': True,
        'company_id': lambda s,cr,uid,c: s.pool.get('res.company')._company_default_get(cr, uid, 'res.partner.address', context=c),
        'validity': 'unknown',
    }

    def name_get(self, cr, user, ids, context=None):
        if not len(ids):
            return []
        if context is None:
            context = {}
        res = []
        for r in self.read(cr, user, ids, ['name','zip','country_id', 'city','partner_id', 'street']):
            if context.get('contact_display', 'contact')=='partner' and r['partner_id']:
                res.append((r['id'], r['partner_id'][1]))
            else:
                addr = r['name'] or ''
                if r['name'] and (r['city'] or r['country_id']):
                    addr += ', '
                addr += (r['country_id'] and r['country_id'][1] or '') + ' ' + (r['city'] or '') + ' '  + (r['street'] or '')
                if (context.get('contact_display', 'contact')=='partner_address') and r['partner_id']:
                    res.append((r['id'], "%s: %s" % (r['partner_id'][1], addr.strip() or '/')))
                else:
                    res.append((r['id'], addr.strip() or '/'))
        return res

    def name_search(self, cr, user, name, args=None, operator='ilike', context=None, limit=100):
        if not args:
            args=[]
        if not context:
            context={}
        if context.get('contact_display', 'contact')=='partner ' or context.get('contact_display', 'contact')=='partner_address '  :
            ids = self.search(cr, user, [('partner_id',operator,name)], limit=limit, context=context)
        else:
            if not name:
                ids = self.search(cr, user, args, limit=limit, context=context)
            else:
                ids = self.search(cr, user, [('zip','=',name)] + args, limit=limit, context=context)
            if not ids:
                ids = self.search(cr, user, [('city',operator,name)] + args, limit=limit, context=context)
            if name:
                ids += self.search(cr, user, [('name',operator,name)] + args, limit=limit, context=context)
                ids += self.search(cr, user, [('street',operator,name)] + args, limit=limit, context=context)
                ids += self.search(cr, user, [('country_id',operator,name)] + args, limit=limit, context=context)
                ids += self.search(cr, user, [('partner_id',operator,name)] + args, limit=limit, context=context)
        return self.name_get(cr, user, ids, context=context)

    def get_city(self, cr, uid, id):
        return self.browse(cr, uid, id).city

res_partner_address()

class res_partner_bank_type(osv.osv):
    _description='Bank Account Type'
    _name = 'res.partner.bank.type'
    _order = 'name'
    _columns = {
        'name': fields.char('Name', size=64, required=True, translate=True),
        'code': fields.char('Code', size=64, required=True),
        'field_ids': fields.one2many('res.partner.bank.type.field', 'bank_type_id', 'Type fields'),
    }
res_partner_bank_type()

class res_partner_bank_type_fields(osv.osv):
    _description='Bank type fields'
    _name = 'res.partner.bank.type.field'
    _order = 'name'
    _columns = {
        'name': fields.char('Field Name', size=64, required=True, translate=True),
        'bank_type_id': fields.many2one('res.partner.bank.type', 'Bank Type', required=True, ondelete='cascade'),
        'required': fields.boolean('Required'),
        'readonly': fields.boolean('Readonly'),
        'size': fields.integer('Max. Size'),
    }
res_partner_bank_type_fields()


class res_partner_bank(osv.osv):
    '''Bank Accounts'''
    _name = "res.partner.bank"
    _rec_name = "acc_number"
    _description = __doc__
    _order = 'sequence,name'

    def _bank_type_get(self, cr, uid, context=None):
        bank_type_obj = self.pool.get('res.partner.bank.type')

        result = []
        bank_types = bank_type_obj.browse(cr, uid, [True,], context=context)
        for bank_type in bank_types:
            result.append((bank_type.code, bank_type.name))
        return result

    def _default_value(self, cursor, user, field, context=None):
        if field in ('country_id', 'state_id'):
            value = False
        else:
            value = ''
        if not context.get('address', False):
            return value
        for ham, spam, address in context['address']:
            if address.get('type', False) == 'default':
                return address.get(field, value)
            elif not address.get('type', False):
                value = address.get(field, value)
        return value

    _columns = {
        'name': fields.char('Description', size=128),
        'acc_number': fields.char('Account Number', size=64, required=False),
        'bank': fields.many2one('res.bank', 'Bank', required=True),
        'owner_name': fields.char('Account Owner', size=64),
        'street': fields.char('Street', size=128),
        'zip': fields.char('Zip', change_default=True, size=24),
        'city': fields.char('City', size=128),
        'country_id': fields.many2one('res.country', 'Country',
            change_default=True),
        'state_id': fields.many2one("res.country.state", 'State',
            change_default=True, domain="[('country_id','=',country_id)]"),
        'partner_id': fields.many2one('res.partner', 'Partner', required=True,
            ondelete='cascade', select=True),
        'state': fields.selection(_bank_type_get, 'Bank Type', required=True,
            change_default=True),
        'sequence': fields.integer('Sequence'),
    }
    _defaults = {
        'owner_name': lambda obj, cursor, user, context: obj._default_value(
            cursor, user, 'name', context=context),
        'street': lambda obj, cursor, user, context: obj._default_value(
            cursor, user, 'street', context=context),
        'city': lambda obj, cursor, user, context: obj._default_value(
            cursor, user, 'city', context=context),
        'zip': lambda obj, cursor, user, context: obj._default_value(
            cursor, user, 'zip', context=context),
        'country_id': lambda obj, cursor, user, context: obj._default_value(
            cursor, user, 'country_id', context=context),
        'state_id': lambda obj, cursor, user, context: obj._default_value(
            cursor, user, 'state_id', context=context),
    }
    
    def fields_get(self, cr, uid, fields=None, context=None):
        res = super(res_partner_bank, self).fields_get(cr, uid, fields, context)
        bank_type_obj = self.pool.get('res.partner.bank.type')
        type_ids = bank_type_obj.search(cr, uid, [])
        types = bank_type_obj.browse(cr, uid, type_ids)
        for type in types:
            for field in type.field_ids:
                if field.name in res:
                    res[field.name].setdefault('states', {})
                    res[field.name]['states'][type.code] = [
                            ('readonly', field.readonly),
                            ('required', field.required)]
        return res

    def name_get(self, cr, uid, ids, context=None):
        if not len(ids):
            return []
        res = []
        for id in self.browse(cr, uid, ids):
            res.append((id.id,id.acc_number))
        return res

res_partner_bank()

class res_partner_category(osv.osv):
    _inherit = 'res.partner.category'
    _columns = {
        'partner_ids': fields.many2many('res.partner', 'res_partner_category_rel', 'category_id', 'partner_id', 'Partners'),
    }

res_partner_category()

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

