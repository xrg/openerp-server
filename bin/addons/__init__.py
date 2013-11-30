# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2010-2011 OpenERP s.a. (<http://openerp.com>).
#    Copyright (C) 2009,2011-2013 P.Christeas <xrg@hellug.gr>
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

#.apidoc title: Addons support

""" Support for loading and managing OpenERP addons (modules)

    Note: this is _not_ the addons themselves, but the code which supports loading
    the OpenERP addons.
"""

import os, sys, imp
from os.path import join as opj
import itertools
import logging
import zipimport

import osv
import tools
import tools.osutil
from tools import sql_model
from tools.safe_eval import safe_eval as eval
import pooler
from tools.translate import _

from tools.data_loaders import DataLoader
import tools.loaders

import zipfile
import release

import re
import base64
from zipfile import PyZipFile, ZIP_DEFLATED
from cStringIO import StringIO

logger = logging.getLogger('init')

_ad = os.path.abspath(opj(tools.ustr(tools.config['root_path']), u'addons'))     # default addons path (base)
ad_paths= map(lambda m: os.path.abspath(tools.ustr(m.strip())), tools.config['addons_path'].split(','))

sys.path.insert(1, _ad)

ad_cnt=1
for adp in ad_paths:
    if adp != _ad:
        sys.path.insert(ad_cnt, adp)
        ad_cnt+=1

ad_paths.append(_ad)    # for get_module_path

#: Modules already loaded
loaded = []

class Graph(dict):
    """ Holds the hierarchical structure of the addons (nodes)
    """

    def addNode(self, name, deps):
        max_depth, father = 0, None
        for n in [Node(x, self) for x in deps]:
            if n.depth >= max_depth:
                father = n
                max_depth = n.depth
        if father:
            father.addChild(name)
        else:
            Node(name, self)

    def update_from_db(self, cr):
        """Update the graph with values from the database (if exist)
        """
        if not len(self):
            return
        ## First, we set the default values for each package in graph
        additional_data = dict.fromkeys(self.keys(), {'id': 0, 'state': 'uninstalled', 'dbdemo': False, 'installed_version': None})
        ## Then we get the values from the database
        cr.execute('SELECT name, id, state, demo AS dbdemo, latest_version AS installed_version'
                   '  FROM ir_module_module'
                   ' WHERE name IN %s',(tuple(additional_data),)
                   )

        ## and we update the default values with values from the database
        additional_data.update(dict([(x.pop('name'), x) for x in cr.dictfetchall()]))

        for package in self.values():
            for k, v in additional_data[package.name].items():
                setattr(package, k, v)

    def __iter__(self):
        level = 0
        done = set(self.keys())
        while done:
            level_modules = [(name, module) for name, module in self.items() if module.depth==level]
            for name, module in level_modules:
                done.remove(name)
                yield module
            level += 1

class Singleton(object):
    def __new__(cls, name, graph):
        if name in graph:
            inst = graph[name]
        else:
            inst = object.__new__(cls)
            inst.name = name
            graph[name] = inst
        return inst


class Node(Singleton):

    def __init__(self, name, graph):
        self.graph = graph
        if not hasattr(self, 'children'):
            self.children = []
        if not hasattr(self, 'depth'):
            self.depth = 0

    def addChild(self, name):
        node = Node(name, self.graph)
        node.depth = self.depth + 1
        if node not in self.children:
            self.children.append(node)
        for attr in ('init', 'update', 'demo'):
            if hasattr(self, attr):
                setattr(node, attr, True)
        self.children.sort(lambda x, y: cmp(x.name, y.name))

    def __setattr__(self, name, value):
        super(Node, self).__setattr__(name, value)
        if name in ('init', 'update', 'demo'):
            tools.config[name][self.name] = 1
            for child in self.children:
                setattr(child, name, value)
        if name == 'depth':
            for child in self.children:
                setattr(child, name, value + 1)

    def __iter__(self):
        return itertools.chain(iter(self.children), *map(iter, self.children))

    def __str__(self):
        return self._pprint()

    def _pprint(self, depth=0):
        s = '%s\n' % self.name
        for c in self.children:
            s += '%s`-> %s' % ('   ' * depth, c._pprint(depth+1))
        return s


def get_module_path(module, downloaded=False):
    """Return the path of the given module
    """
    for adp in ad_paths:
        if os.path.exists(opj(adp, module)) or os.path.exists(opj(adp, '%s.zip' % module)):
            return opj(adp, module)

    if downloaded:
        return opj(_ad, module)
    logger.warning('module %s: module not found' % (module,))
    return False


def get_module_filetree(module, dir='.'):
    path = get_module_path(module)
    if not path:
        return False

    dir = os.path.normpath(dir)
    if dir == '.':
        dir = ''
    if dir.startswith('..') or (dir and dir[0] == '/'):
        raise Exception('Cannot access file outside the module')

    if not os.path.isdir(path):
        # zipmodule
        zip = zipfile.ZipFile(path + ".zip")
        files = ['/'.join(f.split('/')[1:]) for f in zip.namelist()]
    else:
        files = tools.osutil.listdir(path, True)

    tree = {}
    for f in files:
        if not f.startswith(dir):
            continue

        if dir:
            f = f[len(dir)+int(not dir.endswith('/')):]
        lst = f.split(os.sep)
        current = tree
        while len(lst) != 1:
            current = current.setdefault(lst.pop(0), {})
        current[lst.pop(0)] = None

    return tree

def zip_directory(directory, b64enc=True, src=True):
    """Compress a directory

    @param directory The directory to compress
    @param base64enc if True the function will encode the zip file with base64
    @param src Integrate the source files

    @return a string containing the zip file
    """

    RE_exclude = re.compile('(?:^\..+\.swp$)|(?:\.py[oc]$)|(?:\.bak$)|(?:\.~.~$)', re.I)

    def _zippy(archive, path, src=True):
        path = os.path.abspath(path)
        base = os.path.basename(path)
        for f in tools.osutil.listdir(path, True):
            bf = os.path.basename(f)
            if not RE_exclude.search(bf) and (src or bf in ('__openerp__.py', '__terp__.py') or not bf.endswith('.py')):
                archive.write(os.path.join(path, f), os.path.join(base, f))

    archname = StringIO()
    archive = PyZipFile(archname, "w", ZIP_DEFLATED)

    # for Python 2.5, ZipFile.write() still expects 8-bit strings (2.6 converts to utf-8)
    directory = tools.ustr(directory).encode('utf-8')

    archive.writepy(directory)
    _zippy(archive, directory, src=src)
    archive.close()
    archive_data = archname.getvalue()
    archname.close()

    if b64enc:
        return base64.encodestring(archive_data)

    return archive_data

def get_module_as_zip(modulename, b64enc=True, src=True):
    """Generate a module as zip file with the source or not and can do a base64 encoding

    @param modulename The module name
    @param b64enc if True the function will encode the zip file with base64
    @param src Integrate the source files

    @return a stream to store in a file-like object
    """

    ap = get_module_path(str(modulename))
    if not ap:
        raise Exception('Unable to find path for module %s' % modulename)

    ap = ap.encode('utf8')
    if os.path.isfile(ap + '.zip'):
        val = file(ap + '.zip', 'rb').read()
        if b64enc:
            val = base64.encodestring(val)
    else:
        val = zip_directory(ap, b64enc, src)

    return val


def get_module_resource(module, *args):
    """Return the full path of a resource of the given module.

    @param module the module
    @param args the resource path components

    @return absolute path to the resource
    """
    a = get_module_path(module)
    if not a:
        return False
    res = opj(a, *args)
    if os.path.exists(res):
        return res
    elif zipfile.is_zipfile( a +'.zip') :
        zip = zipfile.ZipFile( a + ".zip")
        files = ['/'.join(f.split('/')[1:]) for f in zip.namelist()]
        resource_path = '/'.join(args)
        if resource_path in files:
            return opj(a, resource_path)
    return False

def get_modules():
    """Returns the list of module names
    
        This scans in all addons paths and returns the possible modules,
        regardless of database or module descriptions.
    """
    def listdir(dir):
        def clean(name):
            name = os.path.basename(name)
            if name[-4:] == '.zip':
                name = name[:-4]
            return name

        def is_really_module(name):
            if name.startswith('.'):
                return False
            name = opj(dir, name)
            return os.path.isdir(name) or zipfile.is_zipfile(name)
        return map(clean, filter(is_really_module, os.listdir(dir)))

    plist = []
    for ad in ad_paths:
        plist.extend(listdir(ad))
    return list(set(plist))

def load_information_from_description_file(module):
    """ Read the module description as a dict
    :param module: The name of the module (sale, purchase, ...)
    """

    for filename in ['__openerp__.py', '__terp__.py']:
        description_file = get_module_resource(module, filename)
        if description_file :
            desc_f = tools.file_open(description_file)
            try:
                return eval(desc_f.read())
            finally:
                desc_f.close()

    logging.getLogger('addons').debug('The module %s does not contain a description file:'\
                                      '__openerp__.py or __terp__.py (deprecated)', module)
    return {}

def get_modules_with_version():
    modules = get_modules()
    res = {}
    for module in modules:
        try:
            info = load_information_from_description_file(module)
            res[module] = "%s.%s" % (release.major_version, info['version'])
        except Exception:
            continue
    return res

def create_graph(cr, module_list, force=None):
    graph = Graph()
    upgrade_graph(graph, cr, module_list, force)
    return graph

def upgrade_graph(graph, cr, module_list, force=None):
    if force is None:
        force = []
    packages = []
    len_graph = len(graph)
    for module in module_list:
        mod_path = get_module_path(module)
        terp_file = get_module_resource(module, '__openerp__.py')
        if not terp_file:
            terp_file = get_module_resource(module, '__terp__.py')
        if not mod_path or not terp_file:
            logger.warning('module %s: not installable' % (module))
            cr.execute("update ir_module_module set state=%s where name=%s", ('uninstallable', module))
            continue

        if os.path.isfile(terp_file) or zipfile.is_zipfile(mod_path+'.zip'):
            terp_f = tools.file_open(terp_file)
            try:
                info = eval(terp_f.read())
            except Exception:
                logger.error('module %s: eval file %s' % (module, terp_file))
                raise
            finally:
                terp_f.close()
            if info.get('installable', True):
                api_missing = []
                if 'api_depends' in info:
                    for apidep in info['api_depends']:
                        if apidep not in release.api_options \
                                and apidep not in release.server_options:
                            api_missing.append(apidep)
                if not api_missing:
                    packages.append((module, info.get('depends', []), info))
                else:
                    logger.error('module %s: not installable, because this %s server doesn\'t provide %s',
                            module, release.version, ', '.join(api_missing))
            else:
                logger.warning('module %s: not installable, skipped', module)
    if not packages:
        return False
    dependencies = dict([(p, deps) for p, deps, data in packages])
    current, later = set([p for p, dep, data in packages]), set()

    while packages and current > later:
        package, deps, data = packages[0]

        # if all dependencies of 'package' are already in the graph, add 'package' in the graph
        if reduce(lambda x, y: x and y in graph, deps, True):
            if not package in current:
                packages.pop(0)
                continue
            later.clear()
            current.remove(package)
            graph.addNode(package, deps)
            node = Node(package, graph)
            node.data = data
            for kind in ('init', 'demo', 'update'):
                if package in tools.config[kind] or 'all' in tools.config[kind] or kind in force:
                    setattr(node, kind, True)
        else:
            later.add(package)
            packages.append((package, deps, data))
        packages.pop(0)

    graph.update_from_db(cr)

    for package in later:
        unmet_deps = filter(lambda p: p not in graph, dependencies[package])
        logger.error('module %s: Unmet dependencies: %s' % (package, ', '.join(unmet_deps)))

    result = len(graph) - len_graph
    if result != len(module_list):
        logger.warning('Not all modules have loaded. %d/%d', result, len(module_list))
        mmods = filter( lambda p: p not in graph, module_list)
        logger.info("Missing modules: %s", ', '.join(mmods))
    else:
        logger.info("Successfully loaded all %d modules", result)
    return result


def init_module_objects(cr, module_name, obj_list):
    logger.info('module %s: creating or updating database tables' % module_name)
    todo = []
    schema = sql_model.Schema() # TODO: once per graph!
    schema.hints['tables'].append('res_users') # always needed, for implicit fields
    context = {'module': module_name}
    for obj in obj_list:
        obj._auto_init_prefetch(schema, context=context)
    
    logger.debug("Loading existing elements from db: %r" , schema.hints)
    schema.load_from_db(cr)
    wf_engine = None
    
    for obj in obj_list:
        try:
            context = {'module': module_name} # is it safe to move up?
            # should we have a savepoint ?
            obj._field_model2db(cr, context=context)
            result = obj._auto_init_sql(schema, context=context)
            if result:
                todo += result

            # we need only commit the sql model if the object overrides
            # its _auto_init (compatibility mode)
            if not (getattr(obj._auto_init, 'deferrable', False)):
                logger.debug("Commit schema before %s._auto_init()", obj._name)
                schema.commit_to_db(cr)
            result = obj._auto_init(cr, context=context )
        except Exception:
            logger.error("Schema at time of exception: \n%s", schema.pretty_print(todo_only=True))
            raise
        if result:
            todo += result
        if hasattr(obj, 'init'):
            schema.commit_to_db(cr)
            if not obj._workflow:
                if not wf_engine:
                    wf_engine = osv.osv.netsvc.LocalService('workflow')
                obj._workflow = wf_engine.init_dummy(cr, obj)
            obj.init(cr)

    # print "TODO(last):"
    # print schema._dump_todo()
    schema.commit_to_db(cr)
    if todo:
        if not wf_engine:
            wf_engine = osv.osv.netsvc.LocalService('workflow')
        wf_engine.thaw_dummy(cr)
        todo.sort()
        for t in todo:
            t[1](cr, *t[2])
    cr.commit()


def register_class(m):
    """ Register module named m, if not already registered
    """

    global loaded
    if m in loaded:
        return
    logger.info('module %s: registering objects' % m)
    mod_path = get_module_path(m)

    try:
        zip_mod_path = mod_path + '.zip'
        if not os.path.isfile(zip_mod_path):
            fm = imp.find_module(m, ad_paths)
            try:
                imp.load_module(m, *fm)
            finally:
                if fm[0]:
                    fm[0].close()
        else:
            zimp = zipimport.zipimporter(zip_mod_path)
            zimp.load_module(m)
    except zipimport.ZipImportError:
        logger.exception("Couldn't load zip module %s" % (m,))
        raise
    except Exception:
        logger.exception("Couldn't load module %s" % (m,))
        raise
    else:
        loaded.append(m)


class MigrationManager(object):
    """
        This class manage the migration of modules
        Migrations files must be python files containing a "migrate(cr, installed_version)" function.
        Theses files must respect a directory tree structure: A 'migrations' folder which containt a
        folder by version. Version can be 'module' version or 'server.module' version (in this case,
        the files will only be processed by this version of the server). Python file names must start
        by 'pre' or 'post' and will be executed, respectively, before and after the module initialisation
        
        Example::

            <moduledir>
            `-- migrations
                |-- 1.0
                |   |-- pre-update_table_x.py
                |   |-- pre-update_table_y.py
                |   |-- post-clean-data.py
                |   `-- README.txt              # not processed
                |-- 5.0.1.1                     # files in this folder will be executed only on a 5.0 server
                |   |-- pre-delete_table_z.py
                |   `-- post-clean-data.py
                `-- foo.py                      # not processed

        This similar structure is generated by the maintenance module with the migrations files get by
        the maintenance contract

    """
    def __init__(self, cr, graph):
        self.cr = cr
        self.graph = graph
        self.migrations = {}
        self._get_files()

    def _get_files(self):
        """
        import addons.base.maintenance.utils as maintenance_utils
        maintenance_utils.update_migrations_files(self.cr)
        """

        for pkg in self.graph:
            self.migrations[pkg.name] = {}
            if not (hasattr(pkg, 'update') or pkg.state == 'to upgrade'):
                continue

            self.migrations[pkg.name]['module'] = get_module_filetree(pkg.name, 'migrations') or {}
            self.migrations[pkg.name]['maintenance'] = get_module_filetree('base', 'maintenance/migrations/' + pkg.name) or {}

    def migrate_module(self, pkg, stage):
        assert stage in ('pre', 'post')
        stageformat = {'pre': '[>%s]',
                       'post': '[%s>]',
                      }
        
        mlog = logging.getLogger('migration')

        if not (hasattr(pkg, 'update') or pkg.state == 'to upgrade'):
            return

        def convert_version(version):
            if version.startswith(release.major_version) and version != release.major_version:
                return version  # the version number already containt the server version
            return "%s.%s" % (release.major_version, version)

        def _get_migration_versions(pkg):
            def __get_dir(tree):
                return [d for d in tree if tree[d] is not None]

            versions = list(set(
                __get_dir(self.migrations[pkg.name]['module']) +
                __get_dir(self.migrations[pkg.name]['maintenance'])
            ))
            versions.sort(key=lambda k: parse_version(convert_version(k)))
            return versions

        def _get_migration_files(pkg, version, stage):
            """ return a list of tuple (module, file)
            """
            m = self.migrations[pkg.name]
            lst = []

            mapping = {'module': opj(pkg.name, 'migrations'),
                       'maintenance': opj('base', 'maintenance', 'migrations', pkg.name),
                      }

            for x in mapping.keys():
                if version in m[x]:
                    for f in m[x][version]:
                        if m[x][version][f] is not None:
                            continue
                        if not f.startswith(stage + '-'):
                            continue
                        lst.append(opj(mapping[x], version, f))
            lst.sort()
            return lst

        def mergedict(a, b):
            a = a.copy()
            a.update(b)
            return a

        from tools.parse_version import parse_version

        parsed_installed_version = parse_version(pkg.installed_version or '')
        current_version = parse_version(convert_version(pkg.data.get('version', '0')))

        versions = _get_migration_versions(pkg)

        for version in versions:
            if parsed_installed_version < parse_version(convert_version(version)) <= current_version:

                strfmt = {'addon': pkg.name,
                          'stage': stage,
                          'version': stageformat[stage] % version,
                          }

                for pyfile in _get_migration_files(pkg, version, stage):
                    name, ext = os.path.splitext(os.path.basename(pyfile))
                    if ext.lower() != '.py':
                        continue
                    mod = fp = fp2 = None
                    try:
                        mlog.info("module %s: Loading migration file %s", pkg.name, pyfile)
                        fp = tools.file_open(pyfile)

                        # imp.load_source need a real file object, so we create
                        # one from the file-like object we get from file_open
                        fp2 = os.tmpfile()
                        fp2.write(fp.read())
                        fp2.seek(0)
                        try:
                            mod = imp.load_source(name, pyfile, fp2)
                            mlog.info('module %(addon)s: Running migration %(version)s %(name)s' % mergedict({'name': mod.__name__}, strfmt))
                            mod.migrate(self.cr, pkg.installed_version)
                        except ImportError:
                            mlog.error('module %(addon)s: Unable to load %(stage)s-migration file %(file)s' % mergedict({'file': pyfile}, strfmt))
                            raise
                        except AttributeError:
                            mlog.exception('module %(addon)s: Each %(stage)s-migration file must have a "migrate(cr, installed_version)" function' % strfmt)
                        except Exception:
                            raise
                    finally:
                        if fp:
                            fp.close()
                        if fp2:
                            fp2.close()
                        if mod:
                            del mod

class ModuleDataLoader(object):
    """ Context-holding engine for all data file formats
    """
    def __init__(self, pool, uid, module_name, context=None, kwargs=None):
        self.idref = {}
        self.pool = pool
        self.uid = uid
        self.module_name = module_name
        self.context = context or {'__ignore_ir_values': True}
        self.kwargs = kwargs

    def process_files(self, cr, file_list, mode, noupdate=False):
        """
            @param file_list is the one like 'init_xml', 'update_xml' etc.
                from `__openerp__.py`
        """
        if not file_list: # this also handles non-list values
            return
        _formats = {}
        for filename in file_list:
            logger.info('module %s: loading %s' % (self.module_name, filename))
            b, ext = os.path.splitext(filename)
            if not ext.startswith('.'):
                logger.error('module %s: cannot load %s because its extension is not recognized!',
                            self.module_name, filename)
                continue
            ext = ext[1:]
            if ext not in _formats:
                try:
                    _formats[ext] = DataLoader[ext](self.pool, self.uid,
                                self.module_name, self.idref, mode, noupdate,
                                self.context, **(self.kwargs))
                except TypeError:
                    logger.error('module %s: cannot load %s because its format is not recognized!',
                            self.module_name, filename)
                    continue
                except Exception:
                    logger.error('module %s: cannot load %s , parser failed',
                            self.module_name, filename, exc_info=True)
                    continue
            parser = _formats[ext]
            fp = tools.file_open(opj(self.module_name, filename))
            try:
                parser.parse(cr, filename, fp)
            except Exception:
                cr.rollback()
                tools.cache.clean_caches_for_db(cr.dbname)
                raise
            finally:
                fp.close()

def load_module_graph(cr, graph, status=None, perform_checks=True, skip_modules=None, **kwargs):
    """ Migrates+Updates or Installs all module nodes from ``graph``
    
       :param graph: graph of module nodes to load
       :param status: status dictionary for keeping track of progress
       :param perform_checks: whether module descriptors should be checked for validity (prints warnings
                              for same cases, and even raise osv_except if certificate is invalid)
       :param skip_modules: optional list of module names (packages) which have previously been loaded and can be skipped
       :return: list of modules that were installed or updated
    """

    # **kwargs is passed directly to convert_xml_import
    if not status:
        status = {}

    status = status.copy()
    processed_modules = []
    statusi = 0
    pool = pooler.get_pool(cr.dbname)
    migrations = MigrationManager(cr, graph)
    wf_engine = osv.osv.netsvc.LocalService('workflow')
    modobj = None
    logger.debug('loading %d packages..' % len(graph))
    abstract_models = []

    wf_engine.freeze(cr)

    for package in graph:
        if skip_modules and package.name in skip_modules:
            continue
        logger.info('module %s: loading objects' % package.name)
        migrations.migrate_module(package, 'pre')
        register_class(package.name)
        modules = pool.instanciate(package.name, cr)
        for m in modules:
            # Discover all abstract models (of all modules)
            if isinstance(m, osv.orm.orm_abstract):
                abstract_models.append(m)
        if hasattr(package, 'init') or hasattr(package, 'update') or package.state in ('to install', 'to upgrade'):
            init_module_objects(cr, package.name, modules)

            for am in abstract_models:
                # check that new models satisfy their abstract definitions
                # Note that new models may /not/ have the _implements flag,
                # yet need to implement an abstract through their _inherits.
                am._verify_implementations(modules)

        wf_engine.reload_models(cr, [m._name for m in modules])
        cr.commit()

    del abstract_models

    for package in graph:
        status['progress'] = (float(statusi)+0.1) / len(graph)
        m = package.name
        mid = package.id

        if skip_modules and m in skip_modules:
            continue

        if modobj is None:
            modobj = pool.get('ir.module.module')

        if modobj and perform_checks:
            modobj.check(cr, 1, [mid])

        status['progress'] = (float(statusi)+0.4) / len(graph)

        mode = 'update'
        if hasattr(package, 'init') or package.state == 'to install':
            mode = 'init'

        if hasattr(package, 'init') or hasattr(package, 'update') \
                    or package.state in ('to install', 'to upgrade'):
            wf_engine.thaw_dummy(cr)
            mLoader = ModuleDataLoader(pool, uid=1, module_name=m, kwargs=kwargs)
            for kind in ('init', 'update'):
                if package.state == 'to upgrade':
                    # upgrading the module information
                    modobj.write(cr, 1, [mid], modobj.get_values_from_terp(package.data))
                mLoader.process_files(cr,package.data.get('%s_xml' % kind, []), mode, noupdate=False)
            mLoader.process_files(cr, package.data.get('data', []), mode, noupdate=False)

            if hasattr(package, 'demo') or (package.dbdemo and package.state != 'installed'):
                status['progress'] = (float(statusi)+0.75) / len(graph)
                wf_engine.thaw(cr) # activate workflows, before we put any data
                demo_files = package.data.get('demo', [])
                demo_files += package.data.get('demo_xml', [])
                mLoader.process_files(cr, demo_files, mode, noupdate=True)
                cr.execute('update ir_module_module set demo=%s where id=%s', (True, mid))

                # launch tests only in demo mode, as most tests will depend
                # on demo data. Other tests can be added into the regular
                # 'data' section, but should probably not alter the data,
                # as there is no rollback.
                if tools.config.get_misc('tests','enable',True):
                    """ Load and execute install-time tests.

                        Normally, all that data should never be committed into the
                        db, so this fn will almost be a no-op (except that it commits
                        the cursor).
                    """
                    cr.commit()
                    try:
                        mLoader.process_files(cr, package.data.get('test',[]), mode, noupdate=True)
                    except Exception, e:
                        if tools.config.get_misc('tests', 'nonfatal', False):
                            logger.warn(e)
                            pass
                        else:
                            raise
                    finally:
                        if tools.config.get_misc('tests','rollback', True):
                            cr.rollback()
                        else:
                            cr.commit()
                wf_engine.freeze(cr)

            processed_modules.append(package.name)

            migrations.migrate_module(package, 'post')

            if modobj:
                ver = release.major_version + '.' + package.data.get('version', '0.1')
                # Set new modules and dependencies
                modobj.write(cr, 1, [mid], {'state': 'installed', 'latest_version': ver})
                cr.commit()
                # Update translations for all installed languages
                modobj.update_translations(cr, 1, [mid], None)
                cr.commit()

            package.state = 'installed'
            for kind in ('init', 'demo', 'update'):
                if hasattr(package, kind):
                    delattr(package, kind)

        statusi += 1

    DataLoader.unload_all()
    wf_engine.thaw(cr)
    cr.commit()

    return processed_modules

def _check_module_names(cr, module_names):
    mod_names = set(module_names)
    if 'base' in mod_names:
        # ignore dummy 'all' module
        if 'all' in mod_names:
            mod_names.remove('all')
    if mod_names:
        cr.execute("SELECT count(id) AS count FROM ir_module_module WHERE name in %s", (tuple(mod_names),))
        if cr.dictfetchone()['count'] != len(mod_names):
            # find out what module name(s) are incorrect:
            cr.execute("SELECT name FROM ir_module_module")
            incorrect_names = mod_names.difference([x['name'] for x in cr.dictfetchall()])
            logger.warning('invalid module names, ignored: %s', ", ".join(incorrect_names))

def load_modules(db, force_demo=False, status=None, update_module=False, languages=False):
    if not status:
        status = {}

    cr = db.cursor()
    if cr:
        # Check if this core table exists. If not, do first-time DB initialisation
        cr.execute("SELECT relname FROM pg_class WHERE relkind='r' AND relname='ir_module_module'")
        if len(cr.fetchall())==0:
            logger.info("init db")
            tools.init_db(cr)
            tools.config["init"]["all"] = 1
            tools.config['update']['all'] = 1
            if not tools.config['without_demo']:
                tools.config["demo"]['all'] = 1
    force = []
    if force_demo:
        force.append('demo')

    # This is a brand new pool, just created in pooler.get_db_and_pool()
    pool = pooler.get_pool(cr.dbname)

    if languages:
        # the first of the languages to load is the "official" one, that
        # res_lang.init_lang() will use
        pool._init_values['lang'] = languages.pop(0)

    try:
        processed_modules = []
        report = tools.convert.assertion_report()
        # NOTE: Try to also load the modules that have been marked as uninstallable previously...
        STATES_TO_LOAD = ['installed', 'to upgrade', 'uninstallable']
        if 'base' in tools.config['update'] or 'all' in tools.config['update']:
            cr.execute("update ir_module_module set state=%s where name=%s and state=%s", ('to upgrade', 'base', 'installed'))

        # STEP 1: LOAD BASE (must be done before module dependencies can be computed for later steps) 
        graph = create_graph(cr, ['base'], force)
        if not graph:
            logger.critical('module base cannot be loaded! (hint: verify addons-path)')
            raise osv.osv.except_osv(_('Could not load base module'), _('module base cannot be loaded! (hint: verify addons-path)'))
        processed_modules.extend(load_module_graph(cr, graph, status, perform_checks=(not update_module), report=report))

        if languages:
            for lang in languages:
                tools.load_language(cr, lang)

        # STEP 2: Mark other modules to be loaded/updated
        if update_module:
            modobj = pool.get('ir.module.module')
            logger.info('updating modules list')
            if ('base' in tools.config['init']) or ('base' in tools.config['update']):
                modobj.update_list(cr, 1)

            _check_module_names(cr, itertools.chain(tools.config['init'].keys(), tools.config['update'].keys()))

            mods = [k for k in tools.config['init'] if tools.config['init'][k]]
            if mods:
                ids = modobj.search(cr, 1, ['&', ('state', '=', 'uninstalled'), ('name', 'in', mods)])
                if ids:
                    modobj.button_install(cr, 1, ids)

            mods = [k for k in tools.config['update'] if tools.config['update'][k]]
            if mods:
                ids = modobj.search(cr, 1, ['&', ('state', '=', 'installed'), ('name', 'in', mods)])
                if ids:
                    modobj.button_upgrade(cr, 1, ids)

            cr.execute("UPDATE ir_module_module SET state=%s WHERE name=%s", ('installed', 'base'))

            STATES_TO_LOAD += ['to install']


        # STEP 3: Load marked modules (skipping base which was done in STEP 1)
        loop_guardrail = 0
        while True:
            loop_guardrail += 1
            if loop_guardrail > 100:
                raise ValueError('Possible recursive module tree detected, aborting.')
            cr.execute("SELECT name FROM ir_module_module WHERE state IN %s" ,(tuple(STATES_TO_LOAD),))

            module_list = [name for (name,) in cr.fetchall() if name not in graph]
            if not module_list:
                break

            new_modules_in_graph = upgrade_graph(graph, cr, module_list, force)
            if new_modules_in_graph == 0:
                # nothing to load
                break

            logger.debug('Updating graph with %d more modules' % (len(module_list)))
            processed_modules.extend(load_module_graph(cr, graph, status, report=report, skip_modules=processed_modules))

        # load custom models
        cr.execute('SELECT model FROM ir_model WHERE state=%s', ('manual',))
        for model in cr.dictfetchall():
            pool.get('ir.model').instanciate(cr, 1, model['model'], {})

        # STEP 4: Finish and cleanup
        if processed_modules:
            cr.execute("""SELECT model,name FROM ir_model WHERE id NOT IN (SELECT DISTINCT model_id FROM ir_model_access)""")
            for (model, name) in cr.fetchall():
                model_obj = pool.get(model)
                if model_obj and not isinstance(model_obj, (osv.osv.osv_memory, osv.orm.orm_deprecated)):
                    logger.warning('object %s (%s) has no access rules!' % (model, name))

            # Temporary warning while we remove access rights on osv_memory objects, as they have
            # been replaced by owner-only access rights
            cr.execute("""select distinct mod.model, mod.name from ir_model_access acc, ir_model mod where acc.model_id = mod.id""")
            for (model, name) in cr.fetchall():
                model_obj = pool.get(model)
                if isinstance(model_obj, osv.osv.osv_memory):
                    logger.warning('In-memory object %s (%s) should not have explicit access rules!' % (model, name))

            cr.execute("SELECT model FROM ir_model")
            for (model,) in cr.fetchall():
                obj = pool.get(model)
                if obj:
                    obj._check_removed_columns(cr, log=True)
                else:
                    logger.warning("Model %s is referenced but not present in the orm pool!", model)

            # Cleanup orphan records
            pool.get('ir.model.data')._process_end(cr, 1, processed_modules)

        if report.get_report():
            logger.info( report)

        for kind in ('init', 'demo', 'update'):
            tools.config[kind] = {}

        cr.commit()
        if update_module:
            cr.execute("SELECT id, name FROM ir_module_module WHERE state=%s ORDER BY id desc", ('to remove',))
            for mod_id, mod_name in cr.fetchall():
                logger.info("Removing module '%s' data",  mod_name)
                mod_dict = {}
                if cr.pgmode >= 'pg84':
                    # array_agg() appeared in 8.4, but does exactly the job we want
                    cr.execute('SELECT model, array_agg(res_id) FROM ir_model_data AS imd '
                            ' WHERE noupdate=%s AND module=%s AND model <> \'ir.module.module\' '
                            '    AND source in (\'xml\', \'orm\') AND res_id != 0 '
                            '    AND NOT EXISTS (SELECT 1 FROM ir_model_data AS imd2, ir_module_module AS mo '
                                                'WHERE mo.state != \'to remove\' AND mo.name = imd2.module '
                                                '  AND imd2.model = imd.model AND imd2.res_id = imd.res_id) '
                            'GROUP BY model ', (False, mod_name,), debug=True)
                    mod_dict = dict(cr.fetchall())
                else:
                    # We have to fetch distinct rows and aggregate them ourselves
                    cr.execute('SELECT model, res_id FROM ir_model_data AS imd '
                            'WHERE noupdate=%s AND module=%s AND model <> \'ir.module.module\' '
                            '    AND source in (\'xml\', \'orm\') AND res_id !=0 '
                            '    AND NOT EXISTS (SELECT 1 FROM ir_model_data AS imd2, ir_module_module AS mo '
                                                'WHERE mo.state != \'to remove\' AND mo.name = imd2.module '
                                                '  AND imd2.model = imd.model AND imd2.res_id = imd.res_id) '
                            'ORDER BY model, id DESC', (False, mod_name,), debug=True)
                    for model, res_id in cr.fetchall():
                        mod_dict.setdefault(model, []).append(res_id)

                for rmod, rids in mod_dict.items():
                    uid = 1
                    rmod_module= pool.get(rmod)
                    if rmod_module:
                        rmod_module.unlink(cr, uid, rids)
                    else:
                        logger.error('Could not locate %s to remove res=%s' % (rmod,rids))
                cr.execute('DELETE FROM ir_model_data WHERE noupdate=%s AND module=%s', (False, mod_name,))
                cr.commit()
            #
            # TODO: remove menu without actions of children
            #
            while True:
                cr.execute('''DELETE FROM ir_ui_menu
                    WHERE (id NOT IN (SELECT parent_id FROM ir_ui_menu WHERE parent_id IS NOT NULL))
                      AND (id NOT IN (SELECT res_id FROM ir_values WHERE model='ir.ui.menu'))
                      AND (id NOT IN (SELECT res_id FROM ir_model_data WHERE model='ir.ui.menu'))''')
                cr.commit()
                if not cr.rowcount:
                    break
                else:
                    logger.info( 'removed %d unused menus' % (cr.rowcount,))

            cr.execute("UPDATE ir_module_module SET state=%s WHERE state=%s", ('uninstalled', 'to remove',))
            cr.commit()
    finally:
        cr.close()


# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
