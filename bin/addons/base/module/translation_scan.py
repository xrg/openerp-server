# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP/F3, Open Source Management Solution
#    Copyright (C) 2013 P. Christeas <xrg@hellug.gr>
#    Parts Copyright (C) 2004-2011 OpenERP SA (www.openerp.com).
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

from tools.trans_scanner import _TScanWorker, abstractmethod

import fnmatch
from lxml import etree
from tools.misc import UpdateableStr
from tools.misc import SKIPPED_ELEMENT_TYPES
from collections import defaultdict
import os
import os.path
import tools
import re
import zipfile

class _Model_method(_TScanWorker):
    _name = '.model'

    @abstractmethod
    def scan(self, module, model, ids, id2name):
        """Scan records `ids` of this model
        """
        while False:
            yield None


class _IMD_method(_TScanWorker):
    """Scan for all data records referenced in ir.model.data
    """
    _name = 'method.ir_model_data'
    _inherit = '.method'

    def scan(self, modules):
        logger, cr, uid = self._logger, self.parent.cr, self.parent.uid
        pool = self.parent.pool
        query = 'SELECT name, model, res_id, module' \
            ' FROM ir_model_data WHERE source=\'xml\' AND res_id != 0 AND %s ' \
            ' ORDER BY module, model, name'

        wc, wp = self._get_where_calc(modules)
        cr.execute(query % wc, wp)

        # we have to fetch everything into memory, because fetchall()
        # is not reentrant, so cannot coincide with cr.execute() calls
        # inside the loop.
        model_ids = defaultdict(list)
        for xml_name, model, res_id, module in cr.fetchall():
            model_ids[(module, model)].append((res_id, xml_name))

        for module, model in model_ids:
            logger.debug("Scanning module \"%s\" model \"%s\" for data", module, model)
            obj_model = pool.get(model)
            if not obj_model:
                logger.error("Unable to find object %r", model)
                continue

            id2name = dict(model_ids[(module, model)])
            real_ids = obj_model.search(cr, uid, [('id', 'in', id2name.keys())])
            if not real_ids:
                continue

            # First, process these models whose fields deserve some special
            # handling wrt. translation
            try:
                proc = _TScanWorker['model.'+model](self.parent)
            except TypeError:
                proc = None

            if proc is not None:
                for line in proc.scan(module, obj_model, real_ids, id2name):
                    yield line

            # then, locate all translatable fields and export them, too
            tr_columns = {}
            for field_name, field_def in obj_model._columns.items():
                if field_def.translate:
                    tr_columns[field_name] = model + "," + field_name

            if tr_columns:
                for res in obj_model.read(cr, uid, real_ids, fields=tr_columns.keys()):
                    for tn, nn in tr_columns.items():
                        if not res[tn]:
                            continue
                        yield module, res[tn], nn, module+'.'+id2name[res['id']], 'model'

        # end of function


# Model-specific workers for ir.model.data records
# -------------------------------------------------

class _ui_view_method(_TScanWorker):
    _name = 'model.ir.ui.view'
    _inherit = '.model'

    # Read 'view.rng' and fill below all view elements with their
    # attributes that may get translated.
    _view_elems = { 'form': ['string',],
            'diagram': ['string',],
            'arrow': ['label',],
            'tree': ['string',],
            'search': ['string',],
            'label': ['string', 'help', True],
            'gantt': ['string',],
            'page': ['string',],
            'separator': ['string',],
            'field': ['string', 'sum', 'help'],
            'group': ['string',],
            'calendar': ['string',],
            'graph': ['string',],
            'button': ['string', 'confirm', 'help'],
            'filter': ['string', 'help'],
            'attribute': [],
        }

    def _parse_view(self, view_dom):
        for elem in view_dom.iter(tag=etree.Element):
            if not isinstance(elem, etree._Element):
                continue
            if elem.tag == 'attribute' and elem.get('name') == 'string':
                yield elem.text
            for attr in self._view_elems.get(elem.tag, []):
                if attr is True:
                    yield elem.text
                    continue
                yield elem.get(attr)
        return

    def scan(self, module, model, ids, id2name):
        cr, uid = self.parent.cr, self.parent.uid
        for obj in model.browse(cr, uid, ids):
            arch = obj.arch
            view_model = obj.model
            if isinstance(arch, unicode):
                arch = arch.encode('utf-8')
            dom = etree.XML(arch)
            for y in self._parse_view(dom):
                if not y:
                    continue
                yield module, y, view_model, 0, 'view'

        return

class _ir_wizard_method(_TScanWorker):
    _name = 'model.ir.actions.wizard'
    _inherit = '.model'

    def scan(self, module, model, ids, id2name):
        import netsvc
        cr, uid = self.parent.cr, self.parent.uid
        self.view_parser = _TScanWorker['model.ir.ui.view'](self.parent)

        for obj in model.browse(cr, uid, ids):
            service_name = 'wizard.'+obj.wiz_name
            obj2 = netsvc.Service._services.get(service_name)
            if not obj2:
                self._logger.warning("Wizard not found in netsvc: %s", service_name)
                continue

            for state_name, state_def in obj2.states.iteritems():
                if 'result' in state_def:
                    for line in self._parse_result(module,
                                u"%s,%s" % (obj.wiz_name, state_name),
                                state_def['result']):
                        yield line

    def _parse_result(self, module, name, result):
        if result['type'] != 'form':
            return

        # export fields
        if result.has_key('fields'):
            for field_name, field_def in result['fields'].iteritems():
                res_name = name + ',' + field_name

                if 'string' in field_def:
                    yield  module, field_def['string'], res_name,  0, 'wizard_field'
                if 'help' in field_def:
                    yield  module, field_def['string'], res_name,  0, 'help'
                if 'selection' in field_def and not callable(field_def['selection']):
                    for v, hstr in field_def['selection']:
                        yield  module, hstr, res_name,  0, 'selection'
        else:
            self._logger.warning("res has no fields: %r", result)


        # export arch
        arch = result['arch']
        if arch and not isinstance(arch, UpdateableStr):
            if isinstance(arch, unicode):
                arch = arch.encode('utf-8')
            
            dom = etree.XML(arch)
            for y in self.view_parser._parse_view(dom):
                if not y: continue
                yield module, y, name, 0, 'wizard_view'

        # export button labels
        for but_args in result['state']:
            button_name = but_args[0]
            source = but_args[1]
            res_name = name + ',' + button_name
            yield module, source, res_name, 0, 'wizard_button'

        # end of _parse_result().

class _model_fields_method(_TScanWorker):
    """Scan names of fields, help strings
    """
    _name = 'model.ir.model.fields'
    _inherit = '.model'

    def scan(self, module, model, ids, id2name):
        cr, uid = self.parent.cr, self.parent.uid
        if not model:
            return
        for obj in model.browse(cr, uid, ids):
            xml_name = id2name[obj.id]
            objmodel = self.parent.pool.get(obj.model)
            if not objmodel:
                self._logger.warning("Unable to find ORM model \"%s\" for field #%d %s",
                            obj.model, obj.id, xml_name)
                continue
            try:
                field_def = objmodel._columns.get(obj.name, False)
                if not field_def:
                    self._logger.warning("Strange, object \"%s\" is missing field \"%s\" ",
                                obj.model, obj.name)
                    continue
            except AttributeError, exc:
                self._logger.error("name error in %s: %s", xml_name, exc)
                continue

            name = u"%s,%s" % (obj.model, obj.name)
            yield module, field_def.string, name, 0, 'field'
            
            if getattr(field_def, 'help', None):
                yield module, field_def.help, name, 0, 'help'

            if isinstance(getattr(field_def, 'selection', None), (list, tuple)):
                for dummy, val in field_def.selection:
                    yield module, val, name, 0, 'selection'

class _actions_report_xml_method(_TScanWorker):
    _name = 'model.ir.actions.report.xml'
    _inherit = '.model'

    _rml_re = re.compile(r'\[\[.+?\]\]')

    def _parse_report_rml(self, dom):
        """ Extracts all plain text from RML DOM
        """
        for m in dom.iter(tag=etree.Element):
            if not m.text:
                continue
            for s in self._rml_re.split(m.text):
                yield s.replace('\n', ' ').strip()

    def _parse_report_xsl(self, dom):
        """ XSL translatable strings are all plaintext contained in
            elements that have attribute 't="1"'
            
            It shall traverse these elements down and parse their
            sub-elements, too. So, a plain `iter()` won't work.
        """
        # FIXME: sub-optimal, have to use iterparse() ...
        for n in dom:
            if n.get("t"):
                for m in n:
                    if isinstance(m, SKIPPED_ELEMENT_TYPES) or not m.text:
                        continue
                    l = m.text.strip().replace('\n',' ')
                    if len(l):
                        yield l
            for y in self._parse_report_xsl(n):
                yield y
        return

    def scan(self, module, model, ids, id2name):
        cr, uid = self.parent.cr, self.parent.uid
        for obj in model.browse(cr, uid, ids):
            name = obj.report_name
            fname = ""
            if obj.report_rml:
                fname = obj.report_rml
                parse_func = self._parse_report_rml
                report_type = "report"
            elif obj.report_xsl:
                fname = obj.report_xsl
                parse_func = self._parse_report_xsl
                report_type = "xsl"
            if fname and obj.report_type in ('pdf', 'xsl', 'txt'):
                try:
                    report_file = tools.file_open(fname)
                    try:
                        dom = etree.parse(report_file)
                        for y in parse_func(dom.getroot()):
                            if not y:
                                continue
                            yield module, y, name, 0, report_type
                    finally:
                        report_file.close()
                except (IOError, etree.XMLSyntaxError):
                    self._logger.exception("couldn't export translation for report %s %s %s", name, report_type, fname)


#    More root-level methods
#  -------------------------

class _ORM_Model_method(_TScanWorker):
    """Scan for translations inside ORM models

        So far, only constraint messages are exported from models
    """
    _name = 'method.ir_model'
    _inherit = '.method'

    def scan(self, modules):
        logger, cr, uid = self._logger, self.parent.cr, self.parent.uid
        pool = self.parent.pool
        query = """SELECT * FROM
            ( SELECT DISTINCT ON(m.model) m.id, m.model, imd.module
            FROM ir_model AS m, ir_model_data AS imd
            WHERE m.id = imd.res_id AND imd.model = 'ir.model'
                AND imd.source IN ('orm', 'xml') AND imd.res_id != 0
            ORDER BY m.model, imd.id) AS foo
             WHERE %s
             ORDER BY module, model
            """
        wc, wp = self._get_where_calc(modules)
        cr.execute(query % wc, wp)

        for (model_id, model, module) in cr.fetchall():
            
            model_obj = pool.get(model)

            if not model_obj:
                logger.error("Unable to find object %r", model)
                continue

            #if model_obj._debug:
            logger.debug("Scanning model %s for translations", model)

            for constraint in getattr(model_obj, '_constraints', []):
                c = constraint[1]
                if callable(c) or not c:
                    continue
                yield (module, c, model, 0, 'constraint')

            for constraint in getattr(model_obj, '_sql_constraints', []):
                c = constraint[2]
                if callable(c) or not c:
                    continue
                yield (module, c, model, 0, 'sql_constraint')

        return
        # end of function

class _Source_method(_TScanWorker):
    """Scan for translations in source files of modules

    """
    _name = 'method.module_source'
    _inherit = '.method'

    def __init__(self, scanner):
        super(_Source_method, self).__init__(scanner)
        self.join_dquotes = re.compile(r'([^\\])"[\s\\]*"', re.DOTALL)
        self.join_quotes = re.compile(r'([^\\])\'[\s\\]*\'', re.DOTALL)
        self.re_dquotes = re.compile(r'[^a-zA-Z0-9_]_\([\s]*"(.+?)"[\s]*?\)', re.DOTALL)
        self.re_quotes = re.compile(r'[^a-zA-Z0-9_]_\([\s]*\'(.+?)\'[\s]*?\)', re.DOTALL)


    def _get_module_files(self, module):
        from addons import get_module_path # must be here, lazy!
        path = get_module_path(module)
        if not path:
            raise EnvironmentError("Cannot find files of module %s!" % module)

        is_zip = False
        if os.path.isdir(path):
            files = tools.osutil.listdir(path, True)
            
        else:
            # zipmodule
            is_zip = True
            zipf = zipfile.ZipFile(path + ".zip")
            files = ['/'.join(f.split('/')[1:]) for f in zipf.namelist()]
            zipf.close()

        return path, files, is_zip

    def scan(self, modules):
        logger, cr, uid = self._logger, self.parent.cr, self.parent.uid

        query = """SELECT id, name FROM ir_module_module
             WHERE %s
             ORDER BY name
            """
        wc, wp = self._get_where_calc(modules, col='name')
        cr.execute(query % wc, wp)

        for (module_id, module) in cr.fetchall():
            try:
                path, files, is_zip = self._get_module_files(module)
                logger.debug("Scanning sources of module %s", module)
            except Exception, e:
                logger.warning("Cannot find sources: %s", e)
                continue

            if is_zip:
                # TODO
                logger.error("Not implemented: zip support for %s", path)
                continue

            for fname in fnmatch.filter(files, '*.py'):
                for y in self._scan_source_file(module, path, fname, 'code'):
                    yield y
            for fname in fnmatch.filter(files, '*.mako'):
                for y in self._scan_source_file(module, path, fname, 'report'):
                    yield y

            if module == 'base':
                # Also scan these non-addon paths
                root_path = tools.config['root_path']
                rfiles = []
                rfiles.append((root_path, os.path.join('addons', '__init__.py')))
                for bin_path in ['osv', 'report']:
                    for r in fnmatch.filter(tools.osutil.listdir(os.path.join(root_path, bin_path), True), '*.py'):
                        rfiles.append((root_path, os.path.join(bin_path, r)))

                for path, fname in rfiles:
                    for y in self._scan_source_file('base', path, fname, 'code', True):
                        yield y
        return

    def _scan_source_file(self, module, path, fname, terms_type, abs_mode=False):
        fabsolutepath = path + '/' + fname
        self._logger.debug("Scanning code of %s/%s ....", module, fname)
        src_file = open(fabsolutepath, 'rb')
        try:
            code_string = src_file.read()
        finally:
            src_file.close()
        if abs_mode:
            frelativepath = fname
        else:
            frelativepath = 'addons/'+module+'/'+fname
        return self._scan_stream(module, code_string, terms_type, frelativepath)

    def _scan_stream(self, module, code_string, terms_type, frelativepath):
            code_offset = 0
            code_line = 1
            for i in self.re_dquotes.finditer(code_string):
                src = i.group(1)
                if src.startswith('""'):
                    assert src.endswith('""'), "Incorrect usage of _(..) function (should contain only literal strings!) in file %s near: %s" % (frelativepath, src[:30])
                    src = src[2:-2]
                else:
                    src = self.join_dquotes.sub(r'\1', src)
                # try to count the lines from the last pos to our place:
                code_line += code_string[code_offset:i.start(1)].count('\n')
                # now, since we did a binary read of a python source file, we
                # have to expand pythonic escapes like the interpreter does.
                src = src.decode('string_escape')
                yield module, src, frelativepath, code_line, terms_type
                code_line += i.group(1).count('\n')
                code_offset = i.end() # we have counted newlines up to the match end

            code_offset = 0 #reset counters
            code_line = 1
            for i in self.re_quotes.finditer(code_string):
                src = i.group(1)
                if src.startswith("''"):
                    assert src.endswith("''"), "Incorrect usage of _(..) function (should contain only literal strings!) in file %s near: %s" % (frelativepath, src[:30])
                    src = src[2:-2]
                else:
                    src = self.join_quotes.sub(r'\1', src)
                code_line += code_string[code_offset:i.start(1)].count('\n')
                src = src.decode('string_escape')
                yield module, src, frelativepath, code_line, terms_type
                code_line += i.group(1).count('\n')
                code_offset = i.end() # we have counted newlines up to the match end

#eof

