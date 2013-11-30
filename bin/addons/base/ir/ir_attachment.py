# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2008-2009, 2011-2013 P. Christeas <xrg@hellug.gr>
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

import itertools

from osv import fields, osv, index
from collections import defaultdict
from osv.orm import except_orm
from tools.translate import _

class ir_attachment(osv.osv):
    def check(self, cr, uid, ids, mode, context=None, values=None):
        """Restricts the access to an ir.attachment, according to referred model
        In the 'document' module, it is overriden to relax this hard rule, since
        more complex ones apply there.
        """
        if (not ids) or uid == 1:
            return
        ima = self.pool.get('ir.model.access')
        res_ids = defaultdict(set)
        if ids:
            if isinstance(ids, (int, long)):
                ids = [ids]
            cr.execute('SELECT DISTINCT res_model, res_id FROM ir_attachment WHERE id = ANY (%s) ' \
                    'AND res_model IS NOT NULL AND res_id IS NOT NULL', (ids,), self._debug)
            for rmod, rid in cr.fetchall():
                res_ids[rmod].add(rid)
        if values:
            if 'res_model' in values and 'res_id' in values:
                res_ids[values['res_model']].add(values['res_id'])

        for model, mids in res_ids.items():
            # ignore attachments that are not attached to a resource anymore when checking access rights
            # (resource was deleted but attachment was not)
            mod_obj = self.pool.get(model)
            if not mod_obj:
                raise except_orm('AccessError',
                                _('You are not allowed to access attachments of removed model %s. Please contact your administrator') \
                                % model)

            cr.execute('SELECT id FROM %s WHERE id = ANY(%%s)' % mod_obj._table, (list(mids),), debug=self._debug)
            mids = [x[0] for x in cr.fetchall()]
            ima.check(cr, uid, model, mode, context=context)
            self.pool.get(model).check_access_rule(cr, uid, mids, mode, context=context)

    def _search(self, cr, uid, args, offset=0, limit=None, order=None,
            context=None, count=False, access_rights_uid=None):
        ids = super(ir_attachment, self)._search(cr, uid, args, offset=offset,
                                                limit=limit, order=order,
                                                context=context, count=False,
                                                access_rights_uid=access_rights_uid)
        if not ids:
            if count:
                return 0
            return []

        # Work with a set, as list.remove() is prohibitive for large lists of documents
        # (takes 20+ seconds on a db with 100k docs during search_count()!)
        orig_ids = ids
        ids = set(ids)

        # For attachments, the permissions of the document they are attached to
        # apply, so we must remove attachments for which the user cannot access
        # the linked document.
        # Use pure SQL rather than read() as it is about 50% faster for large dbs (100k+ docs),
        # and the permissions are checked in super() and below anyway.
        cr.execute("""SELECT id, res_model, res_id FROM ir_attachment WHERE id = ANY(%s)""", (list(ids),))
        targets = cr.dictfetchall()
        model_attachments = {}
        for target_dict in targets:
            if not (target_dict['res_id'] and target_dict['res_model']):
                continue
            # model_attachments = { 'model': { 'res_id': [id1,id2] } }
            model_attachments.setdefault(target_dict['res_model'],{}).setdefault(target_dict['res_id'],set()).add(target_dict['id'])

        # To avoid multiple queries for each attachment found, checks are
        # performed in batch as much as possible.
        ima = self.pool.get('ir.model.access')
        for model, targets in model_attachments.iteritems():
            if not ima.check(cr, uid, model, 'read', raise_exception=False, context=context):
                # remove all corresponding attachment ids
                for attach_id in itertools.chain(*targets.values()):
                    ids.remove(attach_id)
                continue # skip ir.rule processing, these ones are out already

            # filter ids according to what access rules permit
            target_ids = targets.keys()
            allowed_ids = self.pool.get(model).search(cr, uid, [('id', 'in', target_ids)], context=context)
            disallowed_ids = set(target_ids).difference(allowed_ids)
            for res_id in disallowed_ids:
                for attach_id in targets[res_id]:
                    ids.remove(attach_id)

        # sort result according to the original sort ordering
        result = [id for id in orig_ids if id in ids]
        return len(result) if count else list(result)

    def read(self, cr, uid, ids, fields=None, context=None, load='_classic_read'):
        self.check(cr, uid, ids, 'read', context=context)
        return super(ir_attachment, self).read(cr, uid, ids, fields, context, load)

    def write(self, cr, uid, ids, vals, context=None):
        self.check(cr, uid, ids, 'write', context=context, values=vals)
        return super(ir_attachment, self).write(cr, uid, ids, vals, context)

    def copy(self, cr, uid, id, default=None, context=None):
        self.check(cr, uid, [id], 'write', context=context)
        return super(ir_attachment, self).copy(cr, uid, id, default, context)

    def unlink(self, cr, uid, ids, context=None):
        self.check(cr, uid, ids, 'unlink', context=context)
        return super(ir_attachment, self).unlink(cr, uid, ids, context)

    def create(self, cr, uid, values, context=None):
        self.check(cr, uid, [], mode='create', context=context, values=values)
        return super(ir_attachment, self).create(cr, uid, values, context)

    def action_get(self, cr, uid, context=None):
        return self.pool.get('ir.actions.act_window').for_xml_id(
            cr, uid, 'base', 'action_attachment', context=context)

    def _name_get_resname(self, cr, uid, ids, object, method, context):
        data = {}
        for attachment in self.browse(cr, uid, ids, context=context):
            model_object = attachment.res_model
            res_id = attachment.res_id
            if model_object and res_id:
                model_pool = self.pool.get(model_object)
                if not model_pool:
                    continue
                res = model_pool.name_get(cr,uid,[res_id],context)
                res_name = res and res[0][1] or False
                if res_name:
                    field = self._columns.get('res_name',False)
                    if field and len(res_name) > field.size:
                        res_name = res_name[:field.size-3] + '...' 
                data[attachment.id] = res_name
            else:
                data[attachment.id] = False
        return data

    _name = 'ir.attachment'
    _function_field_browse = True
    _columns = {
        'name': fields.char('Attachment Name',size=256, required=True),
        'datas': fields.binary('Data'),
        'datas_fname': fields.char('Filename',size=256),
        'description': fields.text('Description'),
        'res_name': fields.function(_name_get_resname, type='char', size=128,
                string='Resource Name', method=True, store=True),
        'res_model': fields.char('Resource Object',size=64, readonly=True,
                help="The database object this attachment will be attached to"),
        'res_id': fields.integer('Resource ID', readonly=True,
                help="The record id this is attached to"),
        'url': fields.char('Url', size=512, oldname="link"),
        'type': fields.selection(
                [ ('url','URL'), ('binary','Binary'), ],
                'Type', help="Binary File or external URL", required=True, change_default=True),

        'create_date': fields.datetime('Date Created', readonly=True),
        'create_uid':  fields.many2one('res.users', 'Owner', readonly=True),
        'company_id': fields.many2one('res.company', 'Company', change_default=True),
    }

    _defaults = {
        'type': 'binary',
        'company_id': lambda s,cr,uid,c: s.pool.get('res.company')._company_default_get(cr, uid, 'ir.attachment', context=c),
    }

    _indices = {
        'res_idx': index.plain('res_model', 'res_id'),
    }

ir_attachment()


# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

