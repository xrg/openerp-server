# -*- coding: utf-8 -*-
#
# Copyright P. Christeas <p_christ@hol.gr> 2008-2010
# Copyright 2010 OpenERP SA. (http://www.openerp.com)
#
#
# WARNING: This program as such is intended to be used by professional
# programmers who take the whole responsability of assessing all potential
# consequences resulting from its eventual inadequacies and bugs
# End users who are looking for a ready-to-use solution with commercial
# garantees and support are strongly adviced to contract a Free Software
# Service Company
#
# This program is Free Software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public License
# as published by the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
###############################################################################

""" This file contains instance of the http server.


"""
from websrv_lib import *
import netsvc
import logging
import errno
import threading
import tools
import posixpath
import urllib
import os
import select
import socket
import re
import xmlrpclib
import StringIO

from SimpleXMLRPCServer import SimpleXMLRPCDispatcher

try:
    import fcntl
except ImportError:
    fcntl = None

try:
    from ssl import SSLError
except ImportError:
    class SSLError(Exception): pass

if os.name == 'posix':
    WRITE_BUFFER_SIZE = 32768
else:
    WRITE_BUFFER_SIZE = 0

class ThreadedHTTPServer(ConnThreadingMixIn, SimpleXMLRPCDispatcher, HTTPServer):
    """ A threaded httpd server, with all the necessary functionality for us.

        It also inherits the xml-rpc dispatcher, so that some xml-rpc functions
        will be available to the request handler
    """
    encoding = None
    allow_none = False
    allow_reuse_address = 1
    _send_traceback_header = False
    daemon_threads = True
    i = 0

    def __init__(self, addr, requestHandler, proto='http',
                 logRequests=True, allow_none=False, encoding=None, bind_and_activate=True):
        self.logRequests = logRequests

        SimpleXMLRPCDispatcher.__init__(self, allow_none, encoding)
        HTTPServer.__init__(self, addr, requestHandler)
        
        self.proto = proto
        self._threads = []
        self.__handlers = []
        self.__threadno = 0

        # [Bug #1222790] If possible, set close-on-exec flag; if a
        # method spawns a subprocess, the subprocess shouldn't have
        # the listening socket open.
        if fcntl is not None and hasattr(fcntl, 'FD_CLOEXEC'):
            flags = fcntl.fcntl(self.fileno(), fcntl.F_GETFD)
            flags |= fcntl.FD_CLOEXEC
            fcntl.fcntl(self.fileno(), fcntl.F_SETFD, flags)
        self.socket.settimeout(2)

    def handle_error(self, request, client_address):
        """ Override the error handler
        """
        
        logging.getLogger("init").exception("Server error in request from %s:" % (client_address,))

    def _mark_start(self, thread):
        self._threads.append(thread)

    def _mark_end(self, thread):
        try:
            self._threads.remove(thread)
        except ValueError: pass
    
    def stop(self):
        self.socket.close()
        h = self.__handlers[:]  # copy the list
        self.socket = None
        for hnd in h:
            hnd.close_connection=1
            hnd.finish()

    def regHandler(self, handler):
        """Register a handler instance, so that we can keep count """
        self.__handlers.append(handler)

    def unregHandler(self, handler):
        try:
            self.__handlers.remove(handler)
        except ValueError: pass

    def _get_next_name(self):
        self.__threadno += 1
        return 'http-client-%d' % self.__threadno

class Threaded6HTTPServer(ThreadedHTTPServer):
    """A variant of ThreadedHTTPServer for IPv6
    """
    address_family = socket.AF_INET6

class HttpLogHandler:
    """ helper class for uniform log handling
    Please define self._logger at each class that is derived from this
    """
    _logger = None
    
    def log_message(self, format, *args):
        self._logger.debug(format % args) # todo: perhaps other level

    def log_error(self, format, *args):
        self._logger.error(format % args)
        
    def log_exception(self, format, *args):
        self._logger.exception(format, *args)

    def log_request(self, code='-', size='-'):
        self._logger.log(netsvc.logging.DEBUG_RPC, '"%s" %s %s',
                        self.requestline, str(code), str(size))
    
class MultiHandler2(HttpLogHandler, MultiHTTPHandler):
    _logger = logging.getLogger('http')
    wbufsize = WRITE_BUFFER_SIZE
    
    def setup(self):
        self.server.regHandler(self)
        return MultiHTTPHandler.setup(self)

    def finish(self):
        res = MultiHTTPHandler.finish(self)
        self.server.unregHandler(self)
        return res

class SecureMultiHandler2(HttpLogHandler, SecureMultiHTTPHandler):
    _logger = logging.getLogger('https')
    wbufsize = WRITE_BUFFER_SIZE

    def getcert_fnames(self):
        tc = tools.config
        fcert = tc.get_misc('httpsd','sslcert', 'ssl/server.cert')
        fkey = tc.get_misc('httpsd','sslkey', 'ssl/server.key')
        return (fcert,fkey)

    def setup(self):
        self.server.regHandler(self)
        return SecureMultiHTTPHandler.setup(self)

    def finish(self):
        res = SecureMultiHTTPHandler.finish(self)
        self.server.unregHandler(self)
        return res

class BaseHttpDaemon(threading.Thread, netsvc.Server):
    _RealProto = '??'
    _ClientProto = False  # one to report to clients, like  <clientproto>://1.2.3.4:8069/
    _IsSecure = False

    def __init__(self, address, handler, server_class=ThreadedHTTPServer):
        threading.Thread.__init__(self, name='%sDaemon-%d'%(self._RealProto, address[1]))
        netsvc.Server.__init__(self)
        self.__address = address

        try:
            self.server = server_class(address, handler, proto=(self._ClientProto or self._RealProto))
            self.server.vdirs = []
            self.server.logRequests = True
            interface, port = address[:2]
            logging.getLogger("web-services").info(
                        "starting %s service at %s port %d" %
                        (self._RealProto, interface or '0.0.0.0', port,))
        except Exception, e:
            logging.getLogger("httpd").exception("Error occured when starting the server daemon.")
            raise

    @property
    def socket(self):
        return self.server.socket

    def attach(self, path, gw):
        pass

    def stop(self):
        self.running = False
        self._close_socket()
        self.server.stop()

    def join(self, timeout=None):
        for thr in self.server._threads:
            thr.join(timeout)
        threading.Thread.join(self, timeout)

    def run(self):
        self.running = True
        while self.running:
            try:
                self.server.handle_request()
            except (socket.error, select.error), e:
                if self.running or e.args[0] != errno.EBADF:
                    raise
        return True

    def stats(self):
        res = "%sd: " % self._RealProto + ((self.running and "running") or  "stopped")
        if self.server:
            res += ", %d threads" % (len(self.server._threads),)
        return res

    def append_svc(self, service):
        if not isinstance(service, HTTPDir):
            raise Exception("Wrong class for http service")
        
        pos = len(self.server.vdirs)
        lastpos = pos
        while pos > 0:
            pos -= 1
            if self.server.vdirs[pos].matches(service.path):
                lastpos = pos
            # we won't break here, but search all way to the top, to
            # ensure there is no lesser entry that will shadow the one
            # we are inserting.
        self.server.vdirs.insert(lastpos, service)

    def list_services(self):
        ret = []
        for svc in self.server.vdirs:
            ret.append( ( svc.path, str(svc.handler)) )
        
        return ret


class HttpDaemon(BaseHttpDaemon):
    _RealProto = 'HTTP'
    _ClientProto = 'http'
    def __init__(self, interface, port):
        super(HttpDaemon, self).__init__(address=(interface, port),
                                         handler=MultiHandler2)
        self.daemon = True

class HttpSDaemon(BaseHttpDaemon):
    _RealProto = 'HTTPS'
    _ClientProto = 'https'
    _IsSecure = True
    def __init__(self, interface, port):
        try:
            super(HttpSDaemon, self).__init__(address=(interface, port),
                                              handler=SecureMultiHandler2)
            self.daemon = True

        except SSLError, e:
            logging.getLogger('httpsd').exception( \
                        "Can not load the certificate and/or the private key files")
            raise

class Http6Daemon(BaseHttpDaemon):
    _RealProto = 'HTTP6'
    _ClientProto = 'http'
    def __init__(self, interface, port):
        super(Http6Daemon, self).__init__(address=(interface, port, 0, 0),
                                         handler=MultiHandler2,
                                         server_class=Threaded6HTTPServer)
        self.daemon = True

class Http6SDaemon(BaseHttpDaemon):
    _RealProto = 'HTTP6S'
    _ClientProto = 'https'
    def __init__(self, interface, port):
        try:
            super(Http6SDaemon, self).__init__(address=(interface, port),
                                              handler=SecureMultiHandler2,
                                              server_class=Threaded6HTTPServer)
            self.daemon = True

        except SSLError, e:
            logging.getLogger('httpsd').exception( \
                        "Can not load the certificate and/or the private key files")
            raise

http_daemons = []

def init_servers():
    global http_daemons
    ipv4_re = re.compile('^([0-9]{1,3}(?:\.(?:[0-9]{1,3})){3})(?::(\d{1,5}))?$')
    ipv6_re = re.compile('^\[([0-9a-f:]+)\](?::(\d{1,5}))?$')
    
    ipv6_missing = False
    if tools.config.get_misc('httpd','enable', True):
        ifaces = tools.config.get_misc('httpd','interface', '')
        if not ifaces:
            ifaces = '0.0.0.0' # By default, IPv4 only
        ifaces = map(str.strip, ifaces.split(','))
        base_port = tools.config.get_misc('httpd','port', tools.config.get('port',8069))
        for iface in ifaces:
            m = ipv4_re.match(iface)
            if m:
                httpd = HttpDaemon( m.group(1), int(m.group(2) or base_port))
                http_daemons.append(httpd)
                continue
            m = ipv6_re.match(iface)
            if m:
                if not socket.has_ipv6:
                    ipv6_missing = True
                    continue
                httpd = Http6Daemon( m.group(1), int(m.group(2) or base_port))
                http_daemons.append(httpd)
                continue
            logging.getLogger('httpd').error("Cannot understand address \"%s\" to launch http daemon on!", iface)

    if tools.config.get_misc('httpsd','enable', True):
        ifaces = tools.config.get_misc('httpsd','interface', '')
        if not ifaces:
            ifaces = '0.0.0.0' # By default, IPv4 only
        ifaces = map(str.strip, ifaces.split(','))
        base_port = tools.config.get_misc('httpsd','port', 8071)
        for iface in ifaces:
            m = ipv4_re.match(iface)
            if m:
                httpd = HttpSDaemon( m.group(1), int(m.group(2) or base_port))
                http_daemons.append(httpd)
                continue
            m = ipv6_re.match(iface)
            if m:
                if not socket.has_ipv6:
                    ipv6_missing = True
                    continue
                httpd = Http6SDaemon( m.group(1), int(m.group(2) or base_port))
                http_daemons.append(httpd)
                continue
            logging.getLogger('httpd').error("Cannot understand address \"%s\" to launch http daemon on!", iface)

    if ipv6_missing:
        logging.getLogger('http6d').warning("HTTPd servers for IPv6 specified, but not supported at this platform.")

def reg_http_service(hts, secure_only = False):
    """ Register some handler to httpd.
        hts must be an HTTPDir
    """
    global http_daemons

    if not http_daemons:
        logging.getLogger('httpd').warning("No httpd available to register service %s" % hts.path)
        return False

    for httpd in http_daemons:
        if secure_only and not httpd._IsSecure:
            continue
        httpd.append_svc(hts)
    return True

def list_http_services(protocol=None):
    global http_daemons
    for httpd in http_daemons:
        if protocol is not None and protocol != httpd._RealProto.lower():
            continue
        return httpd.list_services()
    raise Exception("Incorrect protocol or no http services")

import SimpleXMLRPCServer
import gzip

class xrBaseRequestHandler(FixSendError, HttpLogHandler, SimpleXMLRPCServer.SimpleXMLRPCRequestHandler):
    rpc_paths = []
    protocol_version = 'HTTP/1.1'
    _auth_domain = None
    _logger = logging.getLogger('xmlrpc')

    def _dispatch(self, method, params):
        # used *only* in xmlrpc2
        try:
            service_name = self.path.split("/")[-1]
            return self.dispatch(service_name, method, params)
        except netsvc.OpenERPDispatcherException, e:
            raise xmlrpclib.Fault(e.get_faultCode(), e.get_faultString())

    def handle(self):
        pass

    def finish(self):
        pass

    def do_POST(self):
        """Handles the HTTP POST request.

        Mostly copied from SimpleXMLRPCRequestHandler.
        """

        # Check that the path is legal
        if not self.is_rpc_path_valid():
            self.report_404()
            return

        try:
            # Get arguments by reading body of request.
            # We read this in chunks to avoid straining
            # socket.read(); around the 10 or 15Mb mark, some platforms
            # begin to have problems (bug #792570).
            max_chunk_size = 10*1024*1024
            clen = int(self.headers["content-length"])
            rbuffer = BoundStream(self.rfile, clen, chunk_size=max_chunk_size)
            data = ''
            if self.headers.get('content-encoding',False) == 'gzip':
                rbuffer = gzip.GzipFile(mode='rb', fileobj=rbuffer)

            try:
                while True:
                    chunk = rbuffer.read()
                    if not chunk:
                        break
                    data += chunk
            except EOFError:
                pass

            # In previous versions of SimpleXMLRPCServer, _dispatch
            # could be overridden in this class, instead of in
            # SimpleXMLRPCDispatcher. To maintain backwards compatibility,
            # check to see if a subclass implements _dispatch and dispatch
            # using that method if present.
            response = self.server._marshaled_dispatch(
                    data, getattr(self, '_dispatch', None)
                )

        except Exception, e: # This should only happen if the module is buggy
            # internal error, report as HTTP server error
            self.send_response(500)

            # Send information about the exception if requested
            if hasattr(self.server, '_send_traceback_header') and \
                    self.server._send_traceback_header:
                import traceback
                self.send_header("X-exception", str(e))
                self.send_header("X-traceback", traceback.format_exc())

            self.end_headers()
        else:
            # got a valid XML RPC response
            self.send_response(200)
            self.send_header("Content-type", "text/xml")
            
            if response \
                    and 'gzip' in self.headers.get('Accept-Encoding', '').split(',') \
                    and len(response) > 512:
                buffer = StringIO.StringIO()
                output = gzip.GzipFile(mode='wb', fileobj=buffer)
                if isinstance(response, (str, unicode)):
                    output.write(response)
                else:
                    for buf in response:
                        output.write(buf)
                output.close()
                buffer.seek(0)
                response = buffer.getvalue()
                self.send_header('Content-Encoding', 'gzip')

            self.send_header("Content-length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            self.wfile.flush()

class XMLRPCRequestHandler(netsvc.OpenERPDispatcher,xrBaseRequestHandler):
    def setup(self):
        self.connection = dummyconn()
        self.rpc_paths = map(lambda s: '/%s' % s, netsvc.ExportService._services.keys())

    def _dispatch(self, method, params):
        try:
            service_name = self.path.split("/")[-1]
            return self.dispatch(service_name, method, params)
        except netsvc.OpenERPDispatcherException, e:
            raise xmlrpclib.Fault(e.compat_string(), e.traceback)

class XMLRPCRequestHandler2_Pub(netsvc.OpenERPDispatcher2,xrBaseRequestHandler):
    """ New-style xml-rpc dispatcher, Global methods
        Under this protocol, the authentication will lie in the http layer.
    """

    _auth_domain = 'pub'
    def setup(self):
        self.connection = dummyconn()
        #if not len(XMLRPCRequestHandler.rpc_paths):
        #    XMLRPCRequestHandler.rpc_paths = map(lambda s: '/%s' % s, netsvc.ExportService._services.keys())
        pass

    def get_db_from_path(self, path):
        return False

class XMLRPCRequestHandler2_Root(netsvc.OpenERPDispatcher2,xrBaseRequestHandler):
    """ New-style xml-rpc dispatcher, Admin methods
    """

    _auth_domain = 'root'
    def setup(self):
        self.connection = dummyconn()
        #if not len(XMLRPCRequestHandler.rpc_paths):
        #    XMLRPCRequestHandler.rpc_paths = map(lambda s: '/%s' % s, netsvc.ExportService._services.keys())
        pass

    def get_db_from_path(self, path):
        return True

class XMLRPCRequestHandler2_Db(netsvc.OpenERPDispatcher2,xrBaseRequestHandler):
    """ New-style xml-rpc dispatcher, DB methods
    """

    _auth_domain = 'db'
    def setup(self):
        self.connection = dummyconn()
        #if not len(XMLRPCRequestHandler.rpc_paths):
        #    XMLRPCRequestHandler.rpc_paths = map(lambda s: '/%s' % s, netsvc.ExportService._services.keys())
        pass

    def get_db_from_path(self, path):
        if path.startswith('/'):
            path = path[1:]
        db = path.split('/')[0]
        return db

def init_xmlrpc():
    if tools.config.get_misc('xmlrpc','enable', True):
        sso = tools.config.get_misc('xmlrpc','ssl_require', False)
        if reg_http_service(HTTPDir('/xmlrpc/',XMLRPCRequestHandler), secure_only=sso):
            logging.getLogger("web-services").info("Registered XML-RPC over HTTP")

    if tools.config.get_misc('xmlrpc2','enable', True):
        sso = tools.config.get_misc('xmlrpc2','ssl_require', False)
        if reg_http_service(HTTPDir('/xmlrpc2/pub/',XMLRPCRequestHandler2_Pub), 
                        secure_only=sso) \
            and reg_http_service(HTTPDir('/xmlrpc2/root/',XMLRPCRequestHandler2_Root,
                            OpenERPRootProvider(realm="OpenERP Admin", domain='root')),
                        secure_only=sso) \
            and reg_http_service(HTTPDir('/xmlrpc2/db/',XMLRPCRequestHandler2_Db,
                            OpenERPAuthProvider()), 
                        secure_only=sso):
            logging.getLogger("web-services").info( "Registered XML-RPC 2.0 over HTTP")

class StaticHTTPHandler(HttpLogHandler, FixSendError, HttpOptions, HTTPHandler):
    _logger = logging.getLogger('httpd')
    _HTTP_OPTIONS = { 'Allow': ['OPTIONS', 'GET', 'HEAD'] }

    def __init__(self,request, client_address, server):
        HTTPHandler.__init__(self,request,client_address,server)
        dir_path = tools.config.get_misc('static-http', 'dir_path', False)
        assert dir_path, "Please specify static-http/dir_path in config, or disable static-httpd!"
        self.__basepath = dir_path

    def translate_path(self, path):
        """Translate a /-separated PATH to the local filename syntax.

        Components that mean special things to the local file system
        (e.g. drive or directory names) are ignored.  (XXX They should
        probably be diagnosed.)

        """
        # abandon query parameters
        path = path.split('?',1)[0]
        path = path.split('#',1)[0]
        path = posixpath.normpath(urllib.unquote(path))
        words = path.split('/')
        words = filter(None, words)
        path = self.__basepath
        for word in words:
            if word in (os.curdir, os.pardir): continue
            path = os.path.join(path, word)
        return path

def init_static_http():
    if not tools.config.get_misc('static-http','enable', False):
        return
    
    dir_path = tools.config.get_misc('static-http', 'dir_path', False)
    assert dir_path
    
    base_path = tools.config.get_misc('static-http', 'base_path', '/')
    
    if reg_http_service(HTTPDir(base_path,StaticHTTPHandler)):
        logging.getLogger("web-services").info("Registered HTTP dir %s for %s" % \
                        (dir_path, base_path))


class OerpAuthProxy(AuthProxy):
    """ Require basic authentication..

        This is a copy of the BasicAuthProxy, which however checks/caches the db
        as well.
    """
    def __init__(self,provider):
        AuthProxy.__init__(self,provider)
        self.auth_creds = {}
        self.auth_tries = 0
        self.last_auth = None

    def checkRequest(self,handler,path='/', db=False):
        """ Check authorization of request to path.
            First, we must get the "db" from the path, because it could
            need different authorization per db.
            
            The handler could help us dissect the path, or even return
            True for the super user or False for an allways-allowed path.
            
            Then, we see if we have cached that authorization for this 
            proxy (= session)
         """
        try:
            if not db:
                db = handler.get_db_from_path(path)
        except Exception:
            if path.startswith('/'):
                path = path[1:]
            psp= path.split('/')
            if len(psp)>1:
                db = psp[0]
            else:
                #FIXME!
                self.provider.log("Wrong path: %s, failing auth" %path)
                raise AuthRejectedExc("Authorization failed. Wrong sub-path.")

        if db in self.auth_creds and not (self.auth_creds[db] is False):
            return True
        auth_str = handler.headers.get('Authorization',False)

        if auth_str and auth_str.startswith('Basic '):
            auth_str=auth_str[len('Basic '):]
            (user,passwd) = base64.decodestring(auth_str).split(':')
            try:
                acd = self.provider.authenticate(db,user,passwd,handler.client_address)
                if handler.client_address and len(handler.client_address) == 4:
                    addr_str = "[%s]:%s" % (handler.client_address[:2])
                elif handler.client_address:
                    addr_str = "%s:%s" % handler.client_address
                else:
                    addr_str = '?'
                if acd:
                    self.provider.log("Auth user=\"%s@%s\" from %s" %(user, db, addr_str), lvl=logging.INFO)
                else:
                    self.provider.log("Auth FAILED for user=\"%s@%s\" from %s" %(user, db, addr_str), lvl=logging.WARNING)

            except AuthRequiredExc:
                # sometimes the provider.authenticate may raise, so that
                # it asks for a specific realm. Still, apply the 5 times rule
                if self.auth_tries > 5:
                    raise AuthRejectedExc("Authorization failed.")
                else:
                    raise
            if acd:
                if db:
                    # we only cache the credentials if the db is specified.
                    # the True value gets cached, too, for the super-admin
                    self.auth_creds[db] = acd
                    self.last_auth=db
                return True
        else:    # no auth string
            if db is False:
                # in a special case, we ask the provider to allow us to
                # skip authentication for the "False" db
                acd = self.provider.authenticate(db, None, None, handler.client_address)
                if acd:
                    return True

        if self.auth_tries > 5:
            self.provider.log("Failing authorization after 5 requests w/o password")
            raise AuthRejectedExc("Authorization failed.")
        self.auth_tries += 1
        raise AuthRequiredExc(atype='Basic', realm=self.provider.realm)

import security
class OpenERPAuthProvider(AuthProvider):
    def __init__(self,realm='OpenERP User', domain='db'):
        self.realm = realm
        self.domain=domain

    def setupAuth(self, multi, handler):
        if not multi.sec_realms.has_key(self.realm):
            multi.sec_realms[self.realm] = OerpAuthProxy(self)
        handler.auth_proxy = multi.sec_realms[self.realm]

    def authenticate(self, db, user, passwd, client_address):
        try:
            uid = security.login(db,user,passwd)
            if uid is False:
                return False
            return (user, passwd, db, uid)
        except Exception,e:
            logging.getLogger("auth").debug("Fail auth: %s" % e )
            return False
        return False

    def log(self, msg, lvl=logging.INFO):
        logging.getLogger("auth").log(lvl,msg)

class OpenERPRootProvider(OpenERPAuthProvider):
    """ Authentication provider for the OpenERP database admin
    """
    def authenticate(self, db, user, passwd, client_address):
        try:
            if user == 'root' and security.check_super(passwd, client_address):
                return True
        except security.ExceptionNoTb:
            return False
        return False

#eof
