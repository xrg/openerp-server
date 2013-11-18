# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2010-2011 OpenERP s.a. (<http://openerp.com>).
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

#.apidoc title: PostgreSQL interface

"""
The PostgreSQL connector is a connectivity layer between the OpenERP code and
the database, *not* a database abstraction toolkit. Database abstraction is what
the ORM does, in fact.

See also: the `pooler` module
"""

#.apidoc add-functions: print_stats
#.apidoc add-classes: Cursor Connection ConnectionPool

__all__ = ['db_connect', 'close_db']

import logging
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT, ISOLATION_LEVEL_READ_COMMITTED, ISOLATION_LEVEL_SERIALIZABLE
from psycopg2.pool import PoolError

from psycopg2 import OperationalError
import psycopg2.extensions
import warnings
from operator import itemgetter

psycopg2.extensions.register_type(psycopg2.extensions.UNICODE)

types_mapping = {
    'date': (1082,),
    'time': (1083,),
    'datetime': (1114,),
}

def unbuffer(symb, cr):
    if symb is None: return None
    return str(symb)

def undecimalize(symb, cr):
    if symb is None: return None
    return float(symb)

psycopg2.extensions.register_type(psycopg2.extensions.new_type((1700,), 'float', undecimalize))


import tools
from tools.func import wraps, frame_codeinfo
from tools.misc import ustr
from netsvc import Agent, Server
from datetime import datetime as mdt
from datetime import timedelta
import threading
import time
from inspect import currentframe

import re

re_queries = [
        ('select', re.compile(r'select .*\s+from\s+"?([a-z_0-9]+)"?[\s,]', re.I|re.DOTALL)),
        ('select', re.compile(r'select .*\s+from\s+"?([a-z_0-9]+)"?$', re.I|re.DOTALL)),
        ('select', re.compile(r'select nextval\(\s?\'([a-z_0-9]+)_id_seq?\'\s?\)', re.I)),
        ('execute', re.compile(r'execute\s+"?([a-z_0-9]+)"?', re.I)),
        #('prepare', re.compile(r'prepare.*as\sselect.*\s+from\s+"?([a-z_0-9]+)"?\s', re.I|re.DOTALL)),
        ('prepare', re.compile(r'prepare\s+"?([a-z_0-9]+)"?[\s\(]', re.I)),
        ('update', re.compile(r'update\s+"?([a-z_0-9]+)"?\s', re.I)),
        ('delete', re.compile(r'delete from\s+"?([a-z_0-9]+)"?\s', re.I)),
        ('insert', re.compile(r'insert into\s+"?([a-z_0-9]+)"?\s*\(', re.I)),
        ('alter', re.compile(r'alter table\s+"?([a-z_0-9]+)"?\s', re.I)),
        ('comment', re.compile(r'comment on column\s+"?([a-z_0-9]+)"?\.', re.I)),
        ('comment', re.compile(r'comment on table\s+"?([a-z_0-9]+)"?\s', re.I)),
        ('create', re.compile('create (?:temp )?(?:database|table|view)\s+"?([a-z_0-9]+)"?[\s\(]', re.I)),
        ('create', re.compile('create or replace view\s+"?([a-z_0-9]+)"?[\s\(]', re.I)),
        ('create', re.compile('create (?:unique )?index .*\son\s+"?([a-z_0-9]+)"?[\s\(]', re.I)),
        ('drop', re.compile(r'drop view\s+"?([a-z_0-9]+)"?\s', re.I)),
        ('drop', re.compile(r'drop table\s+"?([a-z_0-9]+)"?', re.I)),
        ('drop db', re.compile(r'drop database "?([a-z_0-9]+)"?', re.I)),
        ('select', re.compile(r'with recursive.*?\sas\s+\(\s*select\s.*?\s+from\s+"?([a-z_0-9]+)"?', re.I|re.DOTALL)),
        ('select', re.compile(r'select (nextval)\(%s\)', re.I)),
        ('transaction', re.compile(r'(?:release )?savepoint\s+"?([a-z_0-9]+)"?', re.I)),
        ('transaction', re.compile(r'rollback to savepoint\s+"?([a-z_0-9]+)"?', re.I)),
    ]

sql_counter = 0

def print_stats(stats, logger):
    """ Print the statistics at the stats dict

        It will print sums per database TABLE, and then per
        SQL verb (like SELECT, INSERT, UPDATE etc)

        @param logger is used for output, at level `debug`
        @return None
    """
    all_sum = [0, 0]
    sum_tbl = {}
    sum_kind = {}
    for table, kind in stats:
        st = stats[(table, kind)]
        logger.debug("Operations: %s ON %s: %s/%s", kind.upper(), table,
                        st[0], timedelta(microseconds=st[1]))

        sum_tbl.setdefault(table, [0,0])
        sum_tbl[table][0] += st[0]
        sum_tbl[table][1] += st[1]
        sum_kind.setdefault(kind, [0,0])
        sum_kind[kind][0] += st[0]
        sum_kind[kind][1] += st[1]
        all_sum[0] += st[0]
        all_sum[1] += st[1]

    for table, st in sum_tbl.items():
        logger.debug("Sum of ops ON %s: %s/%s", table,
                        st[0], timedelta(microseconds=st[1]))

    for kind, st in sum_kind.items():
        logger.debug("Sum of %s ops: %s/%s", kind.upper(),
                        st[0], timedelta(microseconds=st[1]))

    logger.debug("Sum of all ops: %s/%s",
                        all_sum[0], timedelta(microseconds=all_sum[1]))

class Cursor(object):
    """ Cursor is an open transaction to Postgres, utilizing a TCP connection

        A lightweight wrapper around psycopg2's `psycopg1cursor` objects

        This is the object behind the `cr` variable used all over the OpenERP
        code.
    """
    IN_MAX = 1000 # decent limit on size of IN queries - guideline = Oracle limit
    __logger = logging.getLogger('db.cursor')
    __pgmode = None
    __slots__ = ('sql_stats_log', 'sql_log', 'sql_log_count', '__closed', \
               '__caller', '_pool', 'dbname', 'auth_proxy', '_serialized', \
               '_cnx', '_obj', '_pgmode', 'fetchone', 'fetchmany', 'fetchall')

    def check(f):
        @wraps(f)
        def wrapper(self, *args, **kwargs):
            if self.__closed:
                raise psycopg2.OperationalError('Unable to use the cursor after having closed it')
            return f(self, *args, **kwargs)
        return wrapper

    def __init__(self, pool, dbname, serialized=False, temp=False):
        self.sql_stats_log = {}
        # stats log will be a dictionary of
        # { (table, kind-of-qry): (num, delay, {queries?: num}) }

        # default log level determined at cursor creation, could be
        # overridden later for debugging purposes
        self.sql_log = self.__logger.isEnabledFor(logging.DEBUG_SQL)

        self.sql_log_count = 0
        self.__closed = True    # avoid the call of close() (by __del__) if an exception
                                # is raised by any of the following initialisations
        if self.sql_log:
            self.__caller = frame_codeinfo(currentframe(),2)
        else:
            self.__caller = False
        self._pool = pool
        self.dbname = dbname
        self.auth_proxy = None
        self._serialized = serialized
        self._cnx, self._obj = pool.borrow(dsn(dbname), True, temp=temp)
        self.__closed = False   # real initialisation value
        self.autocommit(False)
        if not hasattr(self._cnx,'_prepared'):
            self._cnx._prepared = []
        self._pgmode = self.__pgmode
        if not self._pgmode:
            if self._cnx.server_version >= 80400:
                pv = self._cnx.server_version / 100
                self._pgmode = 'pg%d%d' % ( pv/100, pv % 100)
            else:
                self._pgmode = 'pg00'

        for verb in ('fetchone', 'fetchmany', 'fetchall'):
            # map the *bound* functions, bypass @check
            setattr(self, verb, getattr(self._obj, verb))

    def __del__(self):
        if not self.__closed:
            # Oops. 'self' has not been closed explicitly.
            # The cursor will be deleted by the garbage collector,
            # but the database connection is not put back into the connection
            # pool, preventing some operation on the database like dropping it.
            # This can also lead to a server overload.
            msg = "Cursor not closed explicitly\n"
            if self.__caller:
                msg += "Cursor was created at %s:%s" % self.__caller
            else:
                msg += "Please enable sql debugging to trace the caller."
            self.__logger.warn(msg)
            self._close(True)

    def __repr__(self):
        return "<sql_db.Cursor %x %s>" %( id(self), self.__closed and 'closed' or '')

    def execute(self, query, params=None, debug=False, log_exceptions=True, _fast=False):
        """ Execute some SQL command
            @param debug   Verbosely log the query being sent (not results, yet)
            @param log_exceptions ignored, left there mainly for API compatibility with trunk
        """
        if params and not _fast:
            query = query.replace('%d','%s').replace('%f','%s')

        if self.__closed:
            self.__logger.debug("closed cursor: %r", self)
            raise psycopg2.OperationalError('Unable to use the cursor after having closed it')

        if self.sql_log or debug:
            now = mdt.now()

        # The core of query execution
        try:
            params = params or None
            res = self._obj.execute(query, params)
        except OperationalError, oe:
            self.__logger.exception("Postgres Operational error: %s", oe)
            try:
                self._cnx.status = False
            except TypeError:
                pass
            raise oe
        except psycopg2.DatabaseError, pe:
            self.__logger.error("Programming error: %s", ustr(pe))
            self.__logger.error("bad query: %s\nparams: %s", ustr(query), params)
            if debug or self.__logger.isEnabledFor(logging.DEBUG):
                import traceback
                self.__logger.debug("stack: %s", ''.join(traceback.format_stack(limit=15)))
            raise
        except Exception:
            self.__logger.exception("bad query: %s\nparams: %s", ustr(query),params)
            raise

        if self.sql_log or debug:
            delay = mdt.now() - now
            delay = delay.seconds * 1E6 + delay.microseconds

            dstr = ''
            if delay > 10000: # only show slow times
                dstr = ' (%dms)' % int(delay/1000)
            try:
                self.__logger.debug("Q%s: %s" % (dstr, self._obj.query))
            except Exception:
                # should't break because of logging
                pass
            self.sql_log_count += 1

        if self.sql_log:
            qry2 = query.strip()
            for kind, rex in re_queries:
                res_m = rex.match(qry2)
                if res_m:
                    skey = (res_m.group(1), kind)
                    self.sql_stats_log.setdefault(skey, [0, 0, {}])
                    self.sql_stats_log[skey][0] += 1
                    self.sql_stats_log[skey][1] += delay
                    break
            else:
                if len(query) < 2000: # skip sth big like base.sql
                    self.__logger.warning("Stray query: %r", query)
        return res

    def execute_safe(self, query, params=None, debug=False):
        """ Execute some SQL command, do NOT log the query params

            Used for password operations, avoids logging the sensitive params

            @param debug   Verbosely log the query being sent (not results, yet)
        """
        if self.__closed:
            self.__logger.debug("closed cursor: %r", self)
            raise psycopg2.OperationalError('Unable to use the cursor after having closed it')

        # The core of query execution
        try:
            params = params or None
            res = self._obj.execute(query, params)
        except OperationalError, oe:
            self.__logger.exception("Postgres Operational error: %s", oe)
            try:
                self._cnx.status = False
            except TypeError:
                pass
            raise oe
        except psycopg2.DatabaseError, pe:
            self.__logger.error("Programming error: %s", ustr(pe))
            self.__logger.error("bad query: %s", ustr(query))
            if debug or self.__logger.isEnabledFor(logging.DEBUG):
                import traceback
                self.__logger.debug("stack: %s", ''.join(traceback.format_stack(limit=15)))
            raise
        except Exception:
            self.__logger.exception("bad query: %s", ustr(query))
            raise

        return res

    def split_for_in_conditions(self, ids):
        """Split a list of identifiers into one or more smaller tuples
           safe for IN conditions, after uniquifying them."""
        return tools.misc.split_every(self.IN_MAX, set(ids))

    def print_log(self):
        global sql_counter
        sql_counter += self.sql_log_count
        if not self.sql_log:
            return

        self.__logger.debug("SQL Sums for current cursor:")
        print_stats(self.sql_stats_log, self.__logger)

        # Merge stats at pool stats
        for skey in self.sql_stats_log:
            if skey in self._pool.sql_stats:
                self._pool.sql_stats[skey][0] += self.sql_stats_log[skey][0]
                self._pool.sql_stats[skey][1] += self.sql_stats_log[skey][1]
            else:
                self._pool.sql_stats[skey] = self.sql_stats_log[skey]
        self.sql_stats_log = {}
        self.sql_log_count = 0
        self.sql_log = False

    def execute_prepared(self, name, query, params=None, debug=False, datatypes=None):
        """ Execute and return one query, through a prepared statement.
            The name argument is required, and should be unique accross all code.
            The query will be PREPARE'd and given this name. Then, executed.
            Subsequent calls to the same /code/ will just re-use the prepared
            statement under this name.
            datatypes, if specified, will strictly define the parameter types to
            the prepared statement.
        """
        assert ( (not datatypes) or len(datatypes) == len(params or []))
        assert (name)

        if name not in self._cnx._prepared:
            if '%d' in query or '%f' in query:
                self.__logger.warn(query)
                self.__logger.warn("SQL queries cannot contain %d or %f anymore. Use only %s")
                if params:
                    query = query.replace('%d', '%s').replace('%f', '%s')

            args = ''
            if params and len(params):
                query = query % tuple(map(lambda x: '$%d' % (x+1) , range(len(params))))
                if datatypes:
                    dtt = datatypes
                else:
                    dtt = [ 'UNKNOWN' for x in range(len(params)) ]
                args = '(' + ', '.join(dtt) + ')'

            qry = 'PREPARE ' + name + args + ' AS ' + query + ';'

            self.execute(qry, debug=debug, _fast=True)
            self._cnx._prepared.append(name)

        args = ''
        if params and len(params):
                args = [ '%s' for x in range(len(params)) ]
                args = '(' + ', '.join(args) + ')'
        return self.execute('EXECUTE ' +name + ' '+ args + ';', params, debug=debug, _fast=True)

    @check
    def close(self):
        return self._close(False)

    def _close(self, leak=False):
        if not self._obj:
            return

        self.print_log()

        if not self._serialized:
            self.rollback() # Ensure we close the current transaction.

        self._obj.close()

        # This force the cursor to be freed, and thus, available again. It is
        # important because otherwise we can overload the server very easily
        # because of a cursor shortage (because cursors are not garbage
        # collected as fast as they should). The problem is probably due in
        # part because browse records keep a reference to the cursor.
        del self._obj
        self.__closed = True

        if leak:
            self._cnx.leaked = True
        else:
            self._pool.give_back(self._cnx)

    @check
    def autocommit(self, on):
        offlevel = [ISOLATION_LEVEL_READ_COMMITTED, ISOLATION_LEVEL_SERIALIZABLE][bool(self._serialized)]
        self._cnx.set_isolation_level([offlevel, ISOLATION_LEVEL_AUTOCOMMIT][bool(on)])

    @check
    def commit(self):
        """ Perform an SQL `COMMIT`
        """
        return self._cnx.commit()

    @check
    def rollback(self):
        """ Perform an SQL `ROLLBACK`
        """
        return self._cnx.rollback()

    def __build_cols(self):
        return map(itemgetter(0), self._obj.description)

    # check
    def _dictfetchone_compat(self):
        """Fetch one row in a dict, compatible with psycopg 2.x versions
        """
        row = self._obj.fetchone()
        if row:
            return dict(zip(self.__build_cols(), row))
        else:
            return row

    # check
    def _dictfetchone_Caccel(self):
        """C-accelerated version of dictfetchone()
        """
        self._obj.row_factory = dict
        row = self._obj.fetchone()
        self._obj.row_factory = None
        return row

    # check
    def _dictfetchmany_compat(self, size):
        rows = self._obj.fetchmany(size)
        cols = self.__build_cols()
        return [ dict(zip(cols, row)) for row in rows]

    # check
    def _dictfetchmany_Caccel(self, size):
        self._obj.row_factory = dict
        rows = self._obj.fetchmany(size)
        self._obj.row_factory = None
        return rows

    # check
    def _dictfetchall_compat(self):
        """Fetch in dict, compatible with all psycopg 2.x versions
        """
        rows = self._obj.fetchall()
        cols = self.__build_cols()
        return [ dict(zip(cols, row)) for row in rows]

    def _dictfetchall_Caccel(self):
        """Fetch in dict, with C acceleration from psycopg2
        """
        self._obj.row_factory = dict
        ret = self._obj.fetchall()
        self._obj.row_factory = None
        return ret

    @check
    def __getattr__(self, name):
        if name == 'server_version':
            return self._cnx.server_version
        elif name == 'pgmode':
            return self._pgmode
        return getattr(self._obj, name)

    @classmethod
    def set_pgmode(cls, pgmode):
        """ Set the mode of postgres operations for all cursors
        """
        cls.__pgmode = pgmode
        cls.__logger.info("Postgres mode set to %s" % str(pgmode))

    @classmethod
    def get_pgmode(cls):
        """Obtain the mode of postgres operations for all cursors
        """
        cls.__logger.info("Postgres mode is %s" % str(cls.__pgmode))
        return cls.__pgmode


class PsycoConnection(psycopg2.extensions.connection):
    pass

class ConnectionPool(threading.Thread, Server):
    """ The pool of connections to database(s)

        Keep a set of connections to pg databases open, and reuse them
        to open cursors for all transactions.

        The connections are *not* automatically closed. Only a close_db()
        can trigger that.
    """
    __logger = logging.getLogger('db.connection_pool')
    EXPIRE_AFTER = 300.0 # 5min
    CLEAN_EVERY = 300.0 # 5min

    def locked(fun):
        @wraps(fun)
        def _locked(self, *args, **kwargs):
            self._lock.acquire()
            try:
                return fun(self, *args, **kwargs)
            finally:
                self._lock.release()
        return _locked


    def __init__(self, maxconn=64, pgmode=None):
        threading.Thread.__init__(self, name='ConnectionPool')
        Server.__init__(self)
        self._connections = []
        self._maxconn = max(maxconn, 1)
        self._lock = threading.Lock()
        self._debug_pool = tools.config.get_misc('debug', 'db_pool', False)
        self.sql_stats = {}
        self.daemon = True # for the thread, we can stop at any time
        if tools.config.get_misc('postgres', 'binary_cursor', False):
            # Binary cursor, experimental
            self._cursor_factory = psycopg2.extensions.cursor_bin
        else:
            # default, compatible one, using ASCII SQL expansion
            self._cursor_factory = psycopg2.extensions.cursor
        if pgmode: # not None or False
            Cursor.set_pgmode(pgmode)
        if 'dfc' in psycopg2.__version__:
            Cursor.dictfetchone = Cursor._dictfetchone_Caccel
            Cursor.dictfetcmany = Cursor._dictfetchmany_Caccel
            Cursor.dictfetchall = Cursor._dictfetchall_Caccel
        else:
            Cursor.dictfetchone = Cursor._dictfetchone_compat
            Cursor.dictfetchmany = Cursor._dictfetchmany_compat
            Cursor.dictfetchall = Cursor._dictfetchall_compat

    def __del__(self):
        # explicitly free them
        del self._connections
        if self.sql_stats:
            self.print_all_stats()

    def __repr__(self):
        used = len([1 for c, u, t in self._connections[:] if u])
        count = len(self._connections)
        return "ConnectionPool(used=%d/count=%d/max=%d)" % (used, count, self._maxconn)

    def _debug(self, msg, *args):
        if self._debug_pool:
            msg = '%r ' + msg
            self.__logger.debug(msg, self, *args)

    def _debug_dsn(self, msg, *args, **kwargs):
        """Debug function, that will decode the dsn_pos'th argument as dsn

            @param kwargs may only contain 'dsn_pos'
        """
        if not self._debug_pool:
            return

        def cleanup(dsn):
            cl = [x for x in dsn.strip().split() if x.split('=', 1)[0] != 'password']
            return ' '.join(cl)

        largs = list(args)
        dsn_pos = kwargs.pop('dsn_pos', 0)
        if kwargs:
            raise TypeError("Unknown keyword argument(s): %r" % kwargs)
        assert dsn_pos < len(largs)
        largs[dsn_pos] = cleanup(largs[dsn_pos])
        msg = '%r ' + msg
        self.__logger.debug(msg, self, *largs)

    def set_pool_debug(self, do_debug = True):
        self._debug_pool = do_debug
        self.__logger.info("Debugging set to %s" % str(do_debug))

    @locked
    def stats(self):
        return repr(self)

    @locked
    def _clear_old_ones(self):
        last_time = time.time() - self.EXPIRE_AFTER
        self._connections = [ (c, u, t) for c, u, t in self._connections if u or t > last_time]

    def run(self):
        self.running = True
        while self.running:
            try:
                time.sleep(self.CLEAN_EVERY)
                self._clear_old_ones()
            except Exception:
                self.__logger.warning("Could not clean old connections:", exc_info=True)
                time.sleep(6.0) # an arbitrary delay..

        return True

    def stop(self):
        self.running = False

    def join(self, dt=None):
        # No need to join() this thread.
        pass

    @locked
    def borrow(self, dsn, do_cursor=False, temp=False):
        self._debug_dsn('Borrow connection to %r', dsn)

        # free leaked connections
        for i, (cnx, u, t) in tools.reverse_enumerate(self._connections):
            if getattr(cnx, 'leaked', False):
                delattr(cnx, 'leaked')
                self._connections.pop(i)
                self._connections.append((cnx, False, time.time()))
                self._debug_dsn('Free leaked connection to %r', cnx.dsn)

        result = None
        for i, (cnx, used, t) in enumerate(self._connections):
            if not used and dsn_are_equals(cnx.dsn, dsn):
                self._connections.pop(i)
                try:
                   if psycopg2.__version__ >= '2.2' :
                        pr = cnx.poll()
                        self._debug("Poll: %d", pr)
                except OperationalError, e:
                    self._debug("Error in poll: %s" % e)
                    continue

                if cnx.closed or not cnx.status:
                    # something is wrong with that connection, let it out
                    self._debug("Troubled connection ")
                    continue

                self._debug('Existing connection found at index %d', i)
                # Note, we ignore the 'temp' flag here, this connection will
                # return to the pool anyway
                if do_cursor:
                    try:
                        cur = cnx.cursor(cursor_factory=self._cursor_factory)
                        if psycopg2.__version__ < '2.2' and not cur.isready():
                            continue
                        if cur.closed:
                            continue
                        self._connections.insert(i,(cnx, True, 0.0))

                        result = (cnx, cur)
                    except OperationalError:
                        continue
                else:
                    self._connections.insert(i,(cnx, True, 0.0))
                    result = cnx
                break
        if result:
            return result

        if len(self._connections) >= self._maxconn:
            # try to remove the oldest connection not used
            for i, (cnx, used, t) in enumerate(self._connections):
                if not used:
                    self._connections.pop(i)
                    self._debug_dsn('Removing old connection at index %d: %r', i, cnx.dsn, dsn_pos=1)
                    break
            else:
                # note: this code is called only if the for loop has completed (no break)
                raise PoolError('The Connection Pool Is Full')

        try:
            result = psycopg2.connect(dsn=dsn, connection_factory=PsycoConnection)
        except psycopg2.Error, e:
            self.__logger.exception('Connection to the database failed')
            raise
        if not temp:
            result.is_temp = False
            self._connections.append((result, True, 0.0))
        else:
            result.is_temp = True
        self._debug('Create new connection')
        if do_cursor:
            cur = result.cursor(cursor_factory=self._cursor_factory)
            return (result, cur)
        return result

    @locked
    def give_back(self, connection, keep_in_pool=True):
        self._debug_dsn('Give back connection to %r', connection.dsn)
        for i, (cnx, used, t) in enumerate(self._connections):
            if cnx is connection:
                self._connections.pop(i)
                if keep_in_pool and not (cnx.closed or cnx.is_temp or not cnx.status):
                    self._connections.insert(i,(cnx, False, time.time()))
                    self._debug_dsn('Put connection to %r back in pool', cnx.dsn)
                else:
                    self._debug_dsn('Forgot connection to %r', cnx.dsn)
                break
        else:
            if not connection.is_temp:
                raise PoolError('This connection does not below to the pool')

    @locked
    def close_all(self, dsn):
        self._debug_dsn('Close all connections to %r', dsn)
        for i, (cnx, used, t) in tools.reverse_enumerate(self._connections):
            if dsn_are_equals(cnx.dsn, dsn):
                cnx.close()
                self._connections.pop(i)

    def print_all_stats(self):
        logger = logging.getLogger('db.cursor') # shall be the same..
        logger.debug("Statistics for the sql pool:")
        print_stats(self.sql_stats, logger)

class Connection(object):
    """ A lightweight instance of a connection to postgres
    """
    __logger = logging.getLogger('db.connection')
    __slots__ = ('dbname', '_pool', '_temp')

    def __init__(self, pool, dbname, temp=False):
        self.dbname = dbname
        self._pool = pool
        self._temp = temp

    def cursor(self, serialized=False):
        cursor_type = serialized and 'serialized ' or ''
        self.__logger.log(logging.DEBUG_SQL, 'create %scursor to %r', cursor_type, self.dbname)
        return Cursor(self._pool, self.dbname, serialized=serialized, temp=self._temp)

    def serialized_cursor(self):
        return self.cursor(True)

    def __nonzero__(self):
        """Check if connection is possible"""
        try:
            warnings.warn("You use an expensive function to test a connection.",
                      DeprecationWarning, stacklevel=1)
            cr = self.cursor()
            cr.close()
            return True
        except Exception:
            return False


_dsn = ''
for p in ('host', 'port', 'user', 'password'):
    cfg = tools.config['db_' + p]
    if cfg:
        _dsn += '%s=%s ' % (p, cfg)

def dsn(db_name):
    return '%sdbname=%s' % (_dsn, db_name)

def dsn_are_equals(first, second):
    if first == second:
        return True
    def key(dsn):
        k = dict(x.split('=', 1) for x in dsn.strip().split())
        k.pop('password', None) # password is not relevant
        return k
    return key(first) == key(second)


_Pool = ConnectionPool(int(tools.config['db_maxconn']),
                tools.config.get_misc('postgres','mode', False))

def db_connect(db_name, temp=False):
    """ Return a connection to that database

        @param temp means this connection will not enter the pool,
            once released
    """
    return Connection(_Pool, db_name, temp=temp)

def get_template_dbnames():
    """ List of special databases, which we should ignore
    """
    temp_dbs = filter(bool, tools.config.get_misc('databases', 'template','').split(' '))
    return ('template1', 'template0', 'postgres') + tuple(temp_dbs)

def close_db(db_name):
    _Pool.close_all(dsn(db_name))
    Agent.cancel(db_name)
    tools.cache.clean_caches_for_db(db_name)


# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
