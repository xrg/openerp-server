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

import ConfigParser
import optparse
import os
import sys
import re
import netsvc
import logging
import release

#.apidoc title: Server Configuration Loader

def check_ssl():
    try:
        from OpenSSL import SSL
        import socket
        
        return hasattr(socket, 'ssl') and hasattr(SSL, "Connection")
    except:
        return False

class configmanager(object):
    _db_sec_re = re.compile(r'(\w+) "(\w+)"')
    def __init__(self, fname=None):
        self.options = {
            'db_host': False,
            'db_port': False,
            'db_name': False,
            'db_user': False,
            'db_password': False,
            'db_maxconn': 64,
            'reportgz': False,
            'translate_in': None,
            'translate_out': None,
            'overwrite_existing_translations': False,
            'load_language': None,
            'language': None,
            'pg_path': None,
            'admin_passwd': 'admin',
            'csv_internal_sep': ',',
            'addons_path': None,
            'root_path': None,
            'debug_mode': False,
            'import_partial': "",
            'pidfile': None,
            'logfile': None,
            'logrotate': True,
            'stop_after_init': False,   # this will stop the server after initialization
            'syslog' : False,
            'log_level': logging.INFO,
            'assert_exit_level': logging.ERROR, # level above which a failed assert will be raised
            'login_message': False,
            'list_db' : True,
            'timezone' : False, # to override the default TZ
            'test_file' : False,
            'test_report_directory' : False,
            'test_disable' : False,
            'test_commit' : False,
            'publisher_warranty_url': False,
            'osv_memory_count_limit': None, # number of records in each osv_memory virtual table
            'osv_memory_age_limit': 1, # hours
        }

        self.aliases = {
            'cache_timeout': 'cache.timeout',
            'httpd_interface': 'httpd.interface',
            'httpd_port': 'httpd.port',
            'httpds_interface': 'httpsd.interface',
            'httpds_port': 'httpsd.port',
            'httpd': 'httpd.enable',
            'httpds': 'httpsd.enable',
            'netrpc_interface': 'netrpcd.interface',
            'netrpc_port': 'netrpcd.port',
            'netrpc': 'netrpcd.enable',
            'secure_cert_file': 'httpsd.sslcert',
            'secure_pkey_file': 'httpsd.sslkey',
            'osv_memory_count_limit': 'osv_memory.count_limit',
            'osv_memory_age_limit': 'osv_memory.age_limit',
            'smtp_server': 'smtp.server',
            'smtp_user': 'smtp.user',
            'smtp_port': 'smtp.port',
            'smtp_ssl': 'smtp.tls',
            'smtp_password': 'smtp.password',
            'email_from': 'smtp.email_from',
        }
        
        self.blacklist_for_save = set(["publisher_warranty_url", "load_language"])
        self.misc = {}
        self.db_misc = {}
        self.config_file = fname
        self.has_ssl = check_ssl()

        self._LOGLEVELS = dict([(getattr(netsvc, 'LOG_%s' % x), getattr(logging, x))
                          for x in ('CRITICAL', 'ERROR', 'WARNING', 'INFO', 'TEST', 'DEBUG', 'DEBUG_RPC', 'DEBUG_SQL', 'NOTSET')])

        version = "%s %s" % (release.description, release.version)
        self.parser = parser = optparse.OptionParser(version=version)

        parser.add_option("-c", "--config", dest="config", help="specify alternate config file")
        parser.add_option("-s", "--save", action="store_true", dest="save", default=False,
                          help="save configuration to ~/.openerp_serverrc")
        parser.add_option("--pidfile", dest="pidfile", help="file where the server pid will be stored")
        
        parser.add_option("-D", "--define", dest="defines", action="append", 
                    help="Define an arbitrary option, like 'section.var[=value]'")

        group = optparse.OptionGroup(parser, "XML-RPC Configuration")
        group.add_option("--httpd-interface", dest="httpd_interface", help="specify the TCP IP address for the XML-RPC protocol")
        group.add_option("--httpd-port", dest="httpd_port", help="specify the TCP port for the HTTP protocol", type="int")
        group.add_option("--no-httpd", dest="httpd", action="store_false", help="disable the HTTP protocol")
        parser.add_option_group(group)

        title = "HTTP Secure Configuration"
        if not self.has_ssl:
            title += " (disabled as ssl is unavailable)"

        group = optparse.OptionGroup(parser, title)
        group.add_option("--httpds-interface", dest="httpds_interface", help="specify the TCP IP address for the XML-RPC Secure protocol")
        group.add_option("--httpds-port", dest="httpds_port", help="specify the TCP port for the HTTP Secure protocol", type="int")
        group.add_option("--no-httpds", dest="httpds", action="store_false", help="disable the HTTP Secure protocol")
        group.add_option("--cert-file", dest="secure_cert_file", help="specify the certificate file for the SSL connection")
        group.add_option("--pkey-file", dest="secure_pkey_file", help="specify the private key file for the SSL connection")
        parser.add_option_group(group)

        group = optparse.OptionGroup(parser, "NET-RPC Configuration")
        group.add_option("--netrpc-interface", dest="netrpc_interface", help="specify the TCP IP address for the NETRPC protocol")
        group.add_option("--netrpc-port", dest="netrpc_port", help="specify the TCP port for the NETRPC protocol", type="int")
        group.add_option("--no-netrpc", dest="netrpc", action="store_false", help="disable the NETRPC protocol")
        parser.add_option_group(group)

        parser.add_option("-i", "--init", dest="init", help="init a module (use \"all\" for all modules)")
        parser.add_option("--without-demo", dest="without_demo",
                          help="load demo data for a module (use \"all\" for all modules)", default=False)
        parser.add_option("-u", "--update", dest="update",
                          help="update a module (use \"all\" for all modules)")
        parser.add_option("--cache-timeout", dest="cache_timeout",
                          help="set the timeout for the cache system", type="int")
        parser.add_option("-t", "--timezone", dest="timezone", help="specify reference timezone for the server (e.g. Europe/Brussels")

        # stops the server from launching after initialization
        parser.add_option("--stop-after-init", action="store_true", dest="stop_after_init", default=False,
                          help="stop the server after it initializes")
        parser.add_option('--debug', dest='debug_mode', action='store_true', default=False, help='enable debug mode')
        parser.add_option("--assert-exit-level", dest='assert_exit_level', type="choice", choices=self._LOGLEVELS.keys(),
                          help="specify the level at which a failed assertion will stop the server. Accepted values: %s" % (self._LOGLEVELS.keys(),))

        # Testing Group
        group = optparse.OptionGroup(parser, "Testing Configuration")
        group.add_option("--test-file", dest="test_file", help="Launch a YML test file.")
        group.add_option("--test-report-directory", dest="test_report_directory", help="If set, will save sample of all reports in this directory.")
        group.add_option("--test-disable", action="store_true", dest="test_disable",
                         default=False, help="Disable loading test files.")
        group.add_option("--test-commit", action="store_true", dest="test_commit",
                         default=False, help="Commit database changes performed by tests.")
        parser.add_option_group(group)

        # Logging Group
        group = optparse.OptionGroup(parser, "Logging Configuration")
        group.add_option("--logfile", dest="logfile", help="file where the server log will be stored")
        group.add_option("--no-logrotate", dest="logrotate", action="store_false",
                         help="do not rotate the logfile")
        group.add_option("--syslog", action="store_true", dest="syslog",
                         default=False, help="Send the log to the syslog server")
        group.add_option('--log-level', dest='log_level', type='choice', choices=self._LOGLEVELS.keys(),
                         help='specify the level of the logging. Accepted values: ' + str(self._LOGLEVELS.keys()))
        parser.add_option_group(group)

        # SMTP Group
        group = optparse.OptionGroup(parser, "SMTP Configuration")
        group.add_option('--email-from', dest='email_from', help='specify the SMTP email address for sending email')
        group.add_option('--smtp', dest='smtp_server', help='specify the SMTP server for sending email')
        group.add_option('--smtp-port', dest='smtp_port', help='specify the SMTP port', type="int")
        group.add_option('--smtp-ssl', dest='smtp_ssl', action='store_true', help='specify the SMTP server support SSL or not')
        group.add_option('--smtp-user', dest='smtp_user', help='specify the SMTP username for sending email')
        group.add_option('--smtp-password', dest='smtp_password', help='specify the SMTP password for sending email')
        parser.add_option_group(group)

        group = optparse.OptionGroup(parser, "Database related options")
        group.add_option("-d", "--database", dest="db_name", help="specify the database name")
        group.add_option("-r", "--db_user", dest="db_user", help="specify the database user name")
        group.add_option("-w", "--db_password", dest="db_password", help="specify the database password")
        group.add_option("--pg_path", dest="pg_path", help="specify the pg executable path")
        group.add_option("--db_host", dest="db_host", help="specify the database host")
        group.add_option("--db_port", dest="db_port", help="specify the database port", type="int")
        group.add_option("--db_maxconn", dest="db_maxconn", type='int',
                         help="specify the the maximum number of physical connections to posgresql")
        group.add_option("-P", "--import-partial", dest="import_partial",
                         help="Use this for big data importation, if it crashes you will be able to continue at the current state. Provide a filename to store intermediate importation states.", default=False)
        parser.add_option_group(group)

        group = optparse.OptionGroup(parser, "Internationalisation options",
            "Use these options to translate OpenERP to another language."
            "See i18n section of the user manual. Option '-d' is mandatory."
            "Option '-l' is mandatory in case of importation"
            )

        group.add_option('--load-language', dest="load_language",
                         help="specifies the languages for the translations you want to be loaded")
        group.add_option('-l', "--language", dest="language",
                         help="specify the language of the translation file. Use it with --i18n-export or --i18n-import")
        group.add_option("--i18n-export", dest="translate_out",
                         help="export all sentences to be translated to a CSV file, a PO file or a TGZ archive and exit")
        group.add_option("--i18n-import", dest="translate_in",
                         help="import a CSV or a PO file with translations and exit. The '-l' option is required.")
        group.add_option("--i18n-overwrite", dest="overwrite_existing_translations", action="store_true", default=False,
                         help="overwrites existing translation terms on importing a CSV or a PO file.")
        group.add_option("--modules", dest="translate_modules",
                         help="specify modules to export. Use in combination with --i18n-export")
        group.add_option("--addons-path", dest="addons_path",
                         help="specify an alternative addons path.",
                         action="callback", callback=self._check_addons_path, nargs=1, type="string")
        group.add_option("--osv-memory-count-limit", dest="osv_memory_count_limit", default=False,
                         help="Force a limit on the maximum number of records kept in the virtual "
                              "osv_memory tables. The default is False, which means no count-based limit.",
                         type="int")
        group.add_option("--osv-memory-age-limit", dest="osv_memory_age_limit", default=1.0,
                         help="Force a limit on the maximum age of records kept in the virtual "
                              "osv_memory tables. This is a decimal value expressed in hours, "
                              "and the default is 1 hour.",
                         type="float")
        parser.add_option_group(group)

        security = optparse.OptionGroup(parser, 'Security-related options')
        security.add_option('--no-database-list', action="store_false", dest='list_db', help="disable the ability to return the list of databases")
        parser.add_option_group(security)

    def parse_config(self):
        (opt, args) = self.parser.parse_args()

        def die(cond, msg):
            if cond:
                print msg
                sys.exit(1)

        die(bool(opt.syslog) and bool(opt.logfile),
            "the syslog and logfile options are exclusive")

        die(opt.translate_in and (not opt.language or not opt.db_name),
            "the i18n-import option cannot be used without the language (-l) and the database (-d) options")

        die(opt.overwrite_existing_translations and (not opt.translate_in),
            "the i18n-overwrite option cannot be used without the i18n-import option")

        die(opt.translate_out and (not opt.db_name),
            "the i18n-export option cannot be used without the database (-d) option")

        # Check if the config file exists (-c used, but not -s)
        die(not opt.save and opt.config and not os.path.exists(opt.config),
            "The config file '%s' selected with -c/--config doesn't exist, "\
            "use -s/--save if you want to generate it"%(opt.config))

        # place/search the config file on Win32 near the server installation
        # (../etc from the server)
        # if the server is run by an unprivileged user, he has to specify location of a config file where he has the rights to write,
        # else he won't be able to save the configurations, or even to start the server...
        if os.name == 'nt':
            rcfilepath = os.path.join(os.path.abspath(os.path.dirname(sys.argv[0])), 'openerp-server.conf')
        else:
            rcfilepath = os.path.expanduser('~/.openerp_serverrc')

        self.rcfile = os.path.abspath(
            self.config_file or opt.config \
                or os.environ.get('OPENERP_SERVER') or rcfilepath)
        self.load()


        # Verify that we want to log or not, if not the output will go to stdout
        if self.options['logfile'] in ('None', 'False'):
            self.options['logfile'] = False
        # the same for the pidfile
        if self.options['pidfile'] in ('None', 'False'):
            self.options['pidfile'] = False

        keys = [ 'db_name', 'db_user', 'db_password', 'db_host',
                'db_port', 'logfile', 'pidfile', 'cache_timeout',
                'db_maxconn', 'import_partial', 'addons_path',
                'syslog', 'without_demo', 'timezone',]

        for arg in keys:
            if getattr(opt, arg):
                self.options[arg] = getattr(opt, arg)

        keys = ['language', 'translate_out', 'translate_in', 'debug_mode',
                'stop_after_init', 'logrotate', 'without_demo', 'syslog',
                'list_db', 'test_report_directory' ]

        for arg in keys:
            if getattr(opt, arg) is not None:
                self.options[arg] = getattr(opt, arg)

        for key in self.aliases:
            if getattr(opt, key) is not None:
                sec, arg = self.aliases[key].split('.',1)
                self.misc.setdefault(sec,{})[arg] = getattr(opt, key)

        for dval in opt.defines or []:
            if '=' in dval:
                key, val = dval.split('=',1)
                if val in ('True', 'true'):
                    val = True
                if val in ('False', 'false'):
                    val = False
            else:
                key = dval
                val = True
            sec, arg = key.split('.',1)
            self.misc.setdefault(sec,{})[arg] = val

        if opt.assert_exit_level:
            self.options['assert_exit_level'] = self._LOGLEVELS[opt.assert_exit_level]
        else:
            self.options['assert_exit_level'] = self._LOGLEVELS.get(self.options['assert_exit_level']) or int(self.options['assert_exit_level'])

        if opt.log_level:
            self.options['log_level'] = self._LOGLEVELS[opt.log_level]
        else:
            self.options['log_level'] = self._LOGLEVELS.get(self.options['log_level']) or int(self.options['log_level'])

        if not self.options['root_path'] or self.options['root_path']=='None':
            self.options['root_path'] = os.path.abspath(os.path.dirname(sys.argv[0]))
        if not self.options['addons_path'] or self.options['addons_path']=='None':
            self.options['addons_path'] = os.path.join(self.options['root_path'], 'addons')

        self.options['init'] = opt.init and dict.fromkeys(opt.init.split(','), 1) or {}
        self.options["demo"] = not opt.without_demo and self.options['init'] or {}
        self.options["test-file"] =  opt.test_file
        self.options["test-disable"] =  opt.test_disable
        self.options["test-commit"] =  opt.test_commit
        self.options['update'] = opt.update and dict.fromkeys(opt.update.split(','), 1) or {}

        self.options['translate_modules'] = opt.translate_modules and map(lambda m: m.strip(), opt.translate_modules.split(',')) or ['all']
        self.options['translate_modules'].sort()

        if self.options['timezone']:
            # If an explicit TZ was provided in the config, make sure it is known
            try:
                import pytz
                pytz.timezone(self.options['timezone'])
            except pytz.UnknownTimeZoneError:
                die(True, "The specified timezone (%s) is invalid" % self.options['timezone'])
            except:
                # If pytz is missing, don't check the provided TZ, it will be ignored anyway.
                pass

        if opt.pg_path:
            self.options['pg_path'] = opt.pg_path

        if self.options.get('language', False):
            if len(self.options['language']) > 5:
                raise Exception('ERROR: The Lang name must take max 5 chars, Eg: -lfr_BE')

        if not self.options['db_user']:
            try:
                import getpass
                self.options['db_user'] = getpass.getuser()
            except:
                self.options['db_user'] = None

        die(not self.options['db_user'], 'ERROR: No user specified for the connection to the database')

        if self.options['db_password']:
            if sys.platform == 'win32' and not self.options['db_host']:
                self.options['db_host'] = 'localhost'
            #if self.options['db_host']:
            #    self._generate_pgpassfile()


        if self.misc.get('logging_levels', False):
            for (name, value) in self.misc['logging_levels'].items():
                if not value:
                    continue
                level = self._LOGLEVELS.get(value.lower()) or int(value)
                logging.getLogger(name).setLevel(level)

        if opt.save:
            self.save()

    def _generate_pgpassfile(self):
        """
        Generate the pgpass file with the parameters from the command line (db_host, db_user,
        db_password)

        Used because pg_dump and pg_restore can not accept the password on the command line.
        """
        is_win32 = sys.platform == 'win32'
        if is_win32:
            filename = os.path.join(os.environ['APPDATA'], 'pgpass.conf')
        else:
            filename = os.path.join(os.environ['HOME'], '.pgpass')

        text_to_add = "%(db_host)s:*:*:%(db_user)s:%(db_password)s" % self.options

        if os.path.exists(filename):
            content = [x.strip() for x in file(filename, 'r').readlines()]
            if text_to_add in content:
                return

        fp = file(filename, 'a+')
        fp.write(text_to_add + "\n")
        fp.close()

        if is_win32:
            try:
                import _winreg
            except ImportError:
                _winreg = None
            x=_winreg.ConnectRegistry(None,_winreg.HKEY_LOCAL_MACHINE)
            y = _winreg.OpenKey(x, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment", 0,_winreg.KEY_ALL_ACCESS)
            _winreg.SetValueEx(y,"PGPASSFILE", 0, _winreg.REG_EXPAND_SZ, filename )
            _winreg.CloseKey(y)
            _winreg.CloseKey(x)
        else:
            import stat
            os.chmod(filename, stat.S_IRUSR + stat.S_IWUSR)

    def _check_addons_path(self, option, opt, value, parser):
        """ Check the cmdline addons paths.
        
        Unlike the code in addons/__init__.py, where addons paths may be missing,
        command-line suplied paths must all be valid directories.
        """
        paths = []
        for res in map(str.strip, value.split(',')):
            res = os.path.abspath(os.path.expanduser(res))
            if not os.path.isdir(res):
                raise optparse.OptionValueError("option %s: no such directory: %r" % (opt, res))
            paths.append(res)
        setattr(parser.values, option.dest, ','.join(paths))

    def load(self):
        p = ConfigParser.ConfigParser()
        try:
            p.read([self.rcfile])
            for (name,value) in p.items('options'):
                if value=='True' or value=='true':
                    value = True
                if value=='False' or value=='false':
                    value = False
                self.options[name] = value
            #parse the other sections, as well
            for sec in p.sections():
                if sec == 'options':
                    continue
                dsec = None
                mm = self._db_sec_re.match(sec)
                if mm:
                    dsec = self.db_misc.setdefault(mm.group(2),{}).setdefault(mm.group(1), {})
                else:
                    dsec = self.misc.setdefault(sec, {})
                for (name, value) in p.items(sec):
                    if value=='True' or value=='true':
                        value = True
                    if value=='False' or value=='false':
                        value = False
                    dsec[name] = value
        except IOError:
            pass
        except ConfigParser.NoSectionError:
            pass

    def save(self):
        p = ConfigParser.ConfigParser()
        loglevelnames = dict(zip(self._LOGLEVELS.values(), self._LOGLEVELS.keys()))
        p.add_section('options')
        for opt in sorted(self.options.keys()):
            if opt in ('version', 'language', 'translate_out', 'translate_in',
                        'overwrite_existing_translations',
                        'stop_after_init', 'init', 'update'):
                continue
            if opt in self.blacklist_for_save:
                continue
            if opt in ('log_level', 'assert_exit_level'):
                p.set('options', opt, loglevelnames.get(self.options[opt], self.options[opt]))
            else:
                p.set('options', opt, self.options[opt])

        for sec in sorted(self.misc.keys()):
            p.add_section(sec)
            for opt in sorted(self.misc[sec].keys()):
                p.set(sec,opt,self.misc[sec][opt])

        for db in sorted(self.db_misc.keys()):
            ddic = self.db_misc[db]
            for dsec in sorted(ddic.keys()):
                sec = '%s "%s"' %(dsec, db)
                for opt in sorted(ddic[dsec].keys()):
                    p.set(sec, opt, ddic[dsec][opt])

        # try to create the directories and write the file
        try:
            rc_exists = os.path.exists(self.rcfile)
            if not rc_exists and not os.path.exists(os.path.dirname(self.rcfile)):
                os.makedirs(os.path.dirname(self.rcfile))
            try:
                p.write(file(self.rcfile, 'w'))
                if not rc_exists:
                    os.chmod(self.rcfile, 0600)
            except IOError:
                sys.stderr.write("ERROR: couldn't write the config file\n")

        except OSError:
            # what to do if impossible?
            sys.stderr.write("ERROR: couldn't create the config directory\n")

    def get(self, key, default=None):
        return self.options.get(key, default)

    def get_misc(self, sect, key, default=None):
        return self.misc.get(sect,{}).get(key, default)

    def get_misc_db(self, dbname, sect, key, default=None):
        ret = self.db_misc.get(dbname, {}).get(sect, {}).get(key, None)
        if ret is not None:
            return ret
        else:
            return self.misc.get(sect,{}).get(key, default)

    def __setitem__(self, key, value):
        self.options[key] = value

    def __getitem__(self, key):
        return self.options[key]

if 'openerp-server' in sys.modules['__main__'].__file__:
    # Following line should be called explicitly by the server
    # when it starts, to allow doing 'import tools.config' from
    # other python executables without parsing *their* args.
    config = configmanager()
    config.parse_config()
else:
    class dummy_confmanager(object):
        """ replace the configmanager with some all-empty dictionary

            In particular, for *.enable it will return False
        """
        def get_misc(self, a, b, c=None):
            if b == 'enable':
                return False
            return c

        def get_misc_db(self, a, b, c=None):
            if b == 'enable':
                return False
            return c

        def __getitem__(self, a):
            if a in ('addons_path', ):
                return ''
            if a in ('db_maxconn', ):
                return 0
            return None

        def get(self, a, c):
            return c

    config = dummy_confmanager()

#eof
