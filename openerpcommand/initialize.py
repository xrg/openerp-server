"""
Install OpenERP on a new (by default) database.
"""
import contextlib
import errno
import os
import sys
import time

import common

def install_openerp(database_name, create_database_flag, module_names, install_demo_data):
    import openerp
    config = openerp.tools.config

    if create_database_flag:
        with lock_file('/tmp/global_openerp_create_database.lock'):
            create_database(database_name)

    config['init'] = dict.fromkeys(module_names, 1)

    # Install the import hook, to import openerp.addons.<module>.
    openerp.modules.module.initialize_sys_path()

    registry = openerp.modules.registry.RegistryManager.get(
        database_name, update_module=True, force_demo=install_demo_data)

    return registry

# From http://code.activestate.com/recipes/576572/
@contextlib.contextmanager
def lock_file(path, wait_delay=.1, max_try=600):
    attempt = 0
    while True:
        attempt += 1
        if attempt > max_try:
            raise IOError("Could not lock file %s." % path)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except OSError, e:
            if e.errno != errno.EEXIST:
                raise
            time.sleep(wait_delay)
            continue
        else:
            break
    try:
        yield fd
    finally:
        os.close(fd)
        os.unlink(path)

# TODO turn template1 in a parameter
# This should be exposed from openerp (currently in
# openerp/service/web_services.py).
def create_database(database_name):
    import openerp
    db = openerp.sql_db.db_connect('template1')
    cr = db.cursor() # TODO `with db as cr:`
    try:
        cr.autocommit(True)
        cr.execute("""CREATE DATABASE "%s"
            ENCODING 'unicode' TEMPLATE "template1" """ \
            % (database_name,))
    finally:
        cr.close()

def run(args):
    assert args.database
    assert not (args.module and args.all_modules)

    import openerp

    config = openerp.tools.config

    if args.tests:
        config['log_handler'] = [':INFO']
        config['test_enable'] = True
        config['without_demo'] = False
    else:
        config['log_handler'] = [':CRITICAL']
        config['test_enable'] = False
        config['without_demo'] = True

    if args.addons:
        args.addons = args.addons.split(':')
    else:
        args.addons = []
    config['addons_path'] = ','.join(args.addons)

    if args.all_modules:
        module_names = common.get_addons_from_paths(args.addons, args.exclude)
    elif args.module:
        module_names = args.module
    else:
        module_names = ['base']

    if args.coverage:
        import coverage
        # Without the `include` kwarg, coverage generates 'memory:0xXXXXX'
        # filenames (which do not exist) and cause it to crash. No idea why.
        cov = coverage.coverage(branch=True, include='*.py')
        cov.start()
    openerp.netsvc.init_logger()
    registry = install_openerp(args.database, not args.no_create, module_names, not config['without_demo'])
    if args.coverage:
        cov.stop()
        cov.html_report(directory='coverage')
        # If we wanted the report on stdout:
        # cov.report()

    # The `_assertion_report` attribute was added on the registry during the
    # OpenERP 7.0 development.
    if hasattr(registry, '_assertion_report'):
        sys.exit(1 if registry._assertion_report.failures else 0)

def add_parser(subparsers):
    parser = subparsers.add_parser('initialize',
        description='Create and initialize a new OpenERP database.')
    parser.add_argument('-d', '--database', metavar='DATABASE',
        **common.required_or_default('DATABASE', 'the database to create'))
    common.add_addons_argument(parser)
    parser.add_argument('--module', metavar='MODULE', action='append',
        help='specify a module to install'
        ' (this option can be repeated)')
    parser.add_argument('--all-modules', action='store_true',
        help='install all visible modules (not compatible with --module)')
    parser.add_argument('--no-create', action='store_true',
        help='do not create the database, only initialize it')
    parser.add_argument('--exclude', metavar='MODULE', action='append',
        help='exclude a module from installation'
        ' (this option can be repeated)')
    parser.add_argument('--tests', action='store_true',
        help='run the tests as modules are installed'
        ' (use the `run-tests` command to choose specific'
        ' tests to run against an existing database).'
        ' Demo data are installed.')
    parser.add_argument('--coverage', action='store_true',
        help='report code coverage (particularly useful with --tests).'
        ' The report is generated in a coverage directory and you can'
        ' then point your browser to coverage/index.html.')

    parser.set_defaults(run=run)
