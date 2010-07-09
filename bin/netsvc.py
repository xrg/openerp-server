#!/usr/bin/env python
# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
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

    def abortResponse(self, error, description, origin, details):
        if not tools.config['debug_mode']:
            tb = sys.exc_info()
            tb_s = "".join(traceback.format_exception(*tb))
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
    
    def __init__(self, name, audience=''):
        ExportService._services[name] = self
        self.__name = name

    def joinGroup(self, name):
        ExportService._groups.setdefault(name, {})[self.__name] = self

    @classmethod
    def getService(cls,name):
        return cls._services[name]

    def dispatch(self, method, auth, params):
        raise Exception("stub dispatch at %s" % self.__name)
        
    def new_dispatch(self,method,auth,params):
        raise NotImplementedError("stub dispatch at %s" % self.__name)

    def abortResponse(self, error, description, origin, details):
        if not tools.config['debug_mode']:
            tb = sys.exc_info()
            tb_s = "".join(traceback.format_exception(*tb))
            raise OpenERPDispatcherException(description, origin=origin, 
                    details=details, faultCode=error, traceback=tb_s)
        else:
            raise

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

class ColoredFormatter(logging.Formatter):
    def format(self, record):
        fg_color, bg_color = LEVEL_COLOR_MAPPING[record.levelno]
        record.levelname = COLOR_PATTERN % (30 + fg_color, 40 + bg_color, record.levelname)
        return logging.Formatter.format(self, record)


def init_logger():
    import os
    from tools.translate import resetlocale
    resetlocale()

    logger = logging.getLogger()
    # create a format for log messages and dates
    format = '[%(asctime)s] %(levelname)s:%(name)s:%(message)s'

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
        except Exception, ex:
            sys.stderr.write("ERROR: couldn't create the logfile directory. Logging to the standard output.\n")
            handler = logging.StreamHandler(sys.stdout)
    else:
        # Normal Handler on standard output
        handler = logging.StreamHandler(sys.stdout)

    if isinstance(handler, logging.StreamHandler) and os.isatty(handler.stream.fileno()):
        formatter = ColoredFormatter(format, '%Y-%m-%d %H:%M:%S')
    else:
        formatter = logging.Formatter(format, '%Y-%m-%d %H:%M:%S')
    handler.setFormatter(formatter)

    # add the handler to the root logger
    logger.addHandler(handler)
    logger.setLevel(int(tools.config['log_level'] or '0'))
    
    # By default, don't log db connections, even at debug.
    if int(tools.config['log_level'] or '0') <= logging.DEBUG:
        logging.getLogger('db.connection').setLevel(logging.INFO)


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

        log = logging.getLogger(tools.ustr(name))

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
        except IOError,e:
            # TODO: perhaps reset the logger streams?
            #if logrotate closes our files, we end up here..
            pass
        except:
            # better ignore the exception and carry on..
            pass

    def set_loglevel(self, level, logger=None):
        if logger is not None:
            log = logging.getLogger(str(logger))
        else:
            log = logging.getLogger()
        log.setLevel(logging.INFO) # make sure next msg is printed
        log.info("Log level changed to %s" % logging.getLevelName(level))
        log.setLevel(level)

    def set_logger_level(self, logger, level):
        """ Set the logging level for a particular logger
        """
        log = logging.getLogger(str(logger))
        log.setLevel(logging.INFO) # make sure next msg is printed
        log.info("Log level for %s changed to %s" % (logger, logging.getLevelName(level)))
        log.setLevel(level)

import tools
init_logger()

class Agent(object):
    _timers = {}
    _logger = Logger()

    __logger = logging.getLogger('timer')

    def setAlarm(self, fn, dt, db_name, *args, **kwargs):
        wait = dt - time.time()
        if wait > 0:
            self.__logger.debug("Job scheduled in %.3g seconds for %s.%s" % (wait, fn.im_class.__name__, fn.func_name))
            timer = threading.Timer(wait, fn, args, kwargs)
            timer.start()
            self._timers.setdefault(db_name, []).append(timer)

        for db in self._timers:
            for timer in self._timers[db]:
                if not timer.isAlive():
                    self._timers[db].remove(timer)

    @classmethod
    def cancel(cls, db_name):
        """Cancel all timers for a given database. If None passed, all timers are cancelled"""
        for db in cls._timers:
            if db_name is None or db == db_name:
                for timer in cls._timers[db]:
                    timer.cancel()

    @classmethod
    def quit(cls):
        cls.cancel(None)

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
    _busywait_timeout = 0.5


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

    def get_faultCode(self):
        return int(self.faultCode) or 1
   
    def get_faultString(self):
        """Get the fault string in xml-rpc2 format
        """
        ret = "X-Exception: %s" % (tools.ustr(self.args[0]))
        ret += '\nX-ExcOrigin: %s' % self.args[2]
        if self.args[1]:
            ret += '\nX-ExcDetails: %s' % (tools.ustr(self.args[1]))
        if self.traceback:
            ret += "\nX-Traceback: %s" % ('\n\t'.join(self.traceback.split('\n')))
        return ret

    def compat_string(self):
        """Get the string that v5, xml-rpc1 exceptions would need
        """
        if self.args[2] == 'exception':
            return '%s\%s' % (tools.ustr(self.args[0]), tools.ustr(self.args[1]))
        else:
            return "%s -- %s\n\n%s" % (self.args[2], tools.ustr(self.args[0]), 
                    tools.ustr(self.args[1]))

class OpenERPDispatcher:
    def log(self, title, msg):
        logger = logging.getLogger(title)
        if logger.isEnabledFor(logging.DEBUG_RPC):
            for line in pformat(msg).split('\n'):
                logger.log(logging.DEBUG_RPC, line)

    def dispatch(self, service_name, method, params):
        try:
            self.log('service', service_name)
            self.log('method', method)
            self.log('params', params)
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

    def dispatch(self, service_name, method, params):
        _logger = logging.getLogger('rpc')
        def log(title, msg):
            _logger.log(logging.DEBUG_RPC,'%s: %s' %(title, pformat(msg)))
        
        try:
            log('service', service_name)
            log('method', method)
            log('params', params)
            auth = getattr(self, 'auth_proxy', None)
            if not auth:
                _logger.warn("No Authentication!")
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
            if len(e.args) > 1:
                details = e.args[1]
            raise OpenERPDispatcherException(e.args[0], details=details, traceback=tb_s)

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
