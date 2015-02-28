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

#.apidoc title: Pool of Objects functions

pool_dic = {}

def get_db_and_pool(db_name, force_demo=False, status=None, update_module=False, pooljobs=True, languages=False):
    """Get an active Database and its pool of objects

        May *load* the database, if it is not active.

        @return db,pool
    """
    if not status:
        status={}

    db = get_db_only(db_name)

    if db_name in pool_dic:
        pool = pool_dic[db_name]
    else:
        import addons
        import osv.osv
        import logging
        from tools import config
        from service.websrv_lib import AuthRejectedExc

        log = logging.getLogger('pooler')
        allowed_res = config.get_misc('databases', 'allowed')
        if allowed_res:
            dbs_allowed = [ x.strip() for x in allowed_res.split(' ')]
            if db_name not in dbs_allowed:
                log.critical('Illegal database requested: %s', db_name)
                raise AuthRejectedExc('Illegal database: %s'% db_name)

        log.info("Starting pooler of database: %s" % db_name)

        pool = osv.osv.osv_pool()
        pool_dic[db_name] = pool

        try:
            addons.load_modules(db, force_demo, status, update_module, languages=languages)
        except Exception:
            del pool_dic[db_name]
            log.exception("Could not load modules for %s" % db_name)
            raise

        cr = db.cursor()
        try:
            pool.init_set(cr, False)
            pool.get('ir.actions.report.xml').register_all(cr)
            cr.commit()
        finally:
            cr.close()

        if pooljobs:
            pool.get('ir.cron').restart(db.dbname)
        log.info('Successfuly loaded database \"%s\"' % db_name)
    return db, pool


def restart_pool(db_name, force_demo=False, status=None, update_module=False, languages=False):
    """ Unload and reload a Database

        @return db,pool as in `get_db_and_pool()`
    """
    from netsvc import Agent
    if db_name in pool_dic:
        Agent.cancel(db_name)
        del pool_dic[db_name]
    return get_db_and_pool(db_name, force_demo, status, update_module=update_module, languages=languages)


def get_db_only(db_name):
    """SQL connect to a database and return that
    """
    # ATTENTION:
    # do not put this import outside this function
    # sql_db must not be loaded before the logger is initialized.
    # sql_db import psycopg2.tool which create a default logger if there is not.
    # this resulting of having the logs outputed twice...
    import sql_db
    db = sql_db.db_connect(db_name)
    return db


def get_db(db_name):
    """Return a Database, first part of `get_db_and_pool()`
    """
    return get_db_and_pool(db_name)[0]


def get_pool(db_name, force_demo=False, status=None, update_module=False):
    """Return the pool of objects, second part of `get_db_and_pool()`
    """
    pool = get_db_and_pool(db_name, force_demo, status, update_module)[1]
    return pool

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
