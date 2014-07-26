# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP-F3, Open Source Management Solution
#    Copyright (C) 2011-2014 P. Christeas <xrg@hellug.gr>
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>). All Rights Reserved
#    The refactoring about the OpenSSL support come from Tryton
#    Copyright (C) 2007-2009 Cédric Krier.
#    Copyright (C) 2007-2009 Bertrand Chenal.
#    Copyright (C) 2008 B2CK SPRL.
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

import errno
import logging
import logging.handlers
import os
import socket
import sys
import threading
import time
import release
from pprint import pformat
import warnings
import types
import heapq

#.apidoc title: Common Services: netsvc
#.apidoc module-mods: member-order: bysource

try:
    from inspect import currentframe
except ImportError:
    def currentframe(): return None

class Service(object):
    """ Base class for *Local* services

        Functionality here is trusted, no authentication.
    """
    _services = {}
    def __init__(self, name, audience=''):
        Service._services[name] = self
        self.__name = name
        self._methods = {}

    def joinGroup(self, name):
        raise Exception("No group for local services")
        #GROUPS.setdefault(name, {})[self.__name] = self

    @classmethod
    def exists(cls, name):
        return name in cls._services

    @classmethod
    def remove(cls, name):
        if cls.exists(name):
            cls._services.pop(name)

    def exportMethod(self, method):
        if callable(method):
            self._methods[method.__name__] = method

    def abortResponse(self, error, description, origin, details, do_traceback=True):
        if not tools.config['debug_mode']:
            if do_traceback:
                tb = sys.exc_info()
                tb_s = "".join(traceback.format_exception(*tb))
            else:
                tb_s = None
            raise OpenERPDispatcherException(description, origin=origin, 
                    details=details, faultCode=error, traceback=tb_s)
        else:
            raise

class LocalService(object):
    """ Proxy for local services. 
    
        Any instance of this class will behave like the single instance
        of Service(name)
    """
    __logger = logging.getLogger('service')
    def __init__(self, name):
        self.__name = name
        try:
            self._service = Service._services[name]
            for method_name, method_definition in self._service._methods.items():
                setattr(self, method_name, method_definition)
        except KeyError, keyError:
            self.__logger.error('This service does not exist: %s' % (str(keyError),) )
            raise

    def __call__(self, method, *params):
        return getattr(self, method)(*params)

class ExportService(object):
    """ Proxy for exported services. 

    All methods here should take an AuthProxy as their first parameter. It
    will be appended by the calling framework.

    Note that this class has no direct proxy, capable of calling 
    eservice.method(). Rather, the proxy should call 
    dispatch(method,auth,params)
    """
    
    _services = {}
    _groups = {}
    _logger = logging.getLogger('web-services')
    
    def __init__(self, name, audience=''):
        ExportService._services[name] = self
        self.__name = name
        self._logger.debug("Registered an exported service: %s" % name)

    def joinGroup(self, name):
        ExportService._groups.setdefault(name, {})[self.__name] = self

    @classmethod
    def getService(cls,name):
        return cls._services[name]

    def dispatch(self, method, auth, params):
        raise Exception("stub dispatch at %s" % self.__name)
        
    def new_dispatch(self,method,auth,params):
        raise NotImplementedError("stub dispatch at %s" % self.__name)

    def abortResponse(self, error, description, origin, details, do_traceback=True):
        if not tools.config['debug_mode']:
            if do_traceback:
                tb = sys.exc_info()
                tb_s = "".join(traceback.format_exception(*tb))
            else:
                tb_s = None
            raise OpenERPDispatcherException(description, origin=origin, 
                    details=details, faultCode=error, traceback=tb_s)
        else:
            raise

    def stats(self, _pre_msg='No statistics'):
        """ This function should return statistics about the service.

            @param _pre_msg helps when a child class wants to just display
                a simple message
        """
        return "%s (%s.%s): %s" % (self.__name, 
                    self.__class__.__module__, self.__class__.__name__,
                    _pre_msg)

    @classmethod
    def allStats(cls):
        """ Return a newline-delimited string of all services stats
        
            Remember that the purpose of this is to inspect what the
            server is doing at each moment.
        """
        res = []
        for srv in cls._services.values():
            st = srv.stats()
            if not st:
                continue
            res.append(st)
        return '\n'.join(res)

LOG_NOTSET = 'notset'
LOG_DEBUG_SQL = 'debug_sql'
LOG_DEBUG_RPC = 'debug_rpc'
LOG_DEBUG = 'debug'
LOG_TEST = 'test'
LOG_INFO = 'info'
LOG_WARNING = 'warn'
LOG_ERROR = 'error'
LOG_CRITICAL = 'critical'

logging.DEBUG_RPC = logging.DEBUG - 2
logging.addLevelName(logging.DEBUG_RPC, 'DEBUG_RPC')
logging.DEBUG_SQL = logging.DEBUG_RPC - 2
logging.addLevelName(logging.DEBUG_SQL, 'DEBUG_SQL')

logging.TEST = logging.INFO - 5
logging.addLevelName(logging.TEST, 'TEST')

BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE, _NOTHING, DEFAULT = range(10)
#The background is set with 40 plus the number of the color, and the foreground with 30
#These are the sequences need to get colored ouput
RESET_SEQ = "\033[0m"
COLOR_SEQ = "\033[1;%dm"
BOLD_SEQ = "\033[1m"
COLOR_PATTERN = "%s%s%%s%s" % (COLOR_SEQ, COLOR_SEQ, RESET_SEQ)
LEVEL_COLOR_MAPPING = {
    logging.DEBUG_SQL: (WHITE, MAGENTA),
    logging.DEBUG_RPC: (BLUE, WHITE),
    logging.DEBUG: (BLUE, DEFAULT),
    logging.INFO: (GREEN, DEFAULT),
    logging.TEST: (WHITE, BLUE),
    logging.WARNING: (YELLOW, DEFAULT),
    logging.ERROR: (RED, DEFAULT),
    logging.CRITICAL: (WHITE, RED),
}

class DBFormatter(logging.Formatter):
    def format(self, record):
        if getattr(record, 'dbname', False):
            record.at_dbname = '@%s' % record.dbname
        else:
            record.at_dbname = ''
        return logging.Formatter.format(self, record)

    def formatException(self, ei):
        """ A little better formatter of exceptions, that will tolerate
            locale-encoded strings (or utf8)
        """
        from locale import getpreferredencoding
        res = logging.Formatter.formatException(self, ei)
        if isinstance(res, unicode):
            return res

        for enc in (getpreferredencoding(), 'utf-8'):
            try:
                return unicode(res, 'utf-8')
            except UnicodeEncodeError:
                pass
        return res

class ColoredFormatter(DBFormatter):
    def format(self, record):
        fg_color, bg_color = LEVEL_COLOR_MAPPING[record.levelno]
        record.levelname = COLOR_PATTERN % (30 + fg_color, 40 + bg_color, record.levelname)
        return DBFormatter.format(self, record)

class Logger_compat(logging.Logger):
    """ Backwards compatible logger. Works also for non-ported code
    
        In case we want to run the new-style server with old-style addons,
        we might encounter "self.logger.notifyChannel(...)" calls, on a
        ported logger. Then, use this class to workaround, until modules
        are fixed.
    """
    def notifyChannel(self, name, level, msg):
        warnings.warn("You tried to call notifyChannel on a logger object.",
                      DeprecationWarning, stacklevel=2)
        Logger().notifyChannel(name, level, msg)

class Logger_db(logging.Logger):
    """ Replacement functions for logging.Logger
    
        copied from pythonic 'logging' module
    """
    def findCaller(self):
        """
        Find the stack frame of the caller so that we can note the source
        file name, line number and function name.
        """
        f = currentframe()
        #On some versions of IronPython, currentframe() returns None if
        #IronPython isn't run with -X:Frames.
        if f is not None:
            f = f.f_back.f_back
        rv = "(unknown file)", 0, "(unknown function)", None
        while hasattr(f, "f_code"):
            co = f.f_code
            filename = os.path.normcase(co.co_filename)
            if filename == logging._srcfile:
                f = f.f_back
                continue
            dbname = None
            recu = 0
            df = f
            while (not dbname) and df and (recu < 8):
                # Go up to 8 frames up, searching for local variables
                # that could tell us the db name
                dbname = df.f_locals.get('dbname', None)
                if dbname is None:
                    dbname = df.f_locals.get('db_name', None)
                if dbname is None:
                    cr = f.f_locals.get('cr')
                    if cr:
                        dbname = getattr(cr, 'dbname', None)
                recu += 1
                df = df.f_back

            rv = (filename, f.f_lineno, co.co_name, dbname)
            break
        return rv

    def _log(self, level, msg, args, exc_info=None, extra=None):
        """
        Low-level logging routine which creates a LogRecord and then calls
        all the handlers of this logger to handle the record.
        """
        if logging._srcfile:
            #IronPython doesn't track Python frames, so findCaller throws an
            #exception. We trap it here so that IronPython can use logging.
            try:
                fn, lno, func, dbname = self.findCaller()
            except ValueError:
                fn, lno, func, dbname = "(unknown file)", 0, "(unknown function)"
        else:
            fn, lno, func, dbname = "(unknown file)", 0, "(unknown function)"
        if exc_info:
            if type(exc_info) != types.TupleType:
                exc_info = sys.exc_info()
        if dbname:
            if extra:
                extra = extra.copy()
            else:
                extra = {}
            extra['dbname'] = dbname
        record = self.makeRecord(self.name, level, fn, lno, msg, args, exc_info, func, extra)
        self.handle(record)


def init_logger():
    from tools.translate import resetlocale
    resetlocale()

    if tools.config.get_misc('debug', 'compat_logger', False):
        logging.setLoggerClass(Logger_compat)
    elif tools.config.get_misc('debug', 'log_dbname', False):
        logging.setLoggerClass(Logger_db)

    logger = logging.getLogger()
    # create a format for log messages and dates
    format = '[%(asctime)s] %(levelname)s:%(name)s%(at_dbname)s:%(message)s'

    if tools.config['syslog']:
        # SysLog Handler
        if os.name == 'nt':
            handler = logging.handlers.NTEventLogHandler("%s %s" % (release.description, release.version))
        else:
            handler = logging.handlers.SysLogHandler('/dev/log')
        format = '%s %s' % (release.description, release.version) \
               + ':%(levelname)s:%(name)s:%(message)s'

    elif tools.config['logfile']:
        # LogFile Handler
        logf = tools.config['logfile']
        try:
            dirname = os.path.dirname(logf)
            if dirname and not os.path.isdir(dirname):
                os.makedirs(dirname)
            if tools.config['logrotate'] is not False:
                handler = logging.handlers.TimedRotatingFileHandler(logf,'D',1,30)
            elif os.name == 'posix':
                handler = logging.handlers.WatchedFileHandler(logf)
            else:
                handler = logging.handlers.FileHandler(logf)
        except Exception:
            sys.stderr.write("ERROR: couldn't create the logfile directory. Logging to the standard output.\n")
            handler = logging.StreamHandler(sys.stdout)
    else:
        # Normal Handler on standard output
        handler = logging.StreamHandler(sys.stdout)

    if isinstance(handler, logging.StreamHandler) and os.isatty(handler.stream.fileno()):
        formatter = ColoredFormatter(format, '%Y-%m-%d %H:%M:%S')
    else:
        formatter = DBFormatter(format, '%Y-%m-%d %H:%M:%S')
    handler.setFormatter(formatter)

    # add the handler to the root logger
    logger.addHandler(handler)
    logger.setLevel(int(tools.config['log_level'] or '0'))

    if tools.config.get_misc('debug', 'warnings', False):
        logging.captureWarnings(True)

class Logger(object):
    def __init__(self):
        warnings.warn("The netsvc.Logger API shouldn't be used anymore, please "
                      "use the standard `logging.getLogger` API instead",
                      PendingDeprecationWarning, stacklevel=2)
        super(Logger, self).__init__()

    def notifyChannel(self, name, level, msg):
        warnings.warn("notifyChannel API shouldn't be used anymore, please use "
                      "the standard `logging` module instead",
                      PendingDeprecationWarning, stacklevel=2)
        from service.web_services import common
        from tools.misc import ustr

        log = logging.getLogger(ustr(name))

        if level in [LOG_DEBUG_RPC, LOG_TEST] and not hasattr(log, level):
            fct = lambda msg, *args, **kwargs: log.log(getattr(logging, level.upper()), msg, *args, **kwargs)
            setattr(log, level, fct)


        level_method = getattr(log, level)

        if isinstance(msg, Exception):
            msg = tools.exception_to_unicode(msg)

        try:
            msg = tools.ustr(msg).strip()
            if level in (LOG_ERROR, LOG_CRITICAL) and tools.config.get_misc('debug','env_info',False):
                msg = common().exp_get_server_environment() + "\n" + msg

            result = msg.split('\n')
        except UnicodeDecodeError:
            result = msg.strip().split('\n')
        try:
            if len(result)>1:
                for idx, s in enumerate(result):
                    level_method('[%02d]: %s' % (idx+1, s,))
            elif result:
                level_method(result[0])
        except IOError:
            # TODO: perhaps reset the logger streams?
            #if logrotate closes our files, we end up here..
            pass
        except Exception:
            # better ignore the exception and carry on..
            pass

    def __set_loglevel(self, level, logger=None):
        """Set the level of some logger.
        If the logger doesn't exist, create it (better, because this
        call may be earlier than the first message to the log).
        """
        cre = ''
        if logger is not None:
            if logger not in logging.root.manager.loggerDict:
                cre = ' (created)'
            log = logging.getLogger(str(logger))
        else:
            log = logging.getLogger()

        try:
            level = int(level)
        except ValueError:
            lv = logging._levelNames.get(level.upper(),False)
            if lv and isinstance(lv, int):
                level = lv
            else:
                raise ValueError("No logging level %s" % level)
        log.setLevel(logging.INFO) # make sure next msg is printed
        log.info("Log level changed to %s%s" % (logging.getLevelName(level), cre))
        log.setLevel(level)

    def set_loglevel(self, level, logger=None):
        if isinstance(level, dict):
            for log, lev in level:
                self.__set_loglevel(lev, log or None)
        else:
            return self.__set_loglevel(level, logger)

    def set_logger_level(self, logger, level):
        """ Set the logging level for a particular logger
        """
        return self.__set_loglevel(level, logger)

    def get_loglevel(self,logger=None):
        """Get the logging level of some logger.
        If that logger just inherits it's parent level, return False
        """
        if logger == '*':
            # Special case! we can query all loggers in detail..
            ret = {}
            for log in logging.root.manager.loggerDict.values():
                if not isinstance(log, logging.Logger):
                    continue
                ret[log.name] = log.level or False
            return ret
        elif logger is not None:
            if logger not in logging.root.manager.loggerDict:
                raise KeyError("No logger %s" % logger)
            log = logging.getLogger(str(logger))
        else:
            log = logging.getLogger()
        
        return log.level or False

import tools
if getattr(tools, 'config', {}).get('log_level', None) is not None:
    init_logger()

class Agent(object):
    """ Singleton that keeps track of cancellable tasks to run at a given
        timestamp.
       
        The tasks are characterised by:
       
            * a timestamp
            * the database on which the task run
            * the function to call
            * the arguments and keyword arguments to pass to the function

        Implementation details:
        
          - Tasks are stored as list, allowing the cancellation by setting
            the timestamp to 0.
          - A heapq is used to store tasks, so we don't need to sort
            tasks ourself.
    """
    __tasks = []
    __tasks_by_db = {}
    _logger = logging.getLogger('netsvc.agent')
    _lock = threading.Condition()
    _alive = True

    class pretty_repr(object):
        """ The representation of a function

            short-lived object, that will hold data until its `str()` is called
            to format it
        """

        def __init__(self, func, args, kwargs, trunc=None):
            self._func = func
            self._args = args
            self._kwargs = kwargs
            self._trunc = trunc

        def __str__(self):
            if hasattr(self._func, 'im_class'):
                return "%s.%s(%s)" % (self._func.im_class.__name__, self._func.func_name,
                        self.pretty_args(self._args, self._kwargs, self._trunc))
            else:
                return "%s(%s)" % (self._func.func_name,
                        self.pretty_args(self._args, self._kwargs, self._trunc))

        @staticmethod
        def pretty_args(args, kwargs, trunc=None):
            """ Format the name and arguments like we would write them at python
                Truncate at {trunc} chars
            """
            oout = []
            olen = 0
            if args:
                for arg in args:
                    try:
                        ostr = repr(arg)
                    except Exception:
                        ostr = '<???>'
                    if trunc and (olen >= trunc):
                        break
                    oout.append(ostr)
                    olen += len(ostr) + 2

            if kwargs:
                for kw, val in kwargs.items():
                    if trunc and (olen >= trunc):
                        break
                    try:
                        ostr = "%s=%r" %(kw, val)
                    except Exception:
                        ostr = "%s=??" % kw
                    oout.append(ostr)
                    olen += len(ostr) + 2

            if trunc and (olen >= trunc):
                oout += '...'

            return ', '.join(oout)

    @classmethod
    def setAlarm(cls, function, timestamp, db_name, *args, **kwargs):
        cls._lock.acquire()
        task = [timestamp, db_name, function, args, kwargs]
        heapq.heappush(cls.__tasks, task)
        cls.__tasks_by_db.setdefault(db_name, []).append(task)
        cls._lock.notify_all()
        cls._lock.release()

    @classmethod
    def _setAlarmNow(cls, function, timestamp, dbname, args, kwargs):
        """ companion to `_alarm_later.on_commit()` , set an alarm, even cumulative
        """
        cls._lock.acquire()
        found_task = False
        cumulative = getattr(function, '_cumulative_on', None)
        if cumulative is not None:
            for t in cls.__tasks_by_db.get(dbname, []):
                # Check if there is already some task for function() pending,
                # with same arguments and kwargs (apart from the "cumulative" one)
                if t[0] <= timestamp and t[2] == function \
                        and cls._args_match(cumulative, t[3], t[4], args, kwargs):
                    if cumulative is True:
                        found_task = True
                    elif isinstance(cumulative, int) \
                            and isinstance(t[3][cumulative], (list, set)) \
                            and isinstance(args[cumulative], (list, set)):
                        t[3][cumulative] += args[cumulative]
                        found_task = True
                    elif isinstance(cumulative, basestring) \
                            and isinstance(t[4][cumulative], (list, set)) \
                            and isinstance(kwargs[cumulative], (list, set)):
                        t[4][cumulative] += kwargs[cumulative]
                        found_task = True
                    else:
                        # We cannot use that kind of cumulative argument
                        continue
                    break
        if not found_task:
            task = [timestamp, dbname, function, args, kwargs]
            heapq.heappush(cls.__tasks, task)
            cls.__tasks_by_db.setdefault(dbname, []).append(task)
        cls._lock.notify_all()
        cls._lock.release()

    @classmethod
    def _args_match(cls, cumulative, args1, kwargs1, args2, kwargs2):
        """ Check that (*args1, **kwargs1) == (*args2, **kwargs2) , but ommit "cumulative" arg.

            If cumulative is an integer, it means it is position in "args",
            or if it is a string, it is keyword
        """
        if (len(args1) != len(args2)) or (len(kwargs1) != len(kwargs2)):
            return False

        if cumulative is True:
            if args1 == args2 and kwargs1 == kwargs2:
                return True
            else:
                return False
        elif isinstance(cumulative, int):
            if kwargs1 != kwargs2:
                return False

            for n, val in enumerate(args1):
                if n == cumulative:
                    continue
                if val != args2[cumulative]:
                    return False
            return True
        elif isinstance(cumulative, basestring):
            if args1 != args2:
                return False
            for k, val in kwargs1.items():
                if k == cumulative:
                    continue
                if val != kwargs2[k]:
                    return False
            return True
        else:
            return False

    class _alarm_later(object):
        def __init__(self, dbname, function, timestamp, args, kwargs):
            self.dbname = dbname
            self.function = function
            self.timestamp = timestamp
            self.args = args
            self.kwargs = kwargs

        def on_commit(self):
            Agent._setAlarmNow(self.function, self.timestamp, self.dbname, self.args, self.kwargs)

        def on_rollback(self):
            pass

    @classmethod
    def setAlarmLater(cls, function, timestamp, cr, *args, **kwargs):
        cr.post_commit(cls._alarm_later(cr.dbname, function, timestamp, args, kwargs))

    @classmethod
    def cancel(cls, db_name):
        """Cancel all tasks for a given database. If None is passed, all tasks are cancelled"""
        cls._logger.debug("Cancel timers for %s db", db_name or 'all')
        cls._lock.acquire()
        try:
            if db_name is None:
                cls.__tasks, cls.__tasks_by_db = [], {}
            else:
                if db_name in cls.__tasks_by_db:
                    for task in cls.__tasks_by_db[db_name]:
                        task[0] = 0
        finally:
            cls._lock.notify_all()
            cls._lock.release()

    @classmethod
    def quit(cls):
        cls._alive = False
        cls.cancel(None)

    @classmethod
    def runner(cls):
        """Neverending function (intended to be ran in a dedicated thread) that
           checks every 60 seconds tasks to run. TODO: make configurable
        """

        while cls._alive:
            cls._lock.acquire()
            while cls.__tasks and cls.__tasks[0][0] < time.time():
                task = heapq.heappop(cls.__tasks)
                timestamp, dbname, function, args, kwargs = task
                # dbname will be picked by the logger's stack inspection
                cls.__tasks_by_db[dbname].remove(task)

                if not timestamp:
                    # null timestamp -> cancelled task
                    continue
                cls._lock.release()
                cls._logger.debug("Run %s", cls.pretty_repr(function, args, kwargs, 120))
                thr = threading.Thread(target=function, args=args, kwargs=kwargs)
                thr.setDaemon(True)
                thr.start()
                time.sleep(1)
                thr = None
                cls._lock.acquire()

            # This line must have the lock in acquired state
            wtime = 600.0
            if cls.__tasks:
                wtime = cls.__tasks[0][0] - time.time()
                if wtime < 1.0:
                    wtime = 1.0
                elif wtime > 600.0:
                    wtime = 600.0
            cls._logger.debug("sleeping for %.3f seconds", wtime)
            cls._lock.wait(wtime)
            cls._lock.release()
        cls._logger.debug("thread ended")

agent_runner = threading.Thread(target=Agent.runner, name="netsvc.Agent.runner")
# the agent runner is a typical daemon thread, that will never quit and must be
# terminated when the main process exits - with no consequence
agent_runner.setDaemon(True)
agent_runner.start()

import traceback

class Server:
    """ Generic interface for all servers with an event loop etc.
        Override this to impement http, net-rpc etc. servers.

        Servers here must have threaded behaviour. start() must not block,
        there is no run().
    """
    __is_started = False
    __servers = []
    __starter_threads = []

    # we don't want blocking server calls (think select()) to
    # wait forever and possibly prevent exiting the process,
    # but instead we want a form of polling/busy_wait pattern, where
    # _server_timeout should be used as the default timeout for
    # all I/O blocking operations
    _busywait_timeout = 2.0

    __logger = logging.getLogger('server')

    def __init__(self):
        Server.__servers.append(self)
        if Server.__is_started:
            # raise Exception('All instances of servers must be inited before the startAll()')
            # Since the startAll() won't be called again, allow this server to
            # init and then start it after 1sec (hopefully). Register that
            # timer thread in a list, so that we can abort the start if quitAll
            # is called in the meantime
            t = threading.Timer(1.0, self._late_start)
            t.name = 'Late start timer for %s' % str(self.__class__)
            Server.__starter_threads.append(t)
            t.start()

    def start(self):
        self.__logger.debug("called stub Server.start")
        
    def _late_start(self):
        self.start()
        for thr in Server.__starter_threads:
            if thr.finished.is_set():
                Server.__starter_threads.remove(thr)

    def stop(self):
        self.__logger.debug("called stub Server.stop")

    def join(self, timeout=None):
        raise RuntimeError("Server.join() method called! Should have been the Thread one.")

    def is_alive(self):
        # shouldn't ever reach here, either
        return False

    def stats(self):
        """ This function should return statistics about the server """
        return "%s: No statistics" % str(self.__class__)

    @classmethod
    def startAll(cls):
        if cls.__is_started:
            return
        cls.__logger.info("Starting %d services" % len(cls.__servers))
        for srv in cls.__servers:
            srv.start()
        cls.__is_started = True

    @classmethod
    def quitAll(cls, deta=10.0):
        """ Try to gently stop all services.
        
        @param deta Float seconds we are allowed to wait for services to end
                If not specified, defaults to 10sec.
        """
        if not cls.__is_started:
            return
        cls.__logger.info("Stopping %d services" % len(cls.__servers))
        tnow = time.time()
        for thr in cls.__starter_threads:
            if not thr.finished.is_set():
                thr.cancel()
            cls.__starter_threads.remove(thr)

        for srv in cls.__servers:
            srv.stop()
        
        tend = tnow + (deta or 10.0)
        # now, check again that servers have stopped:
        num_s = 0
        for srv in cls.__servers:
            if srv.is_alive():
                num_s += 1
        cls.__logger.debug('Have to wait for %d services, %d threads', num_s, 
                threading.activeCount()-1)
        
        try:
            for srv in cls.__servers:
                # the first thread will take up all of the time available, the
                # next ones will hardly have any time. This is still OK, because
                # we have already called all threads to stop and we expect that
                # all will be doing so in the meanwhile.
                dt = tend - time.time()
                if dt <= 0.0: dt = 0.01
                if srv.is_alive():
                    srv.join(dt)

            # also, wait for stray threads
            for thr in threading.enumerate():
                if thr == threading.currentThread():
                    continue
                dt = tend - time.time()
                if dt <= 0.0: dt = 0.01
                if thr.is_alive():
                    thr.join(dt)

        except KeyboardInterrupt:
            # we can catch that now, because we are not going to delay any
            # more after this loop.
            cls.__logger.info('Join interrupted by second signal, immediate shutdown!')
            pass

        num_s = 0
        for srv in cls.__servers:
            if srv.is_alive():
                cls.__logger.debug('Server has not stopped: %s', repr(srv))
                num_s += 1

        for thr in threading.enumerate():
            if thr == threading.currentThread():
                continue
            cls.__logger.debug('Thread is still alive: %s', repr(thr))

        cls.__logger.debug('After join: %d services remaining, %d threads', num_s, 
                threading.activeCount()-1 )

        cls.__is_started = False
        return num_s or (threading.activeCount()-1)

    @classmethod
    def allStats(cls):
        res = ["Servers %s" % ('stopped', 'started')[cls.__is_started]]
        res.extend(srv.stats() for srv in cls.__servers)
        return '\n'.join(res)

    def _close_socket(self):
        # FIXME: this code may be in the wrong place, it should only
        # apply to socket servers, not this class.
        if not hasattr(self, 'socket'):
            return
        if not isinstance(self.socket, socket.socket):
            return
        if os.name != 'nt':
            try:
                self.socket.shutdown(getattr(socket, 'SHUT_RDWR', 2))
            except socket.error, e:
                if e.errno != errno.ENOTCONN: raise
                # OSX, socket shutdowns both sides if any side closes it
                # causing an error 57 'Socket is not connected' on shutdown
                # of the other side (or something), see
                # http://bugs.python.org/issue4397
                self.__logger.debug(
                    '"%s" when shutting down server socket, '
                    'this is normal under OS X', e)
        self.socket.close()

class OpenERPDispatcherException(Exception):
    def __init__(self, description, origin='exception', details='', traceback=None, faultCode=1):
        """ Dispatcher exception, data should be transferred accross xml-rpc
        @param description the main text of the exception
        @param origin  a keyword, like 'exception', 'warning' etc.
        @param details A more detailed string
        @param traceback pythonic traceback
        @param faultCode xml-rpc style fault code
        """
        self.args = (description, details, origin)
        self.traceback = traceback
        self.faultCode = faultCode
        assert isinstance(faultCode, int) or \
            (isinstance(faultCode, basestring) and faultCode.isdigit()), faultCode

    def get_faultCode(self):
        return int(self.faultCode) or 1
   
    def get_faultString(self):
        """Get the fault string in xml-rpc2 format
        """
        def pretty_string(src, newlines='\n\t'):
            usrc = tools.ustr(src)
            return newlines.join(usrc.split('\n'))

        ret = "X-Exception: %s" % (pretty_string(self.args[0], newlines=' '),)
        ret += '\nX-ExcOrigin: %s' % self.args[2]
        if self.args[1]:
            ret += '\nX-ExcDetails: %s' % (pretty_string(self.args[1]),)
        if self.traceback:
            ret += "\nX-Traceback: %s" % (pretty_string(self.traceback),)
        return ret

    def compat_string(self):
        """Get the string that v5, xml-rpc1 exceptions would need
        """
        if self.args[2] == 'exception':
            ret = tools.ustr(self.args[0])
            if self.args[1]:
                ret += '\n' + tools.ustr(self.args[1])
            return ret
        else:
            return "%s -- %s\n\n%s" % (self.args[2], tools.ustr(self.args[0]), 
                    tools.ustr(self.args[1]))

def replace_request_password(args):
    # password is always 3rd argument in a request, we replace it in RPC logs
    # so it's easier to forward logs for diagnostics/debugging purposes...
    args = list(args)
    if len(args) > 2:
        args[2] = '*'
    return args

class OpenERPDispatcher:
    def log(self, title, msg, is_passwd=False):
        logger = logging.getLogger(title)
        if logger.isEnabledFor(logging.DEBUG_RPC):
            if is_passwd:
                msg = replace_request_password(msg)
            for line in pformat(msg).split('\n'):
                logger.log(logging.DEBUG_RPC, line)

    def dispatch(self, service_name, method, params):
        try:
            self.log('service', service_name)
            self.log('method', method)
            self.log('params', params, is_passwd=True)
            auth = getattr(self, 'auth_proxy', None)
            result = ExportService.getService(service_name).dispatch(method, auth, params)
            self.log('result', result)
            # We shouldn't marshall None,
            if result == None:
                result = False
            return result
        except OpenERPDispatcherException:
            raise
        except Exception, e:
            self.log('exception', tools.exception_to_unicode(e))
            tb = getattr(e, 'traceback', sys.exc_info())
            tb_s = "".join(traceback.format_exception(*tb))
            if tools.config['debug_mode']:
                import pdb
                pdb.post_mortem(tb[2])
            # TODO meaningful FaultCodes ..
            details = ''
            if len(e.args) > 1:
                details = e.args[1]
            raise OpenERPDispatcherException(e.args[0], details=details, traceback=tb_s)

class OpenERPDispatcher2:
    _logger = logging.getLogger('rpc')
    
    @classmethod
    def _dbg_log(cls, title, params):
        cls._logger.log(logging.DEBUG_RPC,'%s: %s', title, pformat(params))

    @classmethod
    def _fake_log(cls, title, params):
        pass

    def dispatch(self, service_name, method, params):
        """ send method+params to the web_services layer
        """
        try:
            if self._logger.isEnabledFor(logging.DEBUG_RPC):
                log = self._dbg_log
            else:
                log = self._fake_log
            log('service', service_name)
            log('method', method)
            log('params', params)
            auth = getattr(self, 'auth_proxy', None)
            if not auth:
                self._logger.debug("No Authentication for: %s %s", service_name, method)
            result = ExportService.getService(service_name).new_dispatch(method, auth, params)
            log('result', result)
            # We shouldn't marshall None,
            if result == None:
                result = False
            return result
        except OpenERPDispatcherException:
            raise
        except Exception, e:
            log('exception', tools.exception_to_unicode(e))
            tb = getattr(e, 'traceback', sys.exc_info())
            tb_s = "".join(traceback.format_exception(*tb))
            if tools.config['debug_mode']:
                import pdb
                pdb.post_mortem(tb[2])
            details = ''
            if e.args:
                msg = e.args[0]
            else:
                msg = tools.ustr(e)
            if len(e.args) > 1:
                details = e.args[1]
            raise OpenERPDispatcherException(msg, details=details, traceback=tb_s)

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
