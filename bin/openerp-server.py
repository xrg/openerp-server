#!/usr/bin/env python
# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2011-2014 P. Christeas <xrg@hellug.gr>
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

"""
OpenERP - Server
OpenERP is an ERP+CRM program for small and medium businesses.

The whole source code is distributed under the terms of the
GNU Public Licence.

(c) 2003-2011, Fabien Pinckaers - OpenERP s.a.
(c) 2011-2014, P. Christeas
"""

#----------------------------------------------------------
# python imports
#----------------------------------------------------------
import os
import signal
import logging
import sys
import threading
import traceback

import release
__author__ = release.author
__version__ = release.version

if os.name == 'posix':
    import pwd
    # We DON't log this using the standard logger, because we might mess
    # with the logfile's permissions. Just do a quick exit here.
    if pwd.getpwuid(os.getuid())[0] == 'root' :
        sys.stderr.write("Attempted to run OpenERP server as root. This is not good, aborting.\n")
        sys.exit(1)

# This import causes netsvc to initialize itself, including logging.
import netsvc

#-----------------------------------------------------------------------
# import the tools module so that the commandline parameters are parsed
#-----------------------------------------------------------------------
import tools

openerp_isrunning = tools.misc.TSValue(False)

server_logger = logging.getLogger('server')
server_logger.info("OpenERP version - %s" % release.version )
for name, value in [('addons_path', tools.config['addons_path']),
                    ('database hostname', tools.config['db_host'] or 'localhost'),
                    ('database port', tools.config['db_port'] or '5432'),
                    ('database user', tools.config['db_user'])]:
    server_logger.info("%s - %s", name, value )

# Don't allow if the connection to PostgreSQL done by postgres user
if tools.config['db_user'] == 'postgres':
    server_logger.error("Attempted to connect database with postgres user. This is a security flaw, aborting.")
    sys.exit(1)

import time

LST_SIGNALS = ['SIGINT', 'SIGTERM']
# if os.name == 'posix':
#     LST_SIGNALS.extend(['SIGQUIT'])


SIGNALS = dict(
    [(getattr(signal, sign), sign) for sign in LST_SIGNALS]
)

def handler(signum, _):
    """
    :param signum: the signal number
    :param _: 
    """
    global openerp_isrunning
    server_logger.info("Signal received, trying to shutdown: %s", 
        SIGNALS.get(signum, str(signum)))
    
    if not openerp_isrunning.value:
        # this happens if one signal already has flipped the value
        # second signal should immediately cause server to exit.
        raise KeyboardInterrupt
    
    openerp_isrunning.value = False
    return

def sigusr1_handler(signum, _):
    global openerp_isrunning
    try:
        if openerp_isrunning:
            server_logger.info("Server is running normally")
        else:
            server_logger.info("Server is not in running state")
        
        
        stats = netsvc.Server.allStats()
        server_logger.info(stats)
        
        stats = netsvc.ExportService.allStats()
        server_logger.info(stats)
        
        import threading
        
        for thr in threading.enumerate():
            server_logger.debug('Thread found: %s', repr(thr))
    except Exception, e:
        print "Exception!", e
        pass
    
for signum in SIGNALS:
    signal.signal(signum, handler)

if os.name == 'posix':
    signal.signal(signal.SIGUSR1, sigusr1_handler)

#----------------------------------------------------------
# init net service
#----------------------------------------------------------
logging.getLogger("objects").info('initialising distributed objects services')

#---------------------------------------------------------------
# connect to the database and initialize it with base if needed
#---------------------------------------------------------------
import pooler

#----------------------------------------------------------
# import basic modules
#----------------------------------------------------------
import osv
import workflow
import report
import service

#----------------------------------------------------------
# import addons
#----------------------------------------------------------

import addons

#----------------------------------------------------------
# Load and update databases if requested
#----------------------------------------------------------

import service.http_server

if not ( tools.config["stop_after_init"] or \
    tools.config["translate_in"] or \
    tools.config["translate_out"] ):
    service.http_server.init_servers()
    service.http_server.init_xmlrpc()
    service.http_server.init_static_http()

    import service.netrpc_server
    service.netrpc_server.init_servers()

openerp_isrunning.value = True

init_logger = logging.getLogger('init')

if tools.config.get_misc('modules', 'preload', False):
    _preload_modules = map(str.strip, tools.config.get_misc('modules', 'preload').split(','))
    for pm in _preload_modules:
        addons.register_class(pm)
    
if tools.config['db_name']:
    for dbname in tools.config['db_name'].split(','):
        _langs = []
        if tools.config.get('lang'):
            _langs.append(tools.config['lang'])
        if tools.config.get('load_language'):
            _langs.extend(tools.config['load_language'].split(','))
        db,pool = pooler.get_db_and_pool(dbname, update_module=tools.config['init'] or tools.config['update'],
                pooljobs=False, languages=_langs)
        cr = db.cursor()

        if tools.config["test-file"]:
            init_logger.info('loading test file %s' % (tools.config["test-file"],))
            tools.convert_yaml_import(cr, 'base', file(tools.config["test-file"]), {}, 'test', True)
            cr.rollback()

        pool.get('ir.cron')._poolJobs(db.dbname)

        cr.close()

#----------------------------------------------------------
# translation stuff
#----------------------------------------------------------
if tools.config["translate_out"]:
    import csv

    if tools.config["language"]:
        msg = "language %s" % (tools.config["language"],)
    else:
        msg = "new language"
    init_logger.info('writing translation file for %s to %s' % (msg, 
                                        tools.config["translate_out"]))

    fileformat = os.path.splitext(tools.config["translate_out"])[-1][1:].lower()
    buf = file(tools.config["translate_out"], "w")
    dbname = tools.config['db_name']
    cr = pooler.get_db(dbname).cursor()
    tools.trans_export(tools.config["language"], tools.config["translate_modules"] or ["all"], buf, fileformat, cr)
    cr.close()
    buf.close()

    init_logger.info('translation file written successfully')
    sys.exit(0)

if tools.config["translate_in"]:
    context = {'overwrite': tools.config["overwrite_existing_translations"]}
    dbname = tools.config['db_name']
    cr = pooler.get_db(dbname).cursor()
    tools.trans_load(cr,
                     tools.config["translate_in"], 
                     tools.config["language"],
                     context=context)
    cr.commit()
    cr.close()
    sys.exit(0)

#----------------------------------------------------------------------------------
# if we don't want the server to continue to run after initialization, we quit here
#----------------------------------------------------------------------------------
if tools.config["stop_after_init"]:
    sys.exit(0)


#----------------------------------------------------------
# Launch Servers
#----------------------------------------------------------



def dumpstacks(signum, frame):
    # code from http://stackoverflow.com/questions/132058/getting-stack-trace-from-a-running-python-application#answer-2569696
    # modified for python 2.5 compatibility
    thread_map = dict(threading._active, **threading._limbo)
    id2name = dict([(threadId, thread.getName()) for threadId, thread in thread_map.items()])
    code = []
    for threadId, stack in sys._current_frames().items():
        code.append("\n# Thread: %s(%d)" % (id2name[threadId], threadId))
        for filename, lineno, name, line in traceback.extract_stack(stack):
            code.append('File: "%s", line %d, in %s' % (filename, lineno, name))
            if line:
                code.append("  %s" % (line.strip()))
    logging.getLogger('dumpstacks').info("\n".join(code))

if os.name == 'posix':
    signal.signal(signal.SIGQUIT, dumpstacks)
if tools.config['pidfile']:
    fd = open(tools.config['pidfile'], 'w')
    pidtext = "%d" % (os.getpid())
    fd.write(pidtext)
    fd.close()

netsvc.Server.startAll()
logging.getLogger("web-services").info('the server is running, waiting for connections...')

try:
    openerp_isrunning.waitFor(False)
except:
    # catch *all exceptions*
    pass

server_logger.info("Shutting down Server!")
netsvc.Agent.quit()
nrem = netsvc.Server.quitAll()
server_logger.debug("Server is finished!")
if tools.config['pidfile']:
    os.unlink(tools.config['pidfile'])
logging.shutdown()

if nrem:
    # print "Remaining %s, hard exit!" % nrem
    os._exit(0)
else:
    # print "Going for sys exit!"
    sys.exit(0)

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
