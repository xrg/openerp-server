# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2010-2011 OpenERP s.a. (<http://openerp.com>).
#    Copyright (C) 2012-2013 P. Christeas <xrg@hellug.gr>
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
from osv.orm import browse_record, orm_deprecated
import tools
from functools import partial
import pytz
import pooler
from tools.translate import _
from service import security
import logging

class groups(osv.osv):
    _name = "res.groups"
    _order = 'name'
    _description = "Access Groups"
    _columns = {
        'name': fields.char('Group Name', size=64, required=True),
        'model_access': fields.one2many('ir.model.access', 'group_id', 'Access Controls'),
        'rule_groups': fields.many2many('ir.rule', 'rule_group_rel',
            'group_id', 'rule_group_id', 'Rules', domain=[('global', '=', False)]),
        'menu_access': fields.many2many('ir.ui.menu', 'ir_ui_menu_group_rel', 'gid', 'menu_id', 'Access Menu'),
        'comment' : fields.text('Comment',size=250),
    }
    _sql_constraints = [
        ('name_uniq', 'unique (name)', 'The name of the group must be unique !')
    ]

    _groups_cache = {} #: db/uid cache of groups

    def copy(self, cr, uid, id, default=None, context=None):
        group_name = self.read(cr, uid, [id], ['name'])[0]['name']
        default.update({'name': _('%s (copy)')%group_name})
        return super(groups, self).copy(cr, uid, id, default, context)

    def write(self, cr, uid, ids, vals, context=None):
        if 'name' in vals:
            if vals['name'].startswith('-'):
                raise osv.except_osv(_('Error'),
                        _('The name of the group can not start with "-"'))
        self._groups_cache[cr.dbname] = {}
        res = super(groups, self).write(cr, uid, ids, vals, context=context)
        self.pool.get('ir.model.access').call_cache_clearing_methods(cr)
        return res

    def create(self, cr, uid, vals, context=None):
        if 'name' in vals:
            if vals['name'].startswith('-'):
                raise osv.except_osv(_('Error'),
                        _('The name of the group can not start with "-"'))
        gid = super(groups, self).create(cr, uid, vals, context=context)
        self._groups_cache[cr.dbname] = {}
        if context and context.get('noadmin', False):
            pass
        else:
            # assign this new group to user_root
            user_obj = self.pool.get('res.users')
            aid = user_obj.browse(cr, 1, user_obj._get_admin_id(cr))
            if aid:
                aid.write({'groups_id': [(4, gid)]})
        return gid

    def unlink(self, cr, uid, ids, context=None):
        self._groups_cache[cr.dbname] = {}
        return super(groups, self).unlink(cr, uid, ids, context=context)

    def get_extended_interface_group(self, cr, uid, context=None):
        data_obj = self.pool.get('ir.model.data')
        return data_obj.get_object_reference(cr, uid, 'base', 'group_extended')[1]

    def check_user_groups(self, cr, user_id, group_ids, context=None):
        """ Checks if `user_id` belongs to *any* of `group_ids`.

            This method is cached
        """

        if not group_ids:
            return False

        if cr.dbname not in self._groups_cache:
            self._groups_cache[cr.dbname] = {}

        if user_id not in self._groups_cache[cr.dbname]:
            cr.execute("SELECT gid FROM res_groups_users_rel WHERE uid = %s", (user_id,), debug=self._debug)
            self._groups_cache[cr.dbname].setdefault(user_id, [r[0] for r in cr.fetchall()])

        for g in self._groups_cache[cr.dbname][user_id]:
            if g in group_ids:
                return True
        return False

groups()

class roles(orm_deprecated, osv.osv):
    """ DEPRECATED: user roles.

        Kept here just for API compatibility with older installations. Please update
        your ORM objects!
    """
    _name = "res.roles"
    _columns = {
        'name': fields.char('Role Name', size=64, required=True),
        'parent_id': fields.many2one('res.roles', 'Parent', select=True,
            help="The parent role can be used to construct a hierarchy of roles. Parent roles inherit from the roles of their descendants."),
        'child_id': fields.one2many('res.roles', 'parent_id', 'Children'),
        'users': fields.many2many('res.users', 'res_roles_users_rel', 'rid', 'uid', 'Users'),
        'description': fields.text('Description', help="Description of this role and where it is relevant in workflows and processes"),
    }
    def check(self, cr, uid, ids, role_id):
        """ Check must never work again, because it would allow wrong access.
        """
        raise osv.except_osv("Model error", "You are accessing a deprecated model!")
roles()

def _lang_get(self, cr, uid, context=None):
    obj = self.pool.get('res.lang')
    res = obj.search_read(cr, uid, [('translatable','=',True)],
                fields=['code', 'name'], context=context)
    res = [(r['code'], r['name']) for r in res]
    return res

def _tz_get(self,cr,uid, context=None):
    return [(x, x) for x in pytz.all_timezones]

class users(osv.osv):
    __admin_ids = {}
    _uid_cache = {}
    _name = "res.users"
    _order = 'name'

    def get_current_company(self, cr, uid):
        cr.execute('SELECT company_id, res_company.name FROM res_users '
                    'LEFT JOIN res_company ON res_company.id = company_id '
                    'WHERE res_users.id=%s',(uid,))
        return cr.fetchall()

    def _set_interface_type(self, cr, uid, ids, name, value, arg, context=None):
        """Implementation of 'view' function field setter, sets the type of interface of the users.
        @param name: Name of the field
        @param arg: User defined argument
        @param value: new value returned
        @return:  True/False
        """
        if not value or value not in ['simple','extended']:
            return False
        group_obj = self.pool.get('res.groups')
        extended_group_id = group_obj.get_extended_interface_group(cr, uid, context=context)
        # First always remove the users from the group (avoids duplication if called twice)
        self.write(cr, uid, ids, {'groups_id': [(3, extended_group_id)]}, context=context)
        # Then add them back if requested
        if value == 'extended':
            self.write(cr, uid, ids, {'groups_id': [(4, extended_group_id)]}, context=context)
        return True


    def _get_interface_type(self, cr, uid, ids, name, args, context=None):
        """Implementation of 'view' function field getter, returns the type of interface of the users.
        @param field_name: Name of the field
        @param arg: User defined argument
        @return:  Dictionary of values
        """
        group_obj = self.pool.get('res.groups')
        extended_gid = group_obj.get_extended_interface_group(cr, uid, context=context)

        res = {}
        for ubr in self.browse(cr, uid, ids, context=context):
            res[ubr.id] = 'simple'
        cr.execute("SELECT uid FROM res_groups_users_rel "
                    "WHERE uid = ANY(%s) AND gid = %s",
                    (res.keys(), extended_gid), debug=self._debug)
        for r in cr.fetchall():
            res[r[0]] = 'extended'
        return res

    def _email_get(self, cr, uid, ids, name, arg, context=None):
        # perform this as superuser because the current user is allowed to read users, and that includes
        # the email, even without any direct read access on the res_partner_address object.
        return dict([(user.id, user.address_id.email) for user in self.browse(cr, 1, ids)]) # no context to avoid potential security issues as superuser

    def _email_set(self, cr, uid, ids, name, value, arg, context=None):
        if not isinstance(ids,list):
            ids = [ids]
        address_obj = self.pool.get('res.partner.address')
        for user in self.browse(cr, uid, ids, context=context):
            # perform this as superuser because the current user is allowed to write to the user, and that includes
            # the email even without any direct write access on the res_partner_address object.
            if user.address_id:
                address_obj.write(cr, 1, user.address_id.id, {'email': value or None}) # no context to avoid potential security issues as superuser
            else:
                address_id = address_obj.create(cr, 1, {'name': user.name, 'email': value or None}) # no context to avoid potential security issues as superuser
                self.write(cr, uid, ids, {'address_id': address_id}, context)
        return True

    def _set_new_password(self, cr, uid, id, name, value, args, context=None):
        if value is False:
            # Do not update the password if no value is provided, ignore silently.
            # For example web client submits False values for all empty fields.
            return
        if uid == id:
            # To change their own password users must use the client-specific change password wizard,
            # so that the new password is immediately used for further RPC requests, otherwise the user
            # will face unexpected 'Access Denied' exceptions.
            raise osv.except_osv(_('Operation Canceled'), _('Please use the change password wizard (in User Preferences or User menu) to change your own password.'))
        ctx = (context or {}).copy()
        ctx['set_password'] = tools.server_bool(True)
        self.write(cr, uid, id, {'password': value}, ctx)

    def _get_password(self, cr, uid, ids, arg, karg, context=None):
        return dict.fromkeys(ids, '')

    _columns = {
        'name': fields.char('User Name', size=64, required=True, select=True,
                            help="The new user's real name, used for searching"
                                 " and most listings"),
        'login': fields.char('Login', size=64, required=True,
                             help="Used to log into the system"),
        'password': fields.char('Password', size=64, invisible=True, help="Keep empty if you don't want the user to be able to connect on the system."),
        'new_password': fields.function(_get_password, method=True, type='char', size=64,
                                fnct_inv=_set_new_password,
                                string='Change password', help="Only specify a value if you want to change the user password. "
                                "This user will have to logout and login again!"),
        'email': fields.char('E-mail', size=64,
            help='If an email is provided, the user will be sent a message '
                 'welcoming him.\n\nWarning: if "email_from" and "smtp_server"'
                 " aren't configured, it won't be possible to email new "
                 "users."),
        'signature': fields.text('Signature', size=64),
        'address_id': fields.many2one('res.partner.address', 'Address'),
        'active': fields.boolean('Active'),
        'action_id': fields.many2one('ir.actions.actions', 'Home Action', help="If specified, this action will be opened at logon for this user, in addition to the standard menu."),
        'menu_id': fields.many2one('ir.actions.actions', 'Menu Action', help="If specified, the action will replace the standard menu for this user."),
        'groups_id': fields.many2many('res.groups', 'res_groups_users_rel', 'uid', 'gid', 'Groups'),

        # Special behavior for this field: res.company.search() will only return the companies
        # available to the current user (should be the user's companies?), when the user_preference
        # context is set.
        'company_id': fields.many2one('res.company', 'Company', required=True,
            help="The company this user is currently working for.", context={'user_preference': True}),

        'company_ids':fields.many2many('res.company','res_company_users_rel','user_id','cid','Companies'),
        'context_lang': fields.selection(_lang_get, 'Language', required=True,
            help="Sets the language for the user's user interface, when UI "
                 "translations are available"),
        'context_tz': fields.selection(_tz_get,  'Timezone', size=64,
            help="The user's timezone, used to perform timezone conversions "
                 "between the server and the client."),
        'view': fields.function(_get_interface_type, method=True, type='selection', fnct_inv=_set_interface_type,
                                selection=[('simple','Simplified'),('extended','Extended')],
                                string='Interface', help="Choose between the simplified interface and the extended one"),
        'user_email': fields.function(_email_get, method=True, fnct_inv=_email_set, string='Email', type="char", size=240),
        'menu_tips': fields.boolean('Menu Tips', help="Check out this box if you want to always display tips on each menu action"),
        'date': fields.datetime('Last Connection', readonly=True),
    }

    _read_filters_mask = { 'password': '********' }

    def on_change_company_id(self, cr, uid, ids, company_id):
        return {
                'warning' : {
                    'title': _("Company Switch Warning"),
                    'message': _("Please keep in mind that documents currently displayed may not be relevant after switching to another company. If you have unsaved changes, please make sure to save and close all forms before switching to a different company. (You can click on Cancel in the User Preferences now)"),
                }
        }

    def _get_read_filters(self, fields=None):
        """ Return a function that masks read() result with secure values

            For example, it will set outgoing 'password' to a dummy value.
        """
        rdic = self._read_filters_mask
        if fields is not None:
            rdic = {}
            for f in fields:
                if f in self._read_filters_mask:
                    rdic[f] = self._read_filters_mask[f]
        def fn(res):
            res.update(rdic)
            return res
        return fn

    def read(self,cr, uid, ids, fields=None, context=None, load='_classic_read'):
        result = super(users, self).read(cr, uid, ids, fields, context, load)
        _read_filter = self._get_read_filters(fields)
        if isinstance(ids, (int, long)):
            result = _read_filter(result)
        else:
            result = map(_read_filter, result)
        return result

    def search_read(self, cr, uid, domain, offset=0, limit=None, order=None,
                    fields=None, context=None, load='_classic_read'):
        result = super(users, self).search_read(cr, uid, domain, offset=offset, limit=limit, order=order,
                    fields=fields, context=context, load=load)
        _read_filter = self._get_read_filters(fields)
        result = map(_read_filter, result)
        return result

    def _check_company(self, cr, uid, ids, context=None):
        return all(((this.company_id in this.company_ids) or not this.company_ids) for this in self.browse(cr, uid, ids, context))

    _constraints = [
        (_check_company, 'The chosen company is not in the allowed companies for this user', ['company_id', 'company_ids']),
    ]

    _sql_constraints = [
        ('login_key', 'UNIQUE (login)',  'You can not have two users with the same login !')
    ]

    def _get_email_from(self, cr, uid, ids, context=None):
        if not isinstance(ids, list):
            ids = [ids]
        res = dict.fromkeys(ids, False)
        for user in self.browse(cr, uid, ids, context=context):
            if user.user_email:
                res[user.id] = "%s <%s>" % (user.name, user.user_email)
        return res

    def _get_admin_id(self, cr):
        if self.__admin_ids.get(cr.dbname) is None:
            mdid = self.pool.get('ir.model.data').get_object_reference(cr, 1, 'base', 'user_root')[1]
            self.__admin_ids[cr.dbname] = mdid
        return self.__admin_ids[cr.dbname]

    def _get_company(self,cr, uid, context=None, uid2=False):
        if not uid2:
            uid2 = uid
        user = self.pool.get('res.users').read(cr, uid, uid2, ['company_id'], context)
        company_id = user.get('company_id', False)
        return company_id and company_id[0] or False

    def _get_companies(self, cr, uid, context=None):
        c = self._get_company(cr, uid, context)
        if c:
            return [c]
        return False

    def _get_menu(self,cr, uid, context=None):
        dataobj = self.pool.get('ir.model.data')
        try:
            model, res_id = dataobj.get_object_reference(cr, uid, 'base', 'action_menu_admin')
            if model != 'ir.actions.act_window':
                return False
            return res_id
        except ValueError:
            return False

    def _get_group(self,cr, uid, context=None):
        dataobj = self.pool.get('ir.model.data')
        result = []
        try:
            dummy,group_id = dataobj.get_object_reference(cr, 1, 'base', 'group_user')
            result.append(group_id)
            dummy,group_id = dataobj.get_object_reference(cr, 1, 'base', 'group_partner_manager')
            result.append(group_id)
        except ValueError:
            # If these groups do not exist anymore
            pass
        return result

    _defaults = {
        'password' : '',
        'context_lang': 'en_US',
        'active' : True,
        'menu_id': _get_menu,
        'company_id': _get_company,
        'company_ids': _get_companies,
        'groups_id': _get_group,
        'address_id': False,
        'menu_tips':True
    }

    @tools.cache()
    def company_get(self, cr, uid, uid2, context=None):
        return self._get_company(cr, uid, context=context, uid2=uid2)

    # User can write to a few of her own fields (but not her groups for example)
    SELF_WRITEABLE_FIELDS = ['menu_tips','view', 'password', 'signature', 'action_id', 'company_id', 'user_email']

    def write(self, cr, uid, ids, values, context=None):
        if not hasattr(ids, '__iter__'):
            ids = [ids]
        if ids == [uid]:
            for key in values.keys():
                if not (key in self.SELF_WRITEABLE_FIELDS or key.startswith('context_')):
                    break
            else:
                if 'company_id' in values:
                    if not (values['company_id'] in self.read(cr, 1, uid, ['company_ids'], context=context)['company_ids']):
                        del values['company_id']
                uid = 1 # safe fields only, so we write as super-user to bypass access rights

        if ('password' in values) and not tools.server_bool.in_context(context, 'set_password', True):
            self.log(cr, 1, uid, _("Attempt to reset password, through prohibited API!"), context=context)
            raise security.ExceptionNoTb("Access Denied")

        res = super(users, self).write(cr, uid, ids, values, context=context)

        # clear caches linked to the users
        self.company_get.clear_cache(cr.dbname)
        self.pool.get('ir.model.access').call_cache_clearing_methods(cr)
        clear = partial(self.pool.get('ir.rule').clear_cache, cr)
        map(clear, ids)
        db = cr.dbname
        uic_db = self._uid_cache.get(db, False)
        if uic_db:
            for id in ids:
                uic_db.pop(id, None)

        return res

    def unlink(self, cr, uid, ids, context=None):
        if 1 in ids:
            raise osv.except_osv(_('Can not remove root user!'), _('You can not remove the admin user as it is used internally for resources created by OpenERP (updates, module installation, ...)'))
        db = cr.dbname
        uic_db = self._uid_cache.get(db, False)
        if uic_db:
            for id in ids:
                uic_db.pop(id, None)

        return super(users, self).unlink(cr, uid, ids, context=context)

    def name_search(self, cr, user, name='', args=None, operator='ilike', context=None, limit=100):
        if not args:
            args=[]
        if not context:
            context={}
        ids = []
        if name:
            ids = self.search(cr, user, [('login','=',name)]+ args, limit=limit)
        if not ids:
            ids = self.search(cr, user, [('name',operator,name)]+ args, limit=limit)
        return self.name_get(cr, user, ids)

    def copy(self, cr, uid, id, default=None, context=None):
        """ Most of res.users fields are sensitive; only copy from whitelist
        """
        user2copy = self.read(cr, uid, [id], ['login','name'])[0]
        if default is None:
            default = {}
        copy_pattern = _("%s (copy)")
        copydef = dict(login=(copy_pattern % user2copy['login']),
                       name=(copy_pattern % user2copy['name']),
                       address_id=False, # avoid sharing the address of the copied user!
                       )
        copydef.update(default)
        return super(users, self).copy(cr, uid, id, copydef, context)

    def context_get(self, cr, uid, context=None):
        user = self.browse(cr, uid, uid, context)
        result = {}
        for k in self._columns.keys():
            if k.startswith('context_'):
                res = getattr(user,k) or False
                if isinstance(res, browse_record):
                    res = res.id
                result[k[8:]] = res or False
        return result

    def action_get(self, cr, uid, context=None):
        """Return the action-id for setting user's preferences
        """
        dataobj = self.pool.get('ir.model.data')
        return dataobj.get_object_reference(cr, 1, 'base', 'action_res_users_my')[1]

    def login(self, db, login, password):
        """Check password and mark the user as logged-in
        """
        if not password:
            return False
        cr = pooler.get_db(db).cursor()
        try:
            cr.execute_safe('UPDATE res_users SET date=now() WHERE login=%s AND password=%s AND active RETURNING id',
                    (tools.ustr(login), tools.ustr(password)))
            res = cr.fetchone()
            cr.commit()
            if res:
                return res[0]
            else:
                return False
        finally:
            cr.close()

    login.is_plain = True # mark the plaintext version

    def check_super(self, passwd):
        """Verify the super-user password
        """
        if passwd == tools.config['admin_passwd']:
            return True
        else:
            raise security.ExceptionNoTb('AccessDenied')

    def check(self, db, uid, passwd):
        """Verifies that the given (uid, password) pair is authorized for the database ``db`` and
           raise an exception if it is not."""
        if not passwd:
            # empty passwords disallowed for obvious security reasons
            raise security.ExceptionNoTb('AccessDenied')
        if self._uid_cache.get(db, {}).get(uid) == passwd:
            return True
        cr = pooler.get_db(db).cursor()
        try:
            cr.execute_safe('SELECT COUNT(1) FROM res_users WHERE id=%s AND password=%s AND active=%s',
                        (int(uid), passwd, True))
            res = cr.fetchone()
            if not (res and res[0]):
                raise security.ExceptionNoTb('AccessDenied')
            self._uid_cache.setdefault(db, {})[uid] = passwd
            return True
        finally:
            cr.close()

    check.is_plain = True

    def access(self, db, uid, passwd, sec_level, ids):
        if not passwd:
            return False
        cr = pooler.get_db(db).cursor()
        try:
            cr.execute_safe('SELECT id FROM res_users WHERE id=%s AND password=%s', (uid, passwd))
            res = cr.fetchone()
            if not res:
                raise security.ExceptionNoTb('Bad username or password')
            return res[0]
        finally:
            cr.close()

    def change_password(self, cr, uid, old_passwd, new_passwd, context=None):
        """Change current user password. Old password must be provided explicitly
        to prevent hijacking an existing user session, or for cases where the cleartext
        password is not used to authenticate requests.

        :return: True
        :raise: security.ExceptionNoTb when old password is wrong
        :raise: except_osv when new password is not set or empty
        """
        ctx = (context or {}).copy()
        ctx['set_password'] = tools.server_bool(True)
        self.check(cr.dbname, uid, old_passwd)
        if new_passwd:
            ret = self.write(cr, uid, uid, {'password': new_passwd}, ctx)
            # log as admin, the event is to be seen by him
            self.log(cr, 1, uid, _("Password changed"), context=context)
            logging.getLogger('orm').info("%s: password change for user #%d", self._name, uid)
            uic_db = self._uid_cache.get(cr.dbname, False)
            if uic_db:
                uic_db.pop(uid, None)
            return ret
        raise osv.except_osv(_('Warning!'), _("Setting empty passwords is not allowed for security reasons!"))

users()

class config_users(osv.osv_memory):
    _name = 'res.config.users'
    _inherit = ['res.users', 'res.config']

    _columns = {}

    def _generate_signature(self, cr, name, email, context=None):
        return _('--\n%(name)s %(email)s\n') % {
            'name': name or '',
            'email': email and ' <'+email+'>' or '',
            }

    def create_user(self, cr, uid, new_id, context=None):
        """ create a new res.user instance from the data stored
        in the current res.config.users.

        If an email address was filled in for the user, sends a mail
        composed of the return values of ``get_welcome_mail_subject``
        and ``get_welcome_mail_body`` (which should be unicode values),
        with the user's data %-formatted into the mail body
        """
        base_data = self.read(cr, uid, new_id, context=context)
        partner_id = self.pool.get('res.partner').main_partner(cr, uid)
        address = self.pool.get('res.partner.address').create(
            cr, uid, {'name': base_data['name'],
                      'email': base_data['email'],
                      'partner_id': partner_id,},
            context)
        user_data = dict(
            base_data,
            signature=self._generate_signature(
                cr, base_data['name'], base_data['email'], context=context),
            address_id=address,
            )
        self.pool.get('res.users').create(cr, uid, user_data, context)

    def execute(self, cr, uid, ids, context=None):
        'Do nothing on execution, just launch the next action/todo'
        pass

    def action_add(self, cr, uid, ids, context=None):
        'Create a user, and re-display the view'
        self.create_user(cr, uid, ids[0], context=context)
        return {
            'view_type': 'form',
            "view_mode": 'form',
            'res_model': 'res.config.users',
            'view_id':self.pool.get('ir.ui.view')\
                .search(cr,uid,[('name','=','res.config.users.confirm.form')]),
            'type': 'ir.actions.act_window',
            'target':'new',
            }
config_users()

class groups2(osv.osv):
    # Class appended here, to workaround order of instantiation.
    _inherit = 'res.groups'
    _columns = {
        'users': fields.many2many('res.users', 'res_groups_users_rel', 'gid', 'uid', 'Users'),
    }

    def unlink(self, cr, uid, ids, context=None):
        group_users = []
        group_names = []
        for record in self.read(cr, uid, ids, ['name', 'users'], context=context):
            if record['users']:
                group_names.append(record['name'])
                group_users.extend(record['users'])

        if group_users:
            user_names = [user.name for user in self.pool.get('res.users').browse(cr, uid, group_users, context=context)]
            if len(user_names) >= 5:
                user_names = user_names[:5]
                user_names += '...'
            raise osv.except_osv(_('Warning !'),
                        _('Group(s) %s cannot be deleted, because some user(s) still belong to them: %s !') % \
                            ( ', '.join(['"%s"' % g for g in group_names]),
                                ', '.join(user_names)))
        return super(groups2, self).unlink(cr, uid, ids, context=context)

groups2()

class res_config_view(osv.osv_memory):
    _name = 'res.config.view'
    _inherit = 'res.config'
    _columns = {
        'name':fields.char('Name', size=64),
        'view': fields.selection([('simple','Simplified'),
                                  ('extended','Extended')],
                                 'Interface', required=True ),
    }
    _defaults={
        'view':lambda self,cr,uid,*args: self.pool.get('res.users').browse(cr, uid, uid).view or 'simple',
    }

    def execute(self, cr, uid, ids, context=None):
        res = self.read(cr, uid, ids)[0]
        self.pool.get('res.users').write(cr, uid, [uid],
                                 {'view':res['view']}, context=context)

res_config_view()

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
