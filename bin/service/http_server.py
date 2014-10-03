# -*- coding: utf-8 -*-
#
# Copyright P. Christeas <xrg@hellug.gr> 2008-2013
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA.
###############################################################################

#.apidoc title: HTTP and XML-RPC Server

""" This module offers the family of HTTP-based servers. These are not a single
    class/functionality, but a set of network stack layers, implementing
    extendable HTTP protocols.

    The OpenERP server defines a single instance of a HTTP server, listening at
    the standard 8069, 8071 ports (well, it is 2 servers, and ports are
    configurable, of course). This "single" server then uses a `MultiHTTPHandler`
    to dispatch requests to the appropriate channel protocol, like the XML-RPC,
    static HTTP, DAV or other.

    Note: since XML-RPCv1 does NOT expose the client address to the upper layer,
    namely the "web services" one, the "client pit" will receive `None` and may
    hence block the entire XML-RPCv1 upon an attack!
"""

from websrv_lib import ConnThreadingMixIn, HTTPServer, HTTPDir, FixSendError, \
            MultiHTTPHandler, SecureMultiHTTPHandler, dummyconn, BoundStream, \
            AuthProxy, AuthRejectedExc, AuthRequiredExc, AuthProvider, \
            HttpOptions, HTTPHandler
import netsvc
import logging
import errno
import base64
import threading
import tools
import posixpath
import urllib
import os
import sys
import select
import socket
import re
import xmlrpclib
import StringIO
import weakref
from decimal import Decimal
import datetime
from types import NoneType

from SimpleXMLRPCServer import SimpleXMLRPCDispatcher

try:
    import fcntl
    __hush_pyflakes = [fcntl,]
except ImportError:
    fcntl = None

try:
    from ssl import SSLError
    __hush_pyflakes = [SSLError,]
except ImportError:
    class SSLError(Exception): pass

if os.name == 'posix':
    WRITE_BUFFER_SIZE = 32768
else:
    WRITE_BUFFER_SIZE = 0


class OERPMarshaller(xmlrpclib.Marshaller):
    """ Convert data to XML, like the v1,v2 protocols of XML-RPC used to do
    """
    dispatch = xmlrpclib.Marshaller.dispatch.copy()

    def dump_none(self, value, write):
        write("<value><boolean>0</boolean></value>")
    dispatch[NoneType] = dump_none

    def dump_datetime2str(self, value, write):
        if value.tzinfo is not None:
            # we must convert to server's tz
            raise NotImplementedError

        write("<value><string>")
        write(value.strftime('%Y-%m-%d %H:%M:%S'))
        write("</string></value>\n")

    dispatch[datetime.datetime] = dump_datetime2str

    def dump_date2str(self, value, write):
        write("<value><string>")
        write(value.strftime('%Y-%m-%d'))
        write("</string></value>\n")
    dispatch[datetime.date] = dump_date2str

    def dump_decimal(self, value, write):
        write("<value><double>")
        write(str(value))
        write("</double></value>\n")
    dispatch[Decimal] = dump_decimal

class OerpXMLRPCDispatcher(SimpleXMLRPCDispatcher):
    _marshaller_class = OERPMarshaller
    def _marshaled_dispatch(self, data, dispatch_method = None, path = None):
        """Dispatches an XML-RPC method from marshalled (XML) data.

            copied from the base class
        """
        try:
            # begin replacement of xmlrpclib.loads()
            p, u = xmlrpclib.getparser() # use_datetime=1
            p.feed(data)
            p.close()
            params = u.close()
            method = u.getmethodname()
            del p
            del u
            # end replacement

            # generate response
            if dispatch_method is not None:
                response = dispatch_method(method, params)
            else:
                response = self._dispatch(method, params)

            # begin replacement of dumps
            m = self._marshaller_class('utf-8', self.allow_none)

            response = ''.join([
                "<?xml version='1.0'?>\n" # utf-8 is default
                "<methodResponse>\n",
                m.dumps((response,)),
                "</methodResponse>\n"
                ])
            # end replacement
        except xmlrpclib.Fault, fault:
            response = xmlrpclib.dumps(fault, allow_none=self.allow_none,
                                       encoding=self.encoding)
        except Exception:
            # report exception back to client
            exc_type, exc_value, exc_tb = sys.exc_info()
            response = xmlrpclib.dumps(
                xmlrpclib.Fault(1, "%s:%s" % (exc_type, exc_value)),
                encoding=self.encoding, allow_none=self.allow_none,
                )

        return response


class ThreadedHTTPServer(ConnThreadingMixIn, OerpXMLRPCDispatcher, HTTPServer):
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

        OerpXMLRPCDispatcher.__init__(self)
        HTTPServer.__init__(self, addr, requestHandler)

        self.proto = proto
        self._threads = []
        self.__handlers = []
        self.__threadno = 0
        self._cleancount = 0

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
        self._threads.append(weakref.ref(thread))

    def _cleanup_refs(self):
        self._threads = [ r for r in self._threads if r() is not None]
        self.__handlers = [ r for r in self.__handlers if r() is not None]

    def _mark_end(self, thread):
        if self._cleancount > 100:
            self._cleanup_refs()
            self._cleancount = 0
        else:
            self._cleancount += 1

    def stop(self):
        self.socket.close()
        handlers = [ h() for h in self.__handlers if h() is not None ]  # copy the refs of the list
        self.socket = None
        for hnd in handlers:
            hnd.close_connection=1
            hnd.finish()

    def regHandler(self, handler):
        """Register a handler instance, so that we can keep count """
        self.__handlers.append(weakref.ref(handler))

    def unregHandler(self, handler):
        if self._cleancount > 100:
            self._cleanup_refs()
            self._cleancount = 0
        else:
            self._cleancount += 1

    @property
    def len_handlers(self):
        return len(self.__handlers)

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
        self.request.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self.server.regHandler(self)
        return MultiHTTPHandler.setup(self)

class SecureMultiHandler2(HttpLogHandler, SecureMultiHTTPHandler):
    _logger = logging.getLogger('https')
    wbufsize = WRITE_BUFFER_SIZE

    def getcert_fnames(self):
        tc = tools.config
        fcert = tc.get_misc('httpsd','sslcert', 'ssl/server.cert')
        fkey = tc.get_misc('httpsd','sslkey', 'ssl/server.key')
        return (fcert,fkey)

    def setup(self):
        self.request.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self.server.regHandler(self)
        return SecureMultiHTTPHandler.setup(self)

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
        except Exception:
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
        for t in self.server._threads:
            thr = t()
            if thr is not None:
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
            self.server._cleanup_refs()
            res += ", %d threads, %d handlers" % (len(self.server._threads), self.server.len_handlers)
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

        except SSLError:
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
    _IsSecure = True
    def __init__(self, interface, port):
        try:
            super(Http6SDaemon, self).__init__(address=(interface, port),
                                              handler=SecureMultiHandler2,
                                              server_class=Threaded6HTTPServer)
            self.daemon = True

        except SSLError:
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

def reg_http_service(hts, secure_only=False):
    """ Register some handler to httpd.
        hts must be an HTTPDir
    """
    global http_daemons

    if not http_daemons:
        logging.getLogger('httpd').warning("No httpd available to register service %s" % hts.path)
        return False

    ret = False
    for httpd in http_daemons:
        if secure_only and not httpd._IsSecure:
            continue
        httpd.append_svc(hts)
        ret = True
    return ret

def list_http_services(protocol=None):
    global http_daemons
    for httpd in http_daemons:
        if protocol is not None and protocol != httpd._RealProto.lower():
            continue
        return httpd.list_services()
    raise Exception("Incorrect protocol or no http services: %s" % protocol)

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
            del rbuffer

            auth = getattr(self, 'auth_proxy', None)
            if auth and getattr(auth, 'checkPepper', False):
                pepper = ''
                # FIXME : where will the pepper be in XML-RPC ?
                # pepper = kwargs.get('__pepper', None)
                if not auth.checkPepper(pepper):
                    self.send_error(403, "Authorization failed")
                    return

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

            if self.can_send_gzip(response):
                sbuffer = StringIO.StringIO()
                output = gzip.GzipFile(mode='wb', fileobj=sbuffer)
                if isinstance(response, (str, unicode)):
                    output.write(response)
                else:
                    for buf in response:
                        output.write(buf)
                output.close()
                del output
                sbuffer.seek(0)
                response = sbuffer.getvalue()
                del sbuffer
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
            raise xmlrpclib.Fault(e.compat_string(), e.traceback or "")

class XMLRPCRequestHandler2_Pub(netsvc.OpenERPDispatcher2,xrBaseRequestHandler):
    """ New-style xml-rpc dispatcher, Global methods
        Under this protocol, the authentication will lie in the http layer.
    """

    _auth_domain = 'pub'
    def setup(self):
        self.connection = dummyconn()

    def get_db_from_path(self, path):
        return False

class XMLRPCRequestHandler2_Root(netsvc.OpenERPDispatcher2,xrBaseRequestHandler):
    """ New-style xml-rpc dispatcher, Admin methods
    """

    _auth_domain = 'root'
    def setup(self):
        self.connection = dummyconn()

    def get_db_from_path(self, path):
        return True

class XMLRPCRequestHandler2_Db(netsvc.OpenERPDispatcher2,xrBaseRequestHandler):
    """ New-style xml-rpc dispatcher, DB methods
    """

    _auth_domain = 'db'
    def setup(self):
        self.connection = dummyconn()

    def get_db_from_path(self, path):
        if path.startswith('/'):
            path = path[1:]
        db = path.split('/',1)[0]
        return db

def init_xmlrpc():
    # late init of browse-object marshaller, after module has been loaded
    from tools.orm_utils import browse_null
    if browse_null is not None:
        OERPMarshaller.dispatch[browse_null] = OERPMarshaller.dump_none
    
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
        if not os.path.exists(path):
            self._logger.warning('Path not found: "%s"', path)
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
        self.last_address = None #: will hold last client address

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

        auth_str = handler.headers.get('Authorization',False)
        addr_str = self._get_addr_str(handler.client_address)

        if auth_str and auth_str.startswith('Basic '):
            auth_str=auth_str[len('Basic '):]
            (user,passwd) = base64.decodestring(auth_str).split(':',1)
            if db in self.auth_creds and not (self.auth_creds[db] is False):
                if self.provider.check_again(self.auth_creds[db], (user, passwd), handler):
                    self.auth_tries = 0
                    return True
        
            try:
                acd = self.provider.authenticate(db,user,passwd,handler.client_address)

            except AuthRequiredExc:
                # sometimes the provider.authenticate may raise, so that
                # it asks for a specific realm. Still, apply the 5 times rule
                if self.auth_tries > 5:
                    raise AuthRejectedExc("Authorization failed.")
                else:
                    self.auth_tries += 1
                    raise
            if acd:
                self.provider.log("Auth user=\"%s@%s\" from %s" %(user, db, addr_str), lvl=logging.INFO)
                if db:
                    # we only cache the credentials if the db is specified.
                    # the True value gets cached, too, for the super-admin
                    self.auth_creds[db] = acd
                    self.last_auth=db
                    self.last_address = addr_str
                self.auth_tries = 0
                return True
            else:
                self.provider.log("Auth FAILED for user=\"%s@%s\" from %s" %(user, db, addr_str), lvl=logging.WARNING)

        else:    # no auth string
            if db is False:
                # in a special case, we ask the provider to allow us to
                # skip authentication for the "False" db
                acd = self.provider.authenticate(db, None, None, handler.client_address)
                if acd:
                    self.provider.log("Public connection from %s" % (addr_str), lvl=logging.INFO)
                    return True

        if self.auth_tries > 5:
            self.provider.log("Failing authorization after 5 requests w/o password")
            raise AuthRejectedExc("Authorization failed.")
        self.auth_tries += 1
        raise AuthRequiredExc(atype='Basic', realm=self.provider.realm)

    def get_uid(self, dbname):
        """Retrieve the authenticated user id for a database

            @return uid or False if not authenticated
        """
        if self.last_auth and dbname in self.auth_creds:
            return self.auth_creds[dbname][3]
        return False

import security
class OpenERPAuthProvider(AuthProvider):
    proxyFactory = OerpAuthProxy

    def __init__(self,realm='OpenERP User', domain='db'):
        self.realm = realm
        self.domain=domain

    def setupAuth(self, multi, handler):
        if not multi.sec_realms.has_key(self.realm):
            multi.sec_realms[self.realm] = self.proxyFactory(self)
        handler.auth_proxy = multi.sec_realms[self.realm]

    def authenticate(self, db, user, passwd, client_address):
        try:
            uid = security.login(db, user, passwd, client_address)
            if uid is False:
                return False
            return (user, passwd, db, uid)
        except Exception,e:
            logging.getLogger("auth").debug("Fail auth: %s", e )
            return False
        return False

    def check_again(self, auth_creds, new_creds, handler):
        try:
            if auth_creds and isinstance(auth_creds, tuple) \
                    and new_creds and len(new_creds) >= 2:
                if auth_creds[:2] == new_creds[:2] :
                    return True
        except Exception, e:
            logging.getLogger("auth").debug("Fail auth: %s", e )
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

    def check_again(self, auth_creds, new_creds, handler):
        """ Same check as `authenticate()`, but silent at logging
        """
        try:
            if auth_creds is True and new_creds and len(new_creds) >= 2 \
                    and new_creds[0] == 'root':
                if security.check_super(new_creds[1]):
                    return True
        except security.ExceptionNoTb:
            return False
        return False

#eof
