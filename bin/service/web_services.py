# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2009-2010, 2011-2015 P. Christeas <xrg@hellug.gr>
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

import addons
import base64
import ir
import locale
import logging
import netsvc
import os
import platform
import pooler
import release
import security
import sql_db
import sys
import threading
import time
import tools
from operator import itemgetter
from tools.translate import _
from cStringIO import StringIO

#.apidoc title: Exported Service methods
#.apidoc module-mods: member-order: bysource

""" This python module defines the RPC methods available to remote clients.

    Each 'Export Service' is a group of 'methods', which in turn are RPC
    procedures to be called. Each method has its own arguments footprint.
"""

logging.basicConfig()

class baseExportService(netsvc.ExportService):
    """ base class for the objects that implement the standardized
        xmlrpc2 dispatch
    """
    _auth_commands = { 'pub': [] , 'root': [],  'db': [] }

    def new_dispatch(self, method, auth, params, auth_domain=None):
        # Double check, that we have the correct authentication:
        if not auth:
                domain='pub'
        else:
                domain=auth.provider.domain
        if method not in self._auth_commands[domain]:
            raise Exception("Method not found: %s" % method)

        fn = getattr(self, 'exp_'+method)
        if domain == 'db':
            db, uid = auth.auth_creds[auth.last_auth][2:4]
            cr = pooler.get_db(db).cursor()
            try:
                res = fn(cr, uid, *params)
                cr.commit()
                return res
            finally:
                cr.close()
        else:
            return fn(*params)


class db(baseExportService):
    """ Commands to manipulate OpenERP databases
    """
    _auth_commands = { 'root': [ 'create', 'get_progress', 'drop', 'dump',
                'restore', 'rename', 'db_copy',
                'change_admin_password', 'migrate_databases' ],
            'pub': [ 'db_exist', 'list', 'list_lang', 'server_version' ],
            }

    def __init__(self, name="db"):
        netsvc.ExportService.__init__(self, name)
        self.joinGroup("web-services")
        self.actions = {}
        self.id = 0
        self.id_protect = threading.Semaphore()

        self._pg_psw_env_var_is_set = False # on win32, pg_dump need the PGPASSWORD env var

    def dispatch(self, method, auth, params):
        if method in [ 'create', 'get_progress', 'drop', 'dump',
            'restore', 'rename',
            'change_admin_password', 'migrate_databases' ]:
            passwd = params[0]
            params = params[1:]
            security.check_super(passwd)
        elif method in [ 'db_exist', 'list', 'list_lang', 'server_version' ]:
            # params = params
            # No security check for these methods
            pass
        else:
            raise KeyError("Method not found: %s" % method)
        fn = getattr(self, 'exp_'+method)
        return fn(*params)

    def _check_db_allowed(self, dbname):
        """ Check if dbname is allowed to be used (for anything)
        """
        allowed_res = tools.config.get_misc('databases', 'allowed')
        if allowed_res:
            dbs_allowed = [ x.strip() for x in allowed_res.split(' ')]
            return bool(dbname in dbs_allowed)
        else:
            return True # all databases allowed

    def _create_empty_database(self, name):
        db = sql_db.db_connect('template1', temp=True)
        cr = db.cursor()
        ## We check if we can use geospatial enable database directly
        tmpls = tools.config.get_misc('databases', 'template','').split(' ')
        if 'template0' not in tmpls:
            # always add template0 as a failover
            # could have been template1, too
            tmpls.append('template0')
        cr.execute("SELECT datname FROM pg_database WHERE datistemplate = true " \
                "AND datname = ANY(%s);", (tmpls,))

        found_tmpls = map(itemgetter(0), cr.fetchall())
        # Filter the templates, but preserve their order!
        tmpls = filter(lambda x: x in found_tmpls, tmpls)
        assert ';' not in tmpls[0]

        try:
            cr.autocommit(True) # avoid transaction block
            cr.execute("""CREATE DATABASE "%s" ENCODING 'unicode' TEMPLATE %s """ % (name, tmpls[0]))

            logger = logging.getLogger('web-services')
            logger.info('CREATE DATABASE: %s template %s' % (name.lower(), tmpls[0]))

        finally:
            cr.close()

    def exp_create(self, db_name, demo, lang, user_password='admin'):
        """Create a new OpenERP database, with just the core tables

            @param db_name The name of the new database, must be valid Postgres "name"
            @param demo  If set, db will be populated with demo data, and all subsequent
                        module installations will use demo data, too.
            @param lang the language to initialize the database to. Can be empty.
            @param user_password Some string to set the "admin" user's password to.

            @return id  A "thread" number which can be polled by get_progress() call

            Note: this RPC call will return immediately, before the new database is ready.
            You have to poll the `id` to see when the operation is complete.
        """
        self.id_protect.acquire()
        self.id += 1
        id = self.id
        self.id_protect.release()

        self.actions[id] = {'clean': False}

        self._create_empty_database(db_name)

        class DBInitialize(object):
            def __call__(self, serv, id, db_name, demo, lang, user_password='admin'):
                cr = None
                try:
                    serv.actions[id]['progress'] = 0
                    cr = sql_db.db_connect(db_name).cursor()
                    tools.init_db(cr)
                    cr.commit()
                    cr.close()
                    cr = None
                    _langs = []
                    if lang:
                        _langs.append(lang)
                    pool = pooler.restart_pool(db_name, demo, serv.actions[id],
                            update_module=True, languages=_langs)[1]

                    cr = sql_db.db_connect(db_name).cursor()

                    if lang:
                        modobj = pool.get('ir.module.module')
                        mids = modobj.search(cr, 1, [('state', '=', 'installed')])
                        modobj.update_translations(cr, 1, mids, lang)

                    cr.execute('UPDATE res_users SET password=%s, context_lang=%s, active=True WHERE login=%s', (
                        user_password, lang, 'admin'))
                    cr.execute('SELECT login, password, name ' \
                               '  FROM res_users ' \
                               ' ORDER BY login')
                    serv.actions[id]['users'] = cr.dictfetchall()
                    serv.actions[id]['clean'] = True
                    cr.commit()
                    cr.close()
                except Exception, e:
                    serv.actions[id]['clean'] = False
                    serv.actions[id]['exception'] = e
                    import traceback
                    e_str = StringIO()
                    traceback.print_exc(file=e_str)
                    traceback_str = e_str.getvalue()
                    e_str.close()
                    logging.getLogger('web-services').error('CREATE DATABASE\n%s' % (traceback_str))
                    serv.actions[id]['traceback'] = traceback_str
                    if cr:
                        cr.close()

        dbi = DBInitialize()
        create_thread = threading.Thread(target=dbi,
                args=(self, id, db_name, demo, lang, user_password))
        create_thread.start()
        self.actions[id]['thread'] = create_thread
        return id

    def exp_db_copy(self, source_db_name, dest_db_name, user_password=False):
        """Copy an OpenERP database to a new name.

            Optionally, the "admin" user's password of the new database can be reset.

            Note: according to a Postgres limitation, the source database must be *locked*
            to enable this operation! So, it cannot copy a /live/ OpenERP database.
        """
        logger = logging.getLogger('web-services')
        template_dbs = tools.config.get_misc('databases', 'template','').split(' ')
        no_copy_dbs = tools.config.get_misc('databases', 'no_copy','').split(' ')
        if (source_db_name in no_copy_dbs) or ('*' in no_copy_dbs) \
                or (not self._check_db_allowed(source_db_name) \
                and source_db_name not in template_dbs):
            logger.critical("Asked to copy forbidden source database: %s", source_db_name)
            raise Exception("Database %s is not allowed to be copied!" % source_db_name)
        if not self._check_db_allowed(dest_db_name):
            logger.critical("Asked to copy to forbidden destination database: %s", dest_db_name)
            raise Exception("Database %s is not allowed to be created!" % dest_db_name)

        try:
            cr = None
            db = sql_db.db_connect('template1', temp=True)
            cr = db.cursor()
            cr.autocommit(True) # avoid transaction block
            cr.execute("""CREATE DATABASE "%s" ENCODING 'unicode' TEMPLATE %s """ % (dest_db_name, source_db_name))
            logger.info('CREATE DATABASE: %s template %s' % (dest_db_name.lower(), source_db_name))

            return dest_db_name
        finally:
            if cr is not None:
                cr.close()

    def exp_get_progress(self, id):
        """Report progress on the "create database" action

            May return a tuple (<float progress>, users) or throw an Exception
            for any error during creation.
        """
        if self.actions[id]['thread'].isAlive():
#           return addons.init_progress[db_name]
            return (min(self.actions[id].get('progress', 0),0.95), [])
        else:
            clean = self.actions[id]['clean']
            if clean:
                users = self.actions[id]['users']
                self.actions.pop(id)
                return (1.0, users)
            else:
                e = self.actions[id]['exception']
                self.actions.pop(id)
                raise Exception, e

    def exp_drop(self, db_name):
        """ Drop (=destroy, erase) a database

            Needless to say: use with extreme care!

            Remove the database from the disk, and all its data. This will merely issue
            a 'DROP DATABASE' on the Postgres cluster.

            As a security measure, the "debug.drop_guard" config option can disable this
            feature. But, by default, a drop is allowed!
        """
        sql_db.close_db(db_name)
        logger = logging.getLogger()

        db = sql_db.db_connect('template1', temp=True)
        cr = db.cursor()
        cr.autocommit(True) # avoid transaction block
        if tools.config.get_misc('debug', 'drop_guard', False):
            raise Exception("Not dropping database %s because guard is set!" % db_name)
        if not self._check_db_allowed(db_name):
            logger.critical("Asked to drop illegal database: %s", db_name)
            raise Exception("Database %s is not allowed to be dropped!" % db_name)
        try:
            cr.execute('DROP DATABASE "%s"' % db_name)
            logger.info('DROP DB: %s' % (db_name))
        except Exception, e:
            logger.exception('DROP DB: %s failed:' % (db_name,))
            raise Exception("Couldn't drop database %s: %s" % (db_name, e))
        finally:
            cr.close()
        return True

    def _set_pg_psw_env_var(self):
        if os.name == 'nt' and not os.environ.get('PGPASSWORD', ''):
            os.environ['PGPASSWORD'] = tools.config['db_password']
            self._pg_psw_env_var_is_set = True

    def _unset_pg_psw_env_var(self):
        if os.name == 'nt' and self._pg_psw_env_var_is_set:
            os.environ['PGPASSWORD'] = ''

    def exp_dump(self, db_name):
        """Dump (take backup) the contents of a database to the caller

            This should return the contents of database `db_name` as an SQL block. It
            calls 'pg_dump' and returns the dump base64-encoded.

            But is a bad idea! Don't use this call! (it is now disabled by default)

            If you still insist, set "databases.dump_guard=False" in the config file to 
            activate the feature again. Use at your own risk.

            If your database is anything above a few MB, the RPC protocol will not be
            able to transfer the dump, or just be clogged in the best case. Most probably,
            you may receive a *partial*, unusable dump!

            In addition, allowing remote retrieval of the full database is a security risk,
            meaning that all your data could end up in the wrong place.

            So, you should better directly backup your database at server-side, using
            any of the supplied Postgres methods.
        """
        logger = logging.getLogger('web-services')

        if tools.config.get_misc('databases', 'dump_guard', True):
            logger.error("Prevented dump of database %s, because guard is set!", db_name)
            raise Exception("Not dropping database %s because guard is set!" % db_name)

        if not self._check_db_allowed(db_name):
                logger.critical("Asked to dump illegal database: %s", db_name)
                raise Exception("Database %s is not allowed to be dumped!" % db_name)

        self._set_pg_psw_env_var()

        cmd = ['pg_dump', '--format=c', '--no-owner' , '-w']
        if tools.config['db_user']:
            cmd.append('--username=' + tools.config['db_user'])
        if tools.config['db_host']:
            cmd.append('--host=' + tools.config['db_host'])
        if tools.config['db_port']:
            cmd.append('--port=' + str(tools.config['db_port']))
        cmd.append(db_name)

        stdin, stdout = tools.exec_pg_command_pipe(*tuple(cmd))
        stdin.close()
        data = stdout.read()
        res = stdout.close()
        if res:
            logger.error('DUMP DB: %s failed\n%s' % (db_name, data))
            raise Exception("Couldn't dump database")
        logger.info('DUMP DB: %s' % (db_name))

        self._unset_pg_psw_env_var()

        return base64.encodestring(data)

    def exp_restore(self, db_name, data):
        """Restore a database, inverse of 'dump()' operation

            @param db_name A new database name, which will be created
            @param data base64-encoded SQL 'pg_dump' chunk

            Note: this function suffers the same protocol limitations as the `dump()`
            operation.
        """
        logger = logging.getLogger('web-services')

        self._set_pg_psw_env_var()

        if self.exp_db_exist(db_name):
            logger.warning('RESTORE DB: %s already exists' % (db_name,))
            raise Exception("Database already exists")

        self._create_empty_database(db_name)

        cmd = ['pg_restore', '--no-owner', '-w']
        if tools.config['db_user']:
            cmd.append('--username=' + tools.config['db_user'])
        if tools.config['db_host']:
            cmd.append('--host=' + tools.config['db_host'])
        if tools.config['db_port']:
            cmd.append('--port=' + str(tools.config['db_port']))
        cmd.append('--dbname=' + db_name)
        args2 = tuple(cmd)

        buf=base64.decodestring(data)
        if os.name == "nt":
            tmpfile = (os.environ['TMP'] or 'C:\\') + os.tmpnam()
            file(tmpfile, 'wb').write(buf)
            args2=list(args2)
            args2.append(' ' + tmpfile)
            args2=tuple(args2)
        stdin, stdout = tools.exec_pg_command_pipe(*args2)
        if not os.name == "nt":
            stdin.write(base64.decodestring(data))
        stdin.close()
        res = stdout.close()
        if res:
            raise Exception, "Couldn't restore database"
        logger.info('RESTORE DB: %s' % (db_name))

        self._unset_pg_psw_env_var()

        return True

    def exp_rename(self, old_name, new_name):
        """ Rename a Postgres/OpenERP database

            Changes the name of the database, you will be able to re-connect to the
            new name right after this call.

            Note: existing clients will be interrupted and will have to re-login to
            the new name.
        """
        sql_db.close_db(old_name)
        logger = logging.getLogger('web-services')

        allowed_res = tools.config.get_misc('databases', 'allowed')
        if allowed_res:
            # When we have a restricted set of database names, renaming must
            # be totally forbiden. That is, we both don't want some known db
            # to be renamed into an arbitrary name, nor one arbitrary db to
            # be renamed into a known name. The old/new names of the databases
            # are neither expected to be present at the config file.
            # So, just tell the admin that he has to temporarily change the
            # conf file.
            logger.error("Renaming databases is not allowed. "\
                "Please turn off the databases.allowed setting at the conf file.")
            raise Exception("Database renaming is forbiden because the names are restricted")

        db = sql_db.db_connect('template1', temp=True)
        cr = db.cursor()
        cr.autocommit(True) # avoid transaction block
        try:
            try:
                cr.execute('ALTER DATABASE "%s" RENAME TO "%s"' % (old_name, new_name))
            except Exception, e:
                logger.error('RENAME DB: %s -> %s failed:\n%s' % (old_name, new_name, e))
                raise Exception("Couldn't rename database %s to %s: %s" % (old_name, new_name, e))
            else:
                fs = os.path.join(tools.config['root_path'], 'filestore')
                if os.path.exists(os.path.join(fs, old_name)):
                    os.rename(os.path.join(fs, old_name), os.path.join(fs, new_name))

                logger.info('RENAME DB: %s -> %s' % (old_name, new_name))
        finally:
            cr.close()
        return True

    def exp_db_exist(self, db_name):
        """Check connection to database `db_name`
        """
        if not self._check_db_allowed(db_name):
            logger = logging.getLogger('web-services')
            logger.critical("Asked to connect to illegal database: %s", db_name)
            raise Exception("Database %s is not allowed to be used!" % db_name)
        return bool(sql_db.db_connect(db_name, temp=True))

    def exp_list(self, document=False):
        """List available OpenERP databases (names) at this server
        """
        if not tools.config['list_db'] and not document:
            raise Exception('AccessDenied')

        db = sql_db.db_connect('postgres', temp=True)
        cr = db.cursor()
        try:
            try:
                db_user = tools.config["db_user"]
                if not db_user and os.name == 'posix':
                    import pwd
                    db_user = pwd.getpwuid(os.getuid())[0]
                if not db_user:
                    cr.execute("""select decode(usename, 'escape') from pg_user
                                    where usesysid=(select datdba from pg_database where datname=%s)""", (tools.config["db_name"],))
                    res = cr.fetchone()
                    db_user = res and str(res[0])
                if db_user:
                    cr.execute("""select decode(datname, 'escape') from pg_database
                                    where datdba=(select usesysid from pg_user where usename=%s)
                                      and datname not in %s order by datname""", (db_user, sql_db.get_template_dbnames()))
                else:
                    cr.execute("""select decode(datname, 'escape') from pg_database
                                    where datname not in %s order by datname""", (sql_db.get_template_dbnames(),))
                res = [str(name) for (name,) in cr.fetchall()]
            except Exception:
                logger = logging.getLogger('web-services')
                logger.warning("Cannot query the list of dbs", exc_info=True)
                res = []
        finally:
            cr.close()
        allowed_res = tools.config.get_misc('databases', 'allowed')
        if allowed_res:
            dbs_allowed = [ x.strip() for x in allowed_res.split(' ')]
            res_o = res
            res = []
            for s in res_o:
                if s in dbs_allowed:
                    res.append(s)

        res.sort()
        return res

    def exp_change_admin_password(self, new_password):
        tools.config['admin_passwd'] = new_password
        tools.config.save()
        return True

    def exp_list_lang(self):
        """List available languages (system-wide) for a new database
        """
        return tools.scan_languages()

    def exp_server_version(self):
        """ Return the version of the server
            Used by the client to verify the compatibility with its own version
        """
        return release.version

    def exp_migrate_databases(self,databases):

        from osv.orm import except_orm
        from osv.osv import except_osv

        l = logging.getLogger('migration')
        for db in databases:
            if not self._check_db_allowed(db):
                l.critical("Asked to migrate illegal database: %s", db)
                raise Exception("Database %s is not allowed to be migrated!" % db)
            try:
                l.info('migrate database %s' % (db,))
                tools.config['update']['base'] = True
                pooler.restart_pool(db, force_demo=False, update_module=True)
            except except_orm, inst:
                self.abortResponse(1, inst.name, 'warning', inst.value)
            except except_osv, inst:
                self.abortResponse(1, inst.name, inst.exc_type, inst.value)
            except Exception:
                l.exception("Migrate database %s failed" % db)
                raise
        return True

    def stats(self, _pre_msg=None):
        """ The "db" service will list all loaded databases
        """
        ret = baseExportService.stats(self, _pre_msg='%d databases' % len(pooler.pool_dic))
        for name, pool in pooler.pool_dic.iteritems():
            ret += '\n    "%s": %s' % (name, pool.stat_string())
        return ret

db()

class _ObjectService(baseExportService):
     "A common base class for those who have fn(db, uid, password,...) "

     def common_dispatch(self, method, auth, params):
        (db, uid, passwd ) = params[0:3]
        params = params[3:]
        security.check(db,uid,passwd)
        cr = pooler.get_db(db).cursor()
        fn = getattr(self, 'exp_'+method)
        res = fn(cr, uid, *params)
        cr.commit()
        cr.close()
        return res

class common(_ObjectService):
    """Services applying to the whole server instance, irrespective of db.
    """
    _auth_commands = { # 'db-broken': [ 'ir_set','ir_del', 'ir_get' ], 5.0 interface
                'pub': ['about', 'timezone_get', 'get_server_environment',
                        'login_message','get_stats', 'check_connectivity',
                        'list_http_services', 'get_options'],
                'root': ['get_available_updates', 'get_migration_scripts',
                        'set_loglevel', 'set_obj_debug', 'set_pool_debug',
                        'set_logger_level', 'get_pgmode', 'set_pgmode',
                        'get_loglevel', 'get_sqlcount', 'get_sql_stats',
                        'reset_sql_stats',
                        'get_garbage_stats',
                        'get_export_services',
                        'get_os_time']
                }
    def __init__(self,name="common"):
        _ObjectService.__init__(self,name)
        self.joinGroup("web-services")

    def dispatch(self, method, auth, params):
        logger = logging.getLogger('web-services')
        if method in [ 'ir_set','ir_del', 'ir_get' ]:
            return self.common_dispatch(method,auth,params)
        if method == 'login':
            # At this old dispatcher, we do NOT update the auth proxy
            res = security.login(params[0], params[1], params[2])
            msg = res and 'successful login' or 'bad login or password'
            # TODO log the client ip address..
            logger.info("%s from '%s' using database '%s'" % (msg, params[1], params[0].lower()))
            return res or False
        elif method == 'logout':
            if auth:
                auth.logout(params[1])
            logger.info('Logout %s from database %s'%(params[1],db))
            return True
        elif method in self._auth_commands['pub']:
            pass
        elif method in self._auth_commands['root']:
            passwd = params[0]
            params = params[1:]
            security.check_super(passwd)
        else:
            raise Exception("Method not found: %s" % method)

        fn = getattr(self, 'exp_'+method)
        return fn(*params)

    def new_dispatch(self, method, auth, params, auth_domain=None):
        # Double check, that we have the correct authentication:
        if method == 'login':
            if not (auth and auth.provider.domain == 'db'):
                raise Exception("Method not found: %s" % method)
            # By this time, an authentication should already be done at the
            # http level
            if not auth.last_auth:
                return False
            acds = auth.auth_creds[auth.last_auth]
            assert(acds[0] == params[1])
            assert(acds[1] == params[2])
            assert(acds[2] == params[0])
            assert acds[3] != False and acds[3] != None

            log = logging.getLogger('web-service')
            log.info("login from '%s' using database '%s'" % (params[1], params[0].lower()))
            return acds[3]
        else:
            return super(common, self).new_dispatch(method, auth, params, auth_domain)

    def exp_ir_set(self, cr, uid, keys, args, name, value, replace=True, isobject=False):
        res = ir.ir_set(cr,uid, keys, args, name, value, replace, isobject)
        return res

    def exp_ir_del(self, cr, uid, id):
        res = ir.ir_del(cr,uid, id)
        return res

    def exp_ir_get(self, cr, uid, keys, args=None, meta=None, context=None):
        if not args:
            args=[]
        if not context:
            context={}
        res = ir.ir_get(cr,uid, keys, args, meta, context)
        return res

    def exp_about(self, extended=False):
        """Return information about the OpenERP Server.

        @param extended: if True then return version info
        @return string if extended is False else tuple
        """

        info = _('''

OpenERP/F3 is an ERP+CRM program for small and medium businesses.

The whole source code is distributed under the terms of the
GNU Public Licence.

(c) 2003-2009, Fabien Pinckaers - Tiny sprl
(c) 2009-2011, OpenERP SA.
(c) 2009, 2011-2015, P. Christeas
''')

        if extended:
            return info, release.version
        return info

    def exp_timezone_get(self, *args):
        return tools.misc.get_server_timezone()

    def exp_get_available_updates(self, contract_id, contract_password):
        import tools.maintenance as tm
        try:
            rc = tm.remote_contract(contract_id, contract_password)
            if not rc.id:
                raise tm.RemoteContractException('This contract does not exist or is not active')

            return rc.get_available_updates(rc.id, addons.get_modules_with_version())

        except tm.RemoteContractException, e:
            self.abortResponse(1, 'Migration Error', 'warning', str(e))


    def exp_get_migration_scripts(self, contract_id, contract_password):
        l = logging.getLogger('migration')
        import tools.maintenance as tm
        try:
            rc = tm.remote_contract(contract_id, contract_password)
            if not rc.id:
                raise tm.RemoteContractException('This contract does not exist or is not active')
            if rc.status != 'full':
                raise tm.RemoteContractException('Can not get updates for a partial contract')

            l.info('starting migration with contract %s' % (rc.name,))

            zips = rc.retrieve_updates(rc.id, addons.get_modules_with_version())

            from shutil import rmtree, copytree, copy

            backup_directory = os.path.join(tools.config['root_path'], 'backup', time.strftime('%Y-%m-%d-%H-%M'))
            if zips and not os.path.isdir(backup_directory):
                l.info('Create a new backup directory to \
                                store the old modules: %s' % (backup_directory,))
                os.makedirs(backup_directory)

            for module in zips:
                l.info('upgrade module %s' % (module,))
                mp = addons.get_module_path(module)
                if mp:
                    if os.path.isdir(mp):
                        copytree(mp, os.path.join(backup_directory, module))
                        if os.path.islink(mp):
                            os.unlink(mp)
                        else:
                            rmtree(mp)
                    else:
                        copy(mp + 'zip', backup_directory)
                        os.unlink(mp + '.zip')

                try:
                    try:
                        base64_decoded = base64.decodestring(zips[module])
                    except Exception:
                        l.exception('unable to read the module %s' % (module,))
                        raise

                    zip_contents = StringIO(base64_decoded)
                    zip_contents.seek(0)
                    try:
                        try:
                            tools.extract_zip_file(zip_contents, tools.config['addons_path'] )
                        except Exception:
                            l.exception('unable to extract the module %s' % (module, ))
                            rmtree(module)
                            raise
                    finally:
                        zip_contents.close()
                except Exception:
                    l.exception('restore the previous version of the module %s' % (module, ))
                    nmp = os.path.join(backup_directory, module)
                    if os.path.isdir(nmp):
                        copytree(nmp, tools.config['addons_path'])
                    else:
                        copy(nmp+'.zip', tools.config['addons_path'])
                    raise

            return True
        except tm.RemoteContractException, e:
            self.abortResponse(1, 'Migration Error', 'warning', str(e))
        except Exception, e:
            l.exception("%s" % e)
            raise

    def exp_get_server_environment(self):
        """Return a paragraph of string, the environment of the server process

            This should be enough to identify the platform of the server.
        """
        os_lang = '.'.join( [x for x in locale.getdefaultlocale() if x] )
        if not os_lang:
            os_lang = 'NOT SET'
        environment = '\nEnvironment Information : \n' \
                     'System : %s\n' \
                     'OS Name : %s\n' \
                     %(platform.platform(), platform.os.name)
        if os.name == 'posix':
          if platform.system() == 'Linux':
             lsbinfo = os.popen('lsb_release -a').read()
             environment += '%s'%(lsbinfo)
          else:
             environment += 'Your System is not lsb compliant\n'
        environment += 'Operating System Release : %s\n' \
                    'Operating System Version : %s\n' \
                    'Operating System Architecture : %s\n' \
                    'Operating System Locale : %s\n'\
                    'Python Version : %s\n'\
                    'OpenERP-Server Version : %s'\
                    %(platform.release(), platform.version(), platform.architecture()[0],
                      os_lang, platform.python_version(),release.version)
        return environment

    def exp_login_message(self):
        return tools.config.get('login_message', False)

    def exp_set_loglevel(self, loglevel, logger=None):
        """Adjust the logging level of some pythonic logger

            If `logger` is not specified, the level of the root logger (possibly affecting
            many others) will be set. If specified, it can be any existing or /not/ existing
            logger. The latter is needed in order to set levels for messages that are not
            yet issued.

            In a twist, `loglevel` can be a dict, like `{logger: level, ...}` setting
            several loggers at once.
        """
        l = netsvc.Logger()
        l.set_loglevel(loglevel, logger)
        return True

    def exp_set_logger_level(self, logger, loglevel):
        l = netsvc.Logger()
        l.set_logger_level(logger, loglevel)
        return True

    def exp_get_loglevel(self, logger=None):
        """Get logging level of some logger(s)

            If `logger==None`, the main logging level will be returned.
            If it is any of the *existing* loggers, it will return its level. It will
            raise a KeyError if the logger doesn't exist.
            If it is the special '*' string, all loggers and their levels will be
            returned in a dict.
        """
        l = netsvc.Logger()
        return l.get_loglevel(logger)

    def exp_get_pgmode(self):
        """Returns the Postgres operation mode.

            see. set_pgmode()
        """
        return sql_db.Cursor.get_pgmode()

    def exp_set_pgmode(self, pgmode):
        """Set postgres operation mode

            The mode of operation affects usage of advanced Postgres features by the ORM.
            It can be changed in runtime, in order to test the older or newer algorithms.
        """
        assert pgmode in ['old', 'sql', 'pg00', 'pg84', 'pg90', 'pg91', 'pg92', 'pg93', 'pg94']
        sql_db.Cursor.set_pgmode(pgmode)
        return True

    def exp_set_obj_debug(self,db, obj, do_debug):
        """Set ORM object debug

            @param db the database to set at (name)
            @param obj the ORM object to set (like 'res.partner')
            @param do_debug boolean to have debugging or not

            Per-object debug allows terse messages on the operations for some specific
            ORM model. Rather than flooding the logs with all low-level ops, this feature
            allows focused logging of only a few objects.

            Setting one object does not clear any others.

            Object debugging typically prints all SQL calls, ORM methods like read() or
            write(), domain expressions on the object and browse() field statistics. It
            is up to the developer to add more messages in custom methods.

            All these messages are issued at the `DEBUG` log-level.
        """
        log = logging.getLogger('web-services')
        log.info("setting debug for %s@%s to %s" %(obj, db, do_debug))
        ls = netsvc.LocalService('object_proxy')
        res = ls.set_debug(db, obj, do_debug)
        return res

    def exp_set_pool_debug(self,db, do_debug):
        """Activate debugging on the pool of Postgres connections

            This will debug opening/closing/release of database cursors, for each
            transaction happening inside the server.
        """
        sql_db._Pool.set_pool_debug(do_debug)
        return None

    def exp_get_stats(self):
        """Return statistics about the server and internal services

            Several subsystems of the OpenERP server are registered as 'services'.
            These should fill this paragraph with info about their current state,
            like threads, counts etc.
        """
        import threading
        res = "OpenERP server: %d threads\n" % threading.active_count()
        res += netsvc.Server.allStats()
        res += "\n"
        res += netsvc.Agent.stats()
        res += "\n"
        res += netsvc.ExportService.allStats()
        try:
            import gc
            if gc.isenabled():
                res += "\nPython GC enabled: %d:%d:%d objs." % \
                    gc.get_count()
        except ImportError: pass
        try:
            from tools import lru
            res += "\nLRU counts: LRU: %d, nodes: %d" %  \
                    (sys.getrefcount(lru.LRU), sys.getrefcount(lru.LRUNode))
        except Exception: pass
        return res

    def exp_list_http_services(self, *args):
        """List HTTP paths the embedded server is serving
        """
        from service import http_server
        return http_server.list_http_services(*args)

    def exp_check_connectivity(self):
        """This will test if the openerp server can connect to Postgres.
        """
        return bool(sql_db.db_connect('template1', temp=True))

    def exp_get_os_time(self):
        """Return time elapsed in the server process

            This includes real and CPU time on the server, useful for timing the
            performance of our operations.
        """
        return os.times()

    def exp_get_sqlcount(self):
        """Get current count (sum) of SQL calls

            Counters advance only when the 'db.cursor' logger is at DEBUG_SQL level.
            This can be adjusted at runtime.
        """
        logger = logging.getLogger('db.cursor')
        if not logger.isEnabledFor(logging.DEBUG_SQL):
            logger.warning("Counters of SQL will not be reliable unless DEBUG_SQL is set at the server's config.")
        return sql_db.sql_counter

    def exp_get_sql_stats(self):
        """Retrieve the sql statistics from the pool.

        Unfortunately, XML-RPC won't allow tuple indexes, so we have to
        rearrange the dict.

        Returns a dict of `table:operation:(count, time)` .
        """
        ret = {}
        for skey, val in sql_db._Pool.sql_stats.items():
            sk0 = skey[0]
            if not isinstance(skey[0], str):
                sk0 = str(skey[0])
            ret.setdefault(sk0,{})
            ret[sk0][skey[1]] = val
        return ret

    def exp_reset_sql_stats(self):
        """Resets the SQL statistics table
        """
        sql_db._Pool.sql_stats = {}
        return True

    def exp_get_garbage_stats(self):
        import gc
        garbage_count = {}
        for garb in gc.garbage:
            try:
                name = '%s.%s' % (garb.__class__.__module__, garb.__class__.__name__)
                garbage_count.setdefault(name, 0)
                garbage_count[name] += 1
            except Exception, e:
                print "Exception:", e
                continue
            # Perhaps list the attributes of garb that are instances of object

        return garbage_count

    def exp_get_options(self, module=None):
        """Return a list of options, keywords, that the server supports.

        Apart from the server version, which should be a linear number,
        some server branches may support extra API functionality. By this
        call, the server can advertise these extensions to compatible
        clients.
        """
        if module:
            raise NotImplementedError('No module-specific options yet')
        return release.server_options

    def exp_get_export_services(self, group=None, service=None, method=None):
        """Return the available netsvc.ExportService methods

            @param group if specified, lists services of that group
            @param service if specified, lists methods of that service
            @param method if specified (and service is set), introspects that method
        """
        from osv.osv import except_osv
        import inspect
        if not tools.config.get_misc('debug', 'introspection', False):
            raise except_osv('Access Error', 'Introspection is not enabled')

        if (not group) and (not service):
            return {'groups': netsvc.ExportService._groups.keys(),
                    'services': netsvc.ExportService._services.keys() }
        elif service and service in netsvc.ExportService._services:
            svc = netsvc.ExportService._services[service]
            ret = {'services': [service,] , }
            if isinstance(svc, baseExportService):
                ret['methods'] = {}
                if not method:
                    doc = inspect.getdoc(svc) or ''
                    ret['service_doc'] = doc
                    for key, vals in svc._auth_commands.items():
                        vals2 = filter( lambda v: callable(getattr(svc, 'exp_' + v)), vals)
                        ret['methods'][key] = vals2
                else:
                    # introspect some method
                    for key, vals in svc._auth_commands.items():
                        if method in vals:
                            m_fn = getattr(svc, 'exp_' + method)
                            if m_fn and callable(m_fn):
                                ret['methods'][key] = [method,]
                                # now, introspect!
                                argspec = inspect.getargspec(m_fn)
                                doc = inspect.getdoc(m_fn) or ''
                                ret['service_method'] = method + inspect.formatargspec(*argspec)
                                ret['service_method_doc'] = doc.rstrip()
                            else:
                                ret['methods'] = False
                            break
            return ret
        elif group and group in netsvc.ExportService._groups:
            grp = netsvc.ExportService._groups[group]
            return {'groups': [group,], 'services': grp.keys() }
        else:
            return {}
common()

class objects_proxy(baseExportService):
    _auth_commands = { 'db': ['execute','exec_workflow', 'exec_dict'],
            'root': ['obj_list', 'method_list', 'method_explain', 'list_workflow'] }
    def __init__(self, name="object"):
        netsvc.ExportService.__init__(self,name)
        self.joinGroup('web-services')
        self._ls = netsvc.LocalService('object_proxy')

    def dispatch(self, method, auth, params):
        if method in self._auth_commands['root']:
            passwd = params[0]
            params = params[1:]
            security.check_super(passwd)
            fn = getattr(self._ls, method)
            res = fn(*params, **{'auth_proxy': auth})
            return res
        (db, uid, passwd ) = params[0:3]
        params = params[3:]
        if method not in ['execute','exec_workflow', 'exec_dict', 'obj_list']:
            raise KeyError("Method not supported %s" % method)
        security.check(db,uid,passwd)
        fn = getattr(self._ls, method)
        res = fn(db, uid, *params, **{'auth_proxy': auth})
        return res

    def new_dispatch(self, method, auth, params, auth_domain=None):
        # Double check, that we have the correct authentication:
        if not auth:
            raise Exception("Not auth domain for object service")
        if auth.provider.domain not in self._auth_commands:
            from websrv_lib import AuthRejectedExc
            raise AuthRejectedExc("Invalid domain for object service")

        if method not in self._auth_commands[auth.provider.domain]:
            raise Exception("Method not found: %s" % method)

        fn = getattr(self._ls, method)

        if auth.provider.domain == 'root':
            res = fn(*params, **{'auth_proxy': auth})
            return res

        acds = auth.auth_creds[auth.last_auth]
        db, uid = (acds[2], acds[3])
        res = fn(db, uid, *params, **{'auth_proxy': auth})
        return res

    def stats(self, _pre_msg='No statistics'):
        try:
            from osv import orm
            msg = ''
            for klass in ('browse_record', 'browse_record_list', 'browse_null',
                        'orm_memory', 'orm'):
                msg += "%s[%d] " % (klass, sys.getrefcount(getattr(orm,klass)))
        except Exception, e:
            msg = str(e)
        return "%s (%s.%s): %s" % ('object',
                    self.__class__.__module__, self.__class__.__name__,
                    msg)

objects_proxy()


class dbExportDispatch:
    """ Intermediate class for those ExportServices that call fn(db, uid, ...)
        These classes don't need the cursor, but just the name of the db
    """
    def new_dispatch(self, method, auth, params, auth_domain=None):
        # Double check, that we have the correct authentication:
        if not auth:
                domain='pub'
        else:
                domain=auth.provider.domain
        if method not in self._auth_commands[domain]:
            raise Exception("Method not found: %s" % method)

        fn = getattr(self, 'exp_'+method)
        if domain == 'db':
            db, uid = auth.auth_creds[auth.last_auth][2:4]
            res = fn(db, uid, *params)
            return res
        else:
            return fn(*params)

#
# Wizard ID: 1
#    - None = end of wizard
#
# Wizard Type: 'form'
#    - form
#    - print
#
# Wizard datas: {}
# TODO: change local request to OSE request/reply pattern
#
class wizard(dbExportDispatch,baseExportService):
    _auth_commands = { 'db': ['execute','create'] }
    def __init__(self, name='wizard'):
        netsvc.ExportService.__init__(self,name)
        self.joinGroup('web-services')
        self.id = 0
        self.wiz_datas = {}
        self.wiz_name = {}
        self.wiz_uid = {}

    def dispatch(self, method, auth, params):
        (db, uid, passwd ) = params[0:3]
        params = params[3:]
        if method not in ['execute','create']:
            raise KeyError("Method not supported %s" % method)
        security.check(db,uid,passwd)
        fn = getattr(self, 'exp_'+method)
        res = fn(db, uid, *params)
        return res


    def _execute(self, db, uid, wiz_id, datas, action, context):
        self.wiz_datas[wiz_id].update(datas)
        wiz = netsvc.LocalService('wizard.'+self.wiz_name[wiz_id])
        return wiz.execute(db, uid, self.wiz_datas[wiz_id], action, context)

    def exp_create(self, db, uid, wiz_name, datas=None):
        if not datas:
            datas={}
#FIXME: this is not thread-safe
        self.id += 1
        self.wiz_datas[self.id] = {}
        self.wiz_name[self.id] = wiz_name
        self.wiz_uid[self.id] = uid
        return self.id

    def exp_execute(self, db, uid, wiz_id, datas, action='init', context=None):
        if not context:
            context={}

        if wiz_id in self.wiz_uid:
            if self.wiz_uid[wiz_id] == uid:
                return self._execute(db, uid, wiz_id, datas, action, context)
            else:
                raise Exception, 'AccessDenied'
        else:
            raise Exception, 'WizardNotFound'
wizard()

#
# TODO: set a maximum report number per user to avoid DOS attacks
#
# Report state:
#     False -> True
#

class ExceptionWithTraceback(Exception):
    def __init__(self, msg, tb):
        self.message = msg
        self.traceback = tb
        self.args = (msg, tb)

class _report_spool_job(threading.Thread):
    def __init__(self, id, db, uid, obj, ids, datas=None, context=None):
        """A report job, that should be spooled in the background

        @param id the index at the parent spool list, shall not be trusted,
                only useful for the repr()
        @param db the database name
        @param uid the calling user
        @param obj the report orm object (string w/o the 'report.' prefix)
        @param ids of the obj model
        @param datas dictionary of input to report
        """
        threading.Thread.__init__(self)
        self.id = id
        self.uid = uid
        self.db = db
        self.report_obj = obj
        self.ids = ids
        self.datas = datas
        self.context = context
        if self.context is None:
            self.context = {}
        self.result = False
        self.format = None
        self.state = False
        self.exception = None
        self.name = "report-%s-%s" % (self.report_obj, self.id)

    def run(self):
        try:
            self.cr = pooler.get_db(self.db).cursor()
            self.go()
            self.cr.commit()
        except Exception, e:
            logger = logging.getLogger('web-services')
            logger.exception('Exception: %s' % (e))
            if hasattr(e, 'name') and hasattr(e, 'value'):
                self.exception = ExceptionWithTraceback(tools.ustr(e.name), tools.ustr(e.value))
            else:
                self.exception = e
            self.state = True
            return
        except KeyboardInterrupt, e:
            tb = sys.exc_info()
            logger = logging.getLogger('web-services')
            logger.exception('Interrupt of report: %r' % self)
            self.exception = ExceptionWithTraceback('KeyboardInterrupt of report: %r' % self, tb)
            self.state = True
            # we don't need to raise higher, because we already printed the tb
            # and are exiting the thread loop.
            return
        finally:
            if self.cr:
                self.cr.close()
                self.cr = None
        return True

    def stop(self):
        """Try to kill the job.

        So far there is no genuinely good way to stop the thread (is there?),
        so we can at least kill the cursor, so that the rest of the job borks.
        """
        self.must_stop = True
        if self.cr:
            self.cr.rollback()
            self.cr.close()
            self.cr = None

    def __repr__(self):
        """Readable name of report job
        """
        return "<Report job #%s: %s.%s>" % (self.id, self.db, self.report_obj)

    def go(self,):
        cr = self.cr
        obj = netsvc.LocalService('report.' + self.report_obj)
        (result, format) = obj.create(cr, self.uid, self.ids, self.datas, self.context)
        if not result:
            tb = sys.exc_info()
            self.exception = ExceptionWithTraceback('RML is not available at specified location or not enough data to print!', tb)
        self.result = result
        self.format = format
        self.state = True
        return True

class report_spool(dbExportDispatch, baseExportService):
    _auth_commands = { 'db': ['report','report_get', 'report_stop', 'report_list'] }
    def __init__(self, name='report'):
        netsvc.ExportService.__init__(self, name)
        self.joinGroup('web-services')
        self._reports = {}
        self.id = 0
        self.id_protect = threading.Semaphore()

    def dispatch(self, method, auth, params):
        (db, uid, passwd ) = params[0:3]
        params = params[3:]
        if method not in ['report','report_get', 'report_stop', 'report_list']:
            raise KeyError("Method not supported %s" % method)
        security.check(db,uid,passwd)
        fn = getattr(self, 'exp_' + method)
        res = fn(db, uid, *params)
        return res

    def stats(self, _pre_msg=None):
        ret = baseExportService.stats(self, _pre_msg='%d reports' % len(self._reports))
        for id, r in self._reports.items():
            if not r:
                continue
            ret += '\n    [%d] ' % id
            if r.is_alive() or not r.state:
                ret += 'running '
            else:
                ret += 'finished '
            ret += repr(r)
        return ret

    def exp_report(self, db, uid, object, ids, datas=None, context=None):
        """Request, start preparing a report
        """
        if not datas:
            datas={}
        if not context:
            context={}

        self.id_protect.acquire()
        self.id += 1
        id = self.id
        self.id_protect.release()

        self._reports[id] = _report_spool_job(id, db, uid, object, ids, datas=datas, context=context)
        self._reports[id].start()
        return id

    def _check_report(self, report_id):
        report = self._reports[report_id]
        exc = report.exception
        if exc:
            self.id_protect.acquire()
            try:
                del self._reports[report_id]
            finally:
                self.id_protect.release()
            self.abortResponse(1, exc.__class__.__name__, 'warning', exc.message)

        res = {'state': report.state }
        if res['state']:
            if tools.config['reportgz']:
                import zlib
                res2 = zlib.compress(report.result)
                res['code'] = 'zlib'
            else:
                #CHECKME: why is this needed???
                if isinstance(report.result, unicode):
                    res2 = report.result.encode('latin1', 'replace')
                else:
                    res2 = report.result
            if res2:
                res['result'] = base64.encodestring(res2)
            res['format'] = report.format
            self.id_protect.acquire()
            try:
                del self._reports[report_id]
            finally:
                self.id_protect.release()
        # else: leave report there, return the state only
        return res

    def exp_report_list(self, db, uid):
        """Return the list of reports, active or finished

            The list is limited to the calling user, unless called by the admin.

            @return list of tuples: (id, name, running)
        """
        ret = []
        for report in self._reports.values():
            if uid != 1 and report.uid != uid:
                continue
            ret.append((report.id, report.report_obj, report.state))
        return ret

    def exp_report_get(self, db, uid, report_id):
        """ Get a prepared report, pop it from the queue

            If the report is still running, it will return False
        """
        if report_id in self._reports and  self._reports[report_id].db == db:
            if self._reports[report_id].uid == uid:
                return self._check_report(report_id)
            else:
                raise Exception, 'AccessDenied'
        else:
            raise Exception, 'ReportNotFound'

    def exp_report_stop(self, db, uid, report_id, timeout=5.0):
        """ Stop a running report, wait for it to finish

            @return True if stopped, False if alredy finished,
                    Exception('Timeout') if cannot stop

            Note that after a "report_stop" request, the caller shall
            do one more "report_get" to fetch the exception and free
            the job object.
        """
        if report_id in self._reports and self._reports[report_id].db == db:
            report = self._reports[report_id]
            if report.uid == uid or uid == 1:
                if report.is_alive() and not report.state:
                    report.stop()
                    report.join(timeout=timeout)
                    if report.is_alive():
                        raise Exception('Timeout')

                    return True
                else:
                    return False
            else:
                raise Exception, 'AccessDenied'
        else:
            raise Exception, 'ReportNotFound'

report_spool()


# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

