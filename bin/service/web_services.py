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
from tools.translate import _
from cStringIO import StringIO

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
            u, p, db, uid = auth.auth_creds[auth.last_auth]
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
    _auth_commands = { 'root': [ 'create', 'get_progress', 'drop', 'dump', 
                'restore', 'rename', 
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

    #def new_dispatch(self,method,auth,params):
    #    pass
    def _create_empty_database(self, name):
        db = sql_db.db_connect('template1')
        cr = db.cursor()
        try:
            cr.autocommit(True) # avoid transaction block
            cr.execute("""CREATE DATABASE "%s" ENCODING 'unicode' TEMPLATE "template0" """ % name)
        finally:
            cr.close()

    def exp_create(self, db_name, demo, lang, user_password='admin'):
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
                    tools.config['lang'] = lang
                    cr.commit()
                    cr.close()
                    cr = None
                    pool = pooler.restart_pool(db_name, demo, serv.actions[id],
                            update_module=True)[1]

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
        logger = logging.getLogger('web-services')
        logger.info('CREATE DATABASE: %s' % (db_name.lower()))
        dbi = DBInitialize()
        create_thread = threading.Thread(target=dbi,
                args=(self, id, db_name, demo, lang, user_password))
        create_thread.start()
        self.actions[id]['thread'] = create_thread
        return id

    def exp_get_progress(self, id):
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
        sql_db.close_db(db_name)
        logger = logging.getLogger()

        db = sql_db.db_connect('template1')
        cr = db.cursor()
        cr.autocommit(True) # avoid transaction block
        if tools.config.get_misc('debug', 'drop_guard', False):
            raise Exception("Not dropping database %s because guard is set!" % db_name)
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
        logger = logging.getLogger('web-services')

        if tools.config.get_misc('databases', 'dump_guard', False):
            logger.error("Prevented dump of database %s, because guard is set!", db_name)
            raise Exception("Not dropping database %s because guard is set!" % db_name)
        
        allowed_res = tools.config.get_misc('databases', 'allowed')
        if allowed_res:
            dbs_allowed = [ x.strip() for x in allowed_res.split(' ')]
            if not db_name in dbs_allowed:
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

        db = sql_db.db_connect('template1')
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
        ## Not True: in fact, check if connection to database is possible. The database may exists
        return bool(sql_db.db_connect(db_name))

    def exp_list(self, document=False):
        if not tools.config['list_db'] and not document:
            raise Exception('AccessDenied')

        db = sql_db.db_connect('template1')
        cr = db.cursor()
        try:
            try:
                db_user = tools.config["db_user"]
                if not db_user and os.name == 'posix':
                    import pwd
                    db_user = pwd.getpwuid(os.getuid())[0]
                if not db_user:
                    cr.execute("select decode(usename, 'escape') from pg_user where usesysid=(select datdba from pg_database where datname=%s)", (tools.config["db_name"],))
                    res = cr.fetchone()
                    db_user = res and str(res[0])
                if db_user:
                    cr.execute("select decode(datname, 'escape') from pg_database where datdba=(select usesysid from pg_user where usename=%s) and datname not in ('template0', 'template1', 'postgres') order by datname", (db_user,))
                else:
                    cr.execute("select decode(datname, 'escape') from pg_database where datname not in('template0', 'template1','postgres') order by datname")
                res = [str(name) for (name,) in cr.fetchall()]
            except Exception:
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
    _auth_commands = { 'db-broken': [ 'ir_set','ir_del', 'ir_get' ],
                'pub': ['about', 'timezone_get', 'get_server_environment',
                        'login_message','get_stats', 'check_connectivity',
                        'list_http_services', 'get_options'],
                'root': ['get_available_updates', 'get_migration_scripts',
                        'set_loglevel', 'set_obj_debug', 'set_pool_debug',
                        'set_logger_level', 'get_pgmode', 'set_pgmode',
                        'get_loglevel', 'get_sqlcount', 'get_sql_stats',
                        'reset_sql_stats',
                        'get_garbage_stats',
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

OpenERP is an ERP+CRM program for small and medium businesses.

The whole source code is distributed under the terms of the
GNU Public Licence.

(c) 2003-TODAY, Fabien Pinckaers - Tiny sprl''')

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
        l = netsvc.Logger()
        l.set_loglevel(loglevel, logger)
        return True

    def exp_set_logger_level(self, logger, loglevel):
        l = netsvc.Logger()
        l.set_logger_level(logger, loglevel)
        return True

    def exp_get_loglevel(self, logger=None):
        l = netsvc.Logger()
        return l.get_loglevel(logger)

    def exp_get_pgmode(self):
        return sql_db.Cursor.get_pgmode()

    def exp_set_pgmode(self, pgmode):
        assert pgmode in ['old', 'sql', 'pgsql', 'pg84', 'pg90']
        sql_db.Cursor.set_pgmode(pgmode)
        return True

    def exp_set_obj_debug(self,db, obj, do_debug):
        log = logging.getLogger('web-services')
        log.info("setting debug for %s@%s to %s" %(obj, db, do_debug))
        ls = netsvc.LocalService('object_proxy')
        res = ls.set_debug(db, obj, do_debug)
        return res

    def exp_set_pool_debug(self,db, do_debug):
        sql_db._Pool.set_pool_debug(do_debug)
        return None

    def exp_get_stats(self):
        import threading
        res = "OpenERP server: %d threads\n" % threading.active_count()
        res += netsvc.Server.allStats()
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
        from service import http_server
        return http_server.list_http_services(*args)

    def exp_check_connectivity(self):
        return bool(sql_db.db_connect('template1'))
        
    def exp_get_os_time(self):
        return os.times()

    def exp_get_sqlcount(self):
        logger = logging.getLogger('db.cursor')
        if not logger.isEnabledFor(logging.DEBUG_SQL):
            logger.warning("Counters of SQL will not be reliable unless DEBUG_SQL is set at the server's config.")
        return sql_db.sql_counter

    def exp_get_sql_stats(self):
        """Retrieve the sql statistics from the pool.
        
        Unfortunately, XML-RPC won't allow tuple indexes, so we have to 
        rearrange the dict.
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

common()

class objects_proxy(baseExportService):
    _auth_commands = { 'db': ['execute','exec_workflow', 'exec_dict'], 'root': ['obj_list',] }
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
            res = fn(*params)
            return res
        (db, uid, passwd ) = params[0:3]
        params = params[3:]
        if method not in ['execute','exec_workflow', 'exec_dict', 'obj_list']:
            raise KeyError("Method not supported %s" % method)
        security.check(db,uid,passwd)
        fn = getattr(self._ls, method)
        res = fn(db, uid, *params)
        return res

    def new_dispatch(self, method, auth, params, auth_domain=None):
        # Double check, that we have the correct authentication:
        if not auth:
            raise Exception("Not auth domain for object service")
        if auth.provider.domain not in self._auth_commands:
            raise Exception("Invalid domain for object service")

        if method not in self._auth_commands[auth.provider.domain]:
            raise Exception("Method not found: %s" % method)

        fn = getattr(self._ls, method)

        if auth.provider.domain == 'root':
            res = fn(*params)
            return res

        acds = auth.auth_creds[auth.last_auth]
        db, uid = (acds[2], acds[3])
        res = fn(db, uid, *params)
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
            u, p, db, uid = auth.auth_creds[auth.last_auth]
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
                tb = sys.exc_info()
                self.exception = ExceptionWithTraceback(tools.exception_to_unicode(e), tb)
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
    _auth_commands = { 'db': ['report','report_get', 'report_stop'] }
    def __init__(self, name='report'):
        netsvc.ExportService.__init__(self, name)
        self.joinGroup('web-services')
        self._reports = {}
        self.id = 0
        self.id_protect = threading.Semaphore()

    def dispatch(self, method, auth, params):
        (db, uid, passwd ) = params[0:3]
        params = params[3:]
        if method not in ['report','report_get', 'report_stop']:
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
            # rfc: Why send the traceback?
            self.abortResponse(exc, exc.message, 'warning', exc.traceback)
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
            del self._reports[report_id]
            self.id_protect.release()
        return res

    def exp_report_get(self, db, uid, report_id):
        if report_id in self._reports:
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
        if report_id in self._reports:
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

