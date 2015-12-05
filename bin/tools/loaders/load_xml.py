# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2011 OpenERP s.a. (<http://openerp.com>).
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

#.apidoc title: XML loader for OpenERP/F3 data

import os.path
import logging
import re

from lxml import etree
from tools.data_loaders import DataLoader
from tools import config
from tools.misc import ustr, file_open
from tools.translate import _
from tools.service_meta import _ServiceMeta, abstractmethod

# List of etree._Element subclasses that we choose to ignore when parsing XML.
from tools import SKIPPED_ELEMENT_TYPES

# Import of XML records requires the unsafe eval as well,
# almost everywhere, which is ok because it supposedly comes
# from trusted data, but at least we make it obvious now.
unsafe_eval = eval
from tools.safe_eval import safe_eval, ExecContext

import netsvc

# for eval context:
import time
import release
try:
    import pytz
    _hush_pyflakes = [ pytz,]
except ImportError:
    logging.getLogger("init").warning('could not find pytz library, please install it')
    class pytzclass(object):
        all_timezones=[]
    pytz = pytzclass()

from datetime import datetime, timedelta
from tools.date_eval import date_eval

eval = NotImplemented

_eval_consts = { '0': 0, '1': 1, 'True': True, 'False': False, 'None': None,
            '[]': [], '[ ]': [], '{}': {} }

class _TagService(object):
    """Parsers for each XML tag we support in the <data> element
    """
    __metaclass__ = _ServiceMeta

    def __init__(self, parent):
        """ Parent is the DataLoader instance
        """
        self.parent = parent

    @abstractmethod
    def parse_xml(self, cr, rec):
        """Parse `rec` DOM element

            This function shall not return any result, since it will store
            any modifications in the database.
            Remember that it is called for all elements under the `<data>`
            one.
        """
        pass

    def eval_xml(self, cr, rec, parent_model=None, context=None):
        """Like parse_xml, but returning the result

            It is called for elements inside others, like the <field>s
            or <function> contents.

            The base class is /not/ abstract, but raises an error if
            this unexpected element tries to eval_xml() .
        """
        raise NotImplementedError

    def eval(self, code, cr=None, model=None):
        """ A shortened version of safe_eval, used within XML attributes
        """
        if not code:
            return None
        if code in _eval_consts:
            return _eval_consts[code]

        ctx = self.parent._get_eval_context(cr=cr, model=model)
        return safe_eval(code, ctx)

    @property
    def ir_model_data(self):
        """Quick access to the ir.model.data object
        """
        return self.parent.pool.get('ir.model.data')

class _subtag_Mixin(object):
    """helper for tags that have a set of allowed elements inside
    """
    _subtag_prefix = None
    def __init__(self, *args, **kwargs):
        super(_subtag_Mixin, self).__init__(*args, **kwargs)
        self._subtag_cache = {}

    def _get_subtag(self, rec):
        if self._subtag_prefix:
            name = self._subtag_prefix + rec.tag
        else:
            name = rec.tag
        if name not in self._subtag_cache:
            klass = _TagService[name]
            self._subtag_cache[name] = klass(self.parent)
        return self._subtag_cache[name]

class _LoaderExecContext(ExecContext):
    #def __init__(self, **kwargs):
    #    super(_LoaderExecContext
    _name = 'loader_xml'

    def prepare_context(self, context):

        context.update(self.parent.idref)
        context.update(int=int, str=str, bool=bool, unicode=unicode, float=float,
                    time=time,
                    DateTime=datetime,
                    datetime=datetime,
                    date_eval=lambda rstr: date_eval(rstr).date(),
                    datetime_eval=date_eval,
                    time_eval=lambda rstr: date_eval(rstr).time(),
                    timedelta=timedelta,
                    version=release.major_version,
                    pytz=pytz)
        if getattr(self, 'model', None) and getattr(self, 'cr', None):
            model = self.model
            cr = self.cr
            context['obj'] = lambda ids: model.browse(cr, self.parent.uid, ids, \
                                        context=self.parent.context)
        if getattr(self, 'cr', None):
            cr = self.cr # copy, because we will delete it now
            context['ref'] = lambda x: self.parent.id_get(cr, x)

        # some vars must never survive a second context:
        self.__dict__.pop('cr', None)
        self.__dict__.pop('model', None)

def nodeattr2bool(node, attr, default=False):
    if not node.get(attr):
        return default
    val = node.get(attr).strip()
    if not val:
        return default
    return val.lower() not in ('0', 'false', 'off')

def _fix_multiple_roots(node):
    """
    Surround the children of the ``node`` element of an XML field with a
    single root "data" element, to prevent having a document with multiple
    roots once parsed separately.

    XML nodes should have one root only, but we'd like to support
    direct multiple roots in our partial documents (like inherited view architectures).
    As a convention we'll surround multiple root with a container "data" element, to be
    ignored later when parsing.
    """

    num_nodes = 0
    for n in node:
        if not isinstance(n.tag, SKIPPED_ELEMENT_TYPES):
            num_nodes += 1
    if num_nodes > 1:
        data_node = etree.Element("data")
        for child in node:
            data_node.append(child)
        node.append(data_node)

class _XMLloader(DataLoader):
    """ The loader of 'tools/convert.py', refactored, modular
    """
    _name = 'xml'
    _xml_parser = None
    _relaxng_validator = None
    logger = logging.getLogger('tools.convert.xml_import')

    @classmethod
    def _load_validator(cls):
        """Preloads the RNG validator for XML parsing
        """
        if not cls._xml_parser:
            cls._xml_parser = etree.XMLParser(remove_blank_text=False, remove_comments=True)
        if cls._relaxng_validator:
            return

        try:
            frng = None
            frng = file_open(os.path.join(config['root_path'],'import_xml.rng' ))
            relaxng_doc = etree.parse(frng)
            relaxng = etree.RelaxNG(relaxng_doc)
        finally:
            if frng:
                frng.close()

        cls._relaxng_validator = relaxng
        # TODO: multiple files, inheritance

    def __init__(self, *args, **kwargs):
        super(_XMLloader,self).__init__(*args, **kwargs)
        if kwargs.get('report', False):
            self.assert_report = kwargs['report']
        else:
            from tools.convert import assertion_report
            self.assert_report = assertion_report()

        self._tags = {}

        # initialize the RelaxNG validator, just as a Loader is about
        # to start parsing a file
        self._load_validator()
        self._orig_noupdate = self.noupdate
        self._known_modules = set([self.module_name, ])
        self.update_mode = bool(self.noupdate and self.mode != 'init')
        self.exec_context = ExecContext['loader_xml'](parent=self)
        self.data_node = None
        if self.context is None:
            self.context = {}

    def parse(self, cr, fname, fp):

        # load the file into a lXML eTree
        doc = etree.parse(fp, parser=self._xml_parser)

        try:
            self._relaxng_validator.assert_(doc)
            # TODO: perhaps explicitly catch only RelaxNG exceptions
        except Exception:
            self.logger.error('The XML file does not fit the required schema !\n%s', \
                            ustr(self._relaxng_validator.error_log.last_error))
            raise

        root = doc.getroot()
        if not root.tag in ['terp', 'openerp']:
            self.logger.error("Mismatch xml format")
            raise Exception( "Mismatch xml format: only terp or openerp as root tag" )

        if root.tag == 'terp':
            self.logger.warning("The tag <terp/> is deprecated, use <openerp/>")

        for n in root.findall('./data'):
            if n.get('with-modules'):
                need_modules = [ x.strip() for x in n.get('with-modules').split(',')]
                need_modules = filter(bool, need_modules) # no empty entries
                if need_modules:
                    mod_obj = self.pool.get('ir.module.module')
                    mids = mod_obj.search(cr, self.uid, [('name','in', need_modules),
                                                        ('state','in', ('installed', 'to upgrade'))])
                    if len(mids) < len(need_modules):
                        self.logger.warning("Record at module %s skipped, because not all of %s module(s) are installed",
                            self.module_name, ', '.join(need_modules))
                        continue

            if nodeattr2bool(n, 'noupdate', False):
                self.noupdate = True
            else:
                self.noupdate = self._orig_noupdate
            self.update_mode = bool(self.noupdate and self.mode != 'init')
            self.data_node = n

            for rec in n:
                if isinstance(rec, etree.CommentBase):
                    continue
                if not rec.tag in self._tags:
                    # only initialize each tag parser one time for each XML
                    # file, when it is met. This is lazy enough.
                    try:
                        tklass = _TagService[rec.tag]
                        self._tags[rec.tag] = tklass(self)
                    except TypeError:
                        self.logger.warning("Parsing %s:%d: Unknown tag \"%s\" ",
                                    fname, rec.sourceline, rec.tag)
                        continue

                try:
                    self._tags[rec.tag].parse_xml(cr, rec)
                except Exception:
                    self.logger.debug("Tag exception:", exc_info=True)
                    self.logger.error('Parse error in %s:%d: \n%s',
                                        fname, rec.sourceline,
                                        etree.tostring(rec).strip(), exc_info=True)
                    raise

        self.noupdate = self._orig_noupdate
        return True

    @classmethod
    def unload(cls):
        cls._xml_parser = None
        cls._relaxng_validator = None # deallocate it

    def _test_xml_id(self, cr, xml_id):
        if '.' in xml_id:
            module, xml_id2 = xml_id.split('.', 1)
            assert '.' not in xml_id2, """The ID reference "%s" must contain
maximum one dot. They are used to refer to other modules ID, in the
form: module.record_id""" % (xml_id,)
            if module not in self._known_modules:
                if self.pool.get('ir.module.module').search(cr, self.uid,
                                ['&', ('name', '=', module), ('state', 'in', ['installed'])],
                                count=True, limit=1):
                    self._known_modules.add(module)
                else:
                    raise Exception( "The ID \"%s\" refers to an uninstalled module" % (xml_id,))
            xml_id = xml_id

        if len(xml_id) > 64:
            self.logger.error('id: %s is too long (max: 64)', xml_id)
            # we'd better stop here, so that truncation won't mess it.
            raise Exception("XML id too long")

    def id_get(self, cr, id_str):
        if id_str in self.idref:
            return self.idref[id_str]
        elif isinstance(id_str, (int, long)):
            return id_str
        mid = self.model_id_get(cr, id_str)
        if not mid:
            raise KeyError("Id %s not found in db" %(id_str, ))
        assert isinstance(mid[1], (int, long))
        return mid[1]

    def model_id_get(self, cr, id_str):
        model_data_obj = self.pool.get('ir.model.data')
        mod = self.module_name
        if '.' in id_str:
            mod,id_str = id_str.split('.')
        return model_data_obj.get_object_reference(cr, self.uid, mod, id_str)

    def get_uid(self, cr,node):
        node_uid = node.get('uid','') or (len(self.data_node) and self.data_node.get('uid',''))
        if node_uid:
            return self.id_get(cr, node_uid)
        return self.uid

    def _remove_ir_values(self, cr, name, value, model):
        ir_value_ids = self.pool.get('ir.values').search(cr, self.uid, [('name','=',name),('value','=',value),('model','=',model)])
        if ir_value_ids:
            self.pool.get('ir.values').unlink(cr, self.uid, ir_value_ids)
            self.pool.get('ir.model.data')._unlink(cr, self.uid, 'ir.values', ir_value_ids)

        return True

    def make_record(self, cr, dmodel, res, xml_id, res_id=None, skip_check=False, context=None):
        """Create or update a database record

            @param dmodel string name of ORM model
            @param res dictionary of record values
            @param xml_id 'ir.model.data' identifier
            @param skip_check Skips checking the `xml_id` for validity,
                eg. in case it's already done
        """
        if (not skip_check) and xml_id:
            self._test_xml_id(cr, xml_id)

        new_id = self.pool.get('ir.model.data')._update(cr, self.uid, dmodel,
                self.module_name, res, xml_id, noupdate=self.noupdate, res_id=res_id,
                mode=self.mode, context=context or self.context)
        if xml_id and new_id:
            self.idref[xml_id] = int(new_id)
        return new_id

    def _get_eval_context(self, cr=None, model=None):
        """ """
        self.exec_context.cr = cr
        self.exec_context.model = model

        ret = {}
        self.exec_context.prepare_context(ret)
        return ret

    def get_node_context(self, node, eval_dict):
        data_node_context = (len(self.data_node) and self.data_node.get('context',''))
        node_context = node.get("context",'')
        context = {}
        for ctx in (data_node_context, node_context):
            if not ctx:
                continue
            try:
                ctx_res = unsafe_eval(ctx, eval_dict)
                if isinstance(context, dict):
                    context.update(ctx_res)
                else:
                    context = ctx_res
            except NameError:
                # Some contexts contain references that are only valid at runtime at
                # client-side, so in that case we keep the original context string
                # as it is. We also log it, just in case.
                context = ctx
                logging.getLogger("init").debug('Context value (%s) for element with id "%s" or its data node does not parse '\
                                                'at server-side, keeping original string, in case it\'s meant for client side only',
                                                ctx, node.get('id','n/a'), exc_info=True)
        return context

#   XML tags as classes
# ------------------------

class _tag_function(_subtag_Mixin, _TagService):
    _name = 'function'
    _subtag_prefix = 'val.'

    def parse_xml(self, cr, rec):
        if self.parent.update_mode:
            return
        eval_dict = self.parent._get_eval_context(cr)
        context = self.parent.get_node_context(rec, eval_dict )
        res = self.eval_xml(cr, rec, context=context)

        if res is not None and res is not True:
            self.parent.logger.debug("Function result: %r", res)
        return

    def eval_xml(self, cr, rec, parent_model=None, context=None):
        args = []
        ctx = self.parent._get_eval_context(cr, model=parent_model)
        if rec.get('eval'):
            args = safe_eval(rec.get('eval'), ctx)
        for n in rec:
            rtag = self._get_subtag(n)
            rv = rtag.eval_xml(cr, n, context=context)
            if rv is not None:
                args.append(rv)
        uid = self.parent.get_uid(cr, rec)
        model = self.parent.pool.get(rec.get('model',''))
        method = rec.get('name','')
        res = getattr(model, method)(cr, uid, *args)
        return res

class _tag_function_val(_TagService):
    """ A `<function>` can be met as an argument to another function

        Then, the inner function will be evaluated and result fed into
        the outer one.
        This can only work by inheritance, since the caller will look
        for `eval_xml()` at this object.
    """
    _name = 'val.function'
    _inherit = 'function'

class _tag_delete(_TagService):
    _name = 'delete'

    def parse_xml(self, cr, rec):
        d_model = rec.get("model",'')
        d_search = rec.get("search",'')
        d_id = rec.get("id",'')
        ids = []

        if d_search:
            ctx = self.parent._get_eval_context(model=d_model)
            dom = safe_eval(d_search, ctx)
            ids = self.parent.pool.get(d_model).search(cr, self.parent.uid, dom)
        if d_id:
            try:
                ids.append(self.parent.id_get(cr, d_id))
            except Exception:
                # d_id cannot be found. doesn't matter in this case
                pass
        if ids:
            self.parent.pool.get(d_model).unlink(cr, self.uid, ids)
            self.ir_model_data._unlink(cr, self.parent.uid, d_model, ids)


class _tag_report(_TagService):
    _name = 'report'

    def parse_xml(self, cr, rec):
        res = {}
        for dest,f in (('name','string'),('model','model'),('report_name','name')):
            res[dest] = rec.get(f,'')
            if not res[dest]:
                raise ValueError("Attribute %s of report is empty !" % f)
        for field,dest in (('rml','report_rml'),('file','report_rml'),('xml','report_xml'),('xsl','report_xsl'),('attachment','attachment'),('attachment_use','attachment_use')):
            if rec.get(field):
                res[dest] = rec.get(field)
        if rec.get('auto'):
            res['auto'] = self.eval(rec.get('auto','False'))
        if rec.get('sxw'):
            sxw_content = file_open(rec.get('sxw')).read()
            res['report_sxw_content'] = sxw_content
        if rec.get('header'):
            res['header'] = self.eval(rec.get('header','False'))
        if rec.get('report_type'):
            res['report_type'] = rec.get('report_type')

        res['multi'] = rec.get('multi') and self.eval(rec.get('multi','False'))

        xml_id = rec.get('id','')
        # self.parent._test_xml_id(xml_id) # deferred

        if rec.get('groups'):
            g_names = rec.get('groups','').split(',')
            groups_value = []
            # groups_obj = self.pool.get('res.groups')
            for group in g_names:
                if group.startswith('-'):
                    group_id = self.parent.id_get(cr, group[1:])
                    groups_value.append((3, group_id))
                else:
                    group_id = self.parent.id_get(cr, group)
                    groups_value.append((4, group_id))
            res['groups_id'] = groups_value

        id = self.parent.make_record(cr, "ir.actions.report.xml", res, xml_id)

        if not rec.get('menu') or self.eval(rec.get('menu','False')):
            keyword = str(rec.get('keyword', 'client_print_multi'))
            # keys = [('action',keyword),('res_model',res['model'])]
            value = 'ir.actions.report.xml,'+str(id)
            replace = rec.get('replace', True)
            self.ir_model_data.ir_set(cr, self.parent.uid,
                        'action', keyword, res['name'], [res['model']],
                        value, replace=replace, isobject=True, xml_id=xml_id)
        elif self.parent.mode=='update' and self.eval(rec.get('menu','False'))==False:
            # Special check for report having attribute menu=False on update
            value = 'ir.actions.report.xml,'+str(id)
            self.parent._remove_ir_values(cr, res['name'], value, res['model'])
        return False

class _tag_wizard(_TagService):
    _name = 'wizard'

    def parse_xml(self, cr, rec):
        string = rec.get("string",'')
        model = rec.get("model",'')
        name = rec.get("name",'')
        xml_id = rec.get('id','')
        multi = rec.get('multi','') and self.eval(rec.get('multi','False'))
        res = {'name': string, 'wiz_name': name, 'multi': multi, 'model': model}

        if rec.get('groups'):
            g_names = rec.get('groups','').split(',')
            groups_value = []
            # groups_obj = self.pool.get('res.groups')
            for group in g_names:
                if group.startswith('-'):
                    group_id = self.parent.id_get(cr, group[1:])
                    groups_value.append((3, group_id))
                else:
                    group_id = self.parent.id_get(cr, group)
                    groups_value.append((4, group_id))
            res['groups_id'] = groups_value

        new_id = self.parent.make_record(cr, "ir.actions.wizard", res, xml_id)

        # ir_set
        if (not rec.get('menu') or self.eval(rec.get('menu','False'))) and id:
            keyword = str(rec.get('keyword','') or 'client_action_multi')
            value = 'ir.actions.wizard,'+str(new_id)
            replace = rec.get("replace",'') or True
            self.ir_model_data.ir_set(cr, self.parent.uid,
                        'action', keyword, string, [model], value, replace=replace,
                        isobject=True, xml_id=xml_id)
        elif self.parent.mode=='update' and (rec.get('menu') and self.eval(rec.get('menu','False'))==False):
            # Special check for wizard having attribute menu=False on update
            value = 'ir.actions.wizard,'+str(new_id)
            self.parent._remove_ir_values(cr, string, value, model)

class _tag_url(_TagService):
    _name = 'url'

    def parse_xml(self, cr, rec):
        url = rec.get("string",'')
        target = rec.get("target",'')
        name = rec.get("name",'')
        xml_id = rec.get('id','')
        res = {'name': name, 'url': url, 'target':target}

        new_id = self.parent.make_record(cr, "ir.actions.url", res, xml_id,)

        # ir_set
        if (not rec.get('menu') or self.eval(rec.get('menu','False'))) and id:
            keyword = str(rec.get('keyword','') or 'client_action_multi')
            value = 'ir.actions.url,%s' % new_id
            replace = rec.get("replace",'') or True
            self.ir_model_data.ir_set(cr, self.parent.uid,
                    'action', keyword, url, ["ir.actions.url"], value,
                    replace=replace, isobject=True, xml_id=xml_id)
        elif self.parent.mode=='update' and (rec.get('menu') and self.eval(rec.get('menu','False'))==False):
            # Special check for URL having attribute menu=False on update
            value = 'ir.actions.url,%s' % new_id
            self.parent._remove_ir_values(cr, url, value, "ir.actions.url")

class _tag_act_window(_TagService):
    _name = 'act_window'

    def parse_xml(self, cr, rec):
        name = rec.get('name','')
        xml_id = rec.get('id','')
        type = rec.get('type','') or 'ir.actions.act_window'
        view_id = False
        if rec.get('view_id'):
            view_id = self.parent.id_get(cr, rec.get('view_id',''))
        domain = rec.get('domain','') or '[]'
        res_model = rec.get('res_model','')
        src_model = rec.get('src_model','')
        view_type = rec.get('view_type','') or 'form'
        view_mode = rec.get('view_mode','') or 'tree,form'
        usage = rec.get('usage','')
        limit = rec.get('limit','')
        auto_refresh = rec.get('auto_refresh','')
        uid = self.parent.uid
        # def ref() added because , if context has ref('id') eval wil use this ref

        active_id = str("active_id") # for further reference in client/bin/tools/__init__.py

        # Include all locals() in eval_context, for backwards compatibility
        eval_context = {
            'name': name,
            'xml_id': xml_id,
            'type': type,
            'view_id': view_id,
            'domain': domain,
            'res_model': res_model,
            'src_model': src_model,
            'view_type': view_type,
            'view_mode': view_mode,
            'usage': usage,
            'limit': limit,
            'auto_refresh': auto_refresh,
            'uid' : uid,
            'active_id': active_id,
            'ref' : lambda str_id: self.parent.id_get(cr, str_id),
        }
        context = self.parent.get_node_context(rec, eval_context)

        try:
            domain = self.eval(domain, eval_context)
        except NameError:
            # Some domains contain references that are only valid at runtime at
            # client-side, so in that case we keep the original domain string
            # as it is. We also log it, just in case.
            logging.getLogger("init").debug('Domain value (%s) for element with id "%s" does not parse '\
                                            'at server-side, keeping original string, in case it\'s meant for client side only',
                                            domain, xml_id or 'n/a', exc_info=True)
        res = {
            'name': name,
            'type': type,
            'view_id': view_id or None,
            'domain': domain,
            'context': context,
            'res_model': res_model,
            'src_model': src_model,
            'view_type': view_type,
            'view_mode': view_mode,
            'usage': usage,
            'limit': limit or None,
            'auto_refresh': auto_refresh or None,
        }

        if rec.get('groups'):
            g_names = rec.get('groups','').split(',')
            groups_value = []
            for group in g_names:
                if group.startswith('-'):
                    group_id = self.parent.id_get(cr, group[1:])
                    groups_value.append((3, group_id))
                else:
                    group_id = self.parent.id_get(cr, group)
                    groups_value.append((4, group_id))
            res['groups_id'] = groups_value

        if rec.get('target'):
            res['target'] = rec.get('target','')
        if rec.get('multi'):
            res['multi'] = rec.get('multi', False)

        new_id = self.parent.make_record(cr, 'ir.actions.act_window',res, xml_id)
        if src_model:
            #keyword = 'client_action_relate'
            keyword = rec.get('key2','') or 'client_action_relate'
            value = 'ir.actions.act_window,'+str(new_id)
            replace = rec.get('replace','') or True
            self.ir_model_data.ir_set(cr, self.parent.uid, 'action',
                        keyword, xml_id, [src_model], value, replace=replace,
                        isobject=True, xml_id=xml_id)
        # TODO add remove ir.model.data

class _tag_ir_set(_subtag_Mixin, _TagService):
    _name = 'ir_set'
    _subtag_prefix = 'fld.'

    def parse_xml(self, cr, rec):
        if self.parent.mode != 'init':
            return
        rlist = []
        for node in rec:
            rtag = self._get_subtag(node)
            rlist.append(rtag.eval_xml(cr, node))
        res = dict(rlist)
        self.ir_model_data.ir_set(cr, self.parent.uid, res['key'], res['key2'],
                            res['name'], res['models'], res['value'],
                            replace=res.get('replace',True),
                            isobject=res.get('isobject', False),
                            meta=res.get('meta',None))

class _tag_workflow(_subtag_Mixin, _TagService):
    _name = 'workflow'
    _subtag_prefix = 'val.'

    def parse_xml(self, cr, rec):
        if self.parent.update_mode:
            return
        model = str(rec.get('model',''))
        w_ref = rec.get('ref','')
        if w_ref:
            new_id = self.parent.id_get(cr, w_ref)
        else:
            number_children = len(rec)
            assert number_children > 0,\
                'You must define a child node if you dont give a ref'
            assert number_children == 1,\
                'Only one child node is accepted (%d given)' % number_children

            node = rec[0]
            obj = None
            if model:
                obj = self.parent.pool.get(model)
            rtag = self._get_subtag(node)
            new_id = rtag.eval_xml(cr, node, obj)

        uid = self.parent.get_uid(cr, rec)
        wf_service = netsvc.LocalService("workflow")
        wf_service.trg_validate(uid, model, new_id, str(rec.get('action','')), cr)


class _tag_menuitem(_TagService):
    """

    Support two types of notation:
        name="Inventory Control/Sending Goods"
    or
        action="action_id"
        parent="parent_id"
    """
    _name = 'menuitem'

    __escape_re = re.compile(r'(?<!\\)/')
    @staticmethod
    def __escape(x):
        return x.replace('\\/', '/')

    def parse_xml(self, cr, rec):
        # FIXME
        # rec_id = rec.get("id",'')
        m_l = map(self.__escape, self.__escape_re.split(rec.get("name",'')))

        values = {'parent_id': False}
        id_get = self.parent.id_get
        if rec.get('parent', False) is False and len(m_l) > 1:
            # No parent attribute specified and the menu name has several menu components,
            # try to determine the ID of the parent according to menu path
            pid = False
            res = None
            values['name'] = m_l[-1]
            m_l = m_l[:-1] # last part is our name, not a parent
            for idx, menu_elem in enumerate(m_l):
                if pid:
                    cr.execute('select id from ir_ui_menu where parent_id=%s and name=%s', (pid, menu_elem))
                else:
                    cr.execute('select id from ir_ui_menu where parent_id is null and name=%s', (menu_elem,))
                res = cr.fetchone()
                if res:
                    pid = res[0]
                else:
                    # the menuitem does't exist but we are in branch (not a leaf)
                    self.logger.warning('Warning no ID for submenu %s of menu %s !', menu_elem, str(m_l))
                    pid = self.parent.pool.get('ir.ui.menu').create(cr, self.parent.uid,
                                    {'parent_id' : pid, 'name' : menu_elem})
            values['parent_id'] = pid
        else:
            # The parent attribute was specified, if non-empty determine its ID, otherwise
            # explicitly make a top-level menu
            if rec.get('parent'):
                menu_parent_id = id_get(cr, rec.get('parent',''))
            else:
                # we get here with <menuitem parent="">, explicit clear of parent, or
                # if no parent attribute at all but menu name is not a menu path
                menu_parent_id = False
            values = {'parent_id': menu_parent_id}
            if rec.get('name'):
                values['name'] = rec.get('name')
            try:
                res = [ id_get(cr, rec.get('id','')) ]
            except Exception:
                res = None

        if rec.get('action'):
            a_action = rec.get('action','')
            a_type = rec.get('type','') or 'act_window'
            icons = {
                "act_window": 'STOCK_NEW',
                "report.xml": 'STOCK_PASTE',
                "wizard": 'STOCK_EXECUTE',
                "url": 'STOCK_JUMP_TO'
            }
            values['icon'] = icons.get(a_type,'STOCK_NEW')
            if a_type=='act_window':
                a_id = id_get(cr, a_action)
                cr.execute('select view_type,view_mode,name,view_id,target from ir_act_window where id=%s', (int(a_id),))
                rrres = cr.fetchone()
                assert rrres, "No window action defined for this id %s !\n" \
                    "Verify that this is a window action or add a type argument." % (a_action,)
                action_type,action_mode,action_name,view_id,target = rrres
                if view_id:
                    cr.execute('SELECT type FROM ir_ui_view WHERE id=%s', (int(view_id),))
                    action_mode, = cr.fetchone() or ( False, )
                    if not action_mode:
                        raise Exception("View %d specified in ir.act.window[%d] not found!" % (view_id, a_id))
                cr.execute('SELECT view_mode FROM ir_act_window_view WHERE act_window_id=%s ORDER BY sequence LIMIT 1', (int(a_id),))
                if cr.rowcount:
                    action_mode, = cr.fetchone()
                if action_type=='tree': # TODO from osv.views
                    values['icon'] = 'STOCK_INDENT'
                elif action_mode and action_mode.startswith('tree'):
                    values['icon'] = 'STOCK_JUSTIFY_FILL'
                elif action_mode and action_mode.startswith('graph'):
                    values['icon'] = 'terp-graph'
                elif action_mode and action_mode.startswith('calendar'):
                    values['icon'] = 'terp-calendar'
                if target=='new':
                    values['icon'] = 'STOCK_EXECUTE'
                if not values.get('name', False):
                    values['name'] = action_name
            elif a_type=='wizard':
                a_id = id_get(cr, a_action)
                cr.execute('select name from ir_act_wizard where id=%s', (int(a_id),))
                resw = cr.fetchone()
                if (not values.get('name', False)) and resw:
                    values['name'] = resw[0]
        if rec.get('sequence'):
            values['sequence'] = int(rec.get('sequence'))
        if rec.get('icon'):
            values['icon'] = str(rec.get('icon'))
        if rec.get('web_icon'):
            values['web_icon'] = "%s,%s" %(self.parent.module_name, str(rec.get('web_icon')))
        if rec.get('web_icon_hover'):
            values['web_icon_hover'] = "%s,%s" %(self.parent.module_name, str(rec.get('web_icon_hover')))

        if rec.get('groups'):
            g_names = rec.get('groups','').split(',')
            groups_value = []
            # groups_obj = self.pool.get('res.groups')
            for group in g_names:
                if group.startswith('-'):
                    group_id = id_get(cr, group[1:])
                    groups_value.append((3, group_id))
                else:
                    group_id = id_get(cr, group)
                    groups_value.append((4, group_id))
            values['groups_id'] = groups_value

        xml_id = rec.get('id','')
        pid = self.parent.make_record(cr, 'ir.ui.menu', values, xml_id,
                    res_id=res and res[0] or False)

        if rec.get('action') and pid:
            a_action = rec.get('action')
            a_type = rec.get('type','') or 'act_window'
            a_id = id_get(cr, a_action)
            action = "ir.actions.%s,%d" % (a_type, a_id)
            self.ir_model_data.ir_set(cr, self.parent.uid, 'action',
                        'tree_but_open', 'Menuitem', [('ir.ui.menu', int(pid))],
                        action, True, True, xml_id=xml_id)
        return ('ir.ui.menu', pid)

class _tag_assert(_TagService):
    """ An assertion tag triggers a data check while XML is loading

        Otherwise, it should store no modification in the database.
    """
    _name = 'assert'
    def __init__(self, *args, **kwargs):
        super(_tag_assert, self).__init__(*args, **kwargs)
        self.tag_test = _TagService['assert.test'](self.parent)

    def _assert_equals(self, f1, f2, prec=4):
        return not round(f1 - f2, prec)

    def parse_xml(self, cr, rec):

        if self.parent.noupdate and self.parent.mode != 'init':
            return

        rec_model = rec.get("model",'')
        model = self.parent.pool.get(rec_model)
        assert model, "The model %s does not exist !" % (rec_model,)
        rec_id = rec.get("id",'')
        self.parent._test_xml_id(cr, rec_id)
        rec_src = rec.get("search",'')
        rec_src_count = rec.get("count")

        severity = rec.get("severity",'') or netsvc.LOG_ERROR
        rec_string = rec.get("string",'') or 'unknown'

        ids = None
        eval_dict = self.parent._get_eval_context(cr)
        context = self.parent.get_node_context(rec, eval_dict)
        uid = self.parent.get_uid(cr, rec)
        if rec_id:
            ids = [self.parent.id_get(cr, rec_id)]
        elif rec_src:
            q = unsafe_eval(rec_src, eval_dict)
            ids = self.parent.pool.get(rec_model).search(cr, uid, q, context=context)
            if rec_src_count:
                count = int(rec_src_count)
                if len(ids) != count:
                    self.parent.assert_report.record_assertion(False, severity)
                    msg = 'assertion "%s" failed!\n'    \
                          ' Incorrect search count:\n'  \
                          ' expected count: %d\n'       \
                          ' obtained count: %d\n'       \
                          % (rec_string, count, len(ids))
                    sevval = getattr(logging, severity.upper())
                    self.parent.logger.log(sevval, msg)
                    if sevval >= config['assert_exit_level']:
                        # TODO: define a dedicated exception
                        raise Exception('Severe assertion failure')
                    return

        assert ids is not None,\
            'You must give either an id or a search criteria'
        ref = lambda x: self.parent.id_get(cr, x)
        for id in ids:
            brrec =  model.browse(cr, uid, id, context)
            class d(dict):
                def __getitem__(self2, key):
                    if key in brrec:
                        return brrec[key]
                    return dict.__getitem__(self2, key)
            globals_dict = d()
            globals_dict['floatEqual'] = self._assert_equals
            globals_dict['ref'] = ref
            globals_dict['_ref'] = ref
            for test in rec.findall('./test'):
                f_expr = test.get("expr",'')
                expected_value = self.tag_test.eval_xml(cr, test, model, context=context) or True
                expression_value = unsafe_eval(f_expr, globals_dict)
                if expression_value != expected_value: # assertion failed
                    self.assert_report.record_assertion(False, severity)
                    msg = 'assertion "%s" failed!\n'    \
                          ' xmltag: %s\n'               \
                          ' expected value: %r\n'       \
                          ' obtained value: %r\n'       \
                          % (rec_string, etree.tostring(test), expected_value, expression_value)
                    sevval = getattr(logging, severity.upper(), logging.ERROR)
                    self.logger.log(sevval, msg)
                    if sevval >= config['assert_exit_level']:
                        # TODO: define a dedicated exception
                        raise Exception('Severe assertion failure')
                    return
        else: # all tests were successful for this assertion tag (no break)
            self.parent.assert_report.record_assertion(True, severity)

class _tag_record(_subtag_Mixin, _TagService):
    """ The most common tag, used to import arbitrary model data
    """
    _name = 'record'
    _subtag_prefix = 'fld.'

    def parse_xml(self, cr, rec):
        rec_model = rec.get("model")
        model = self.parent.pool.get(rec_model)
        assert model, "The model %s does not exist !" % (rec_model,)
        rec_id = rec.get("id",'')
        rec_context = self.parent.context.copy()
        if rec.get("context", None):
            rec_context.update(unsafe_eval(rec.get('context')))

        if self.parent.noupdate and self.parent.mode != 'init':
            # check if the xml record has an id string
            if rec_id:
                if '.' in rec_id:
                    module,rec_id2 = rec_id.split('.')
                else:
                    module = self.parent.module_name
                    rec_id2 = rec_id
                id = self.ir_model_data._update_dummy(cr, self.parent.uid, rec_model, module, rec_id2)
                # check if the resource already existed at the last update
                if id:
                    # if it existed, we don't update the data, but we need to
                    # know the id of the existing record anyway
                    self.parent.idref[rec_id] = int(id)
                    return None
                else:
                    # if the resource didn't exist
                    if not nodeattr2bool(rec, 'forcecreate', True):
                        # we don't want to create it, so we skip it
                        return None
                    # else, we let the record to be created

            else:
                # otherwise it is skipped
                return None
        rlist = []
        for node in rec:
            rtag = self._get_subtag(node)
            rval = rtag.eval_xml(cr, node, parent_model=model, context=rec_context)
            if rval:
                rlist.append(rval)

        new_id = self.parent.make_record(cr, rec_model, dict(rlist), rec_id, context=rec_context)
        return rec_model, new_id

class _tag_value(_TagService):
    """Set a value in a list, literal or computed one

        A value, so far, is an argument in a `<function>` element or
        a `<workflow>` one
    """
    _name = 'val.value'

    def parse_xml(self, cr, rec):
        """ A value cannot work outside a container
        """
        raise NotImplementedError

    def eval_xml(self, cr, rec, parent_model=None, context=None):
        """

            A value is computed by the following attributes, in that
            order:

                - search:
                - eval:
                - ref
                - file

        """
        f_name = rec.get('name', False)
        f_model = rec.get("model", False)
        f_col = None
        if parent_model:
            f_col = parent_model._columns.get(f_name, None)
            if f_col is None:
                f_col = parent_model._inherit_fields.get(f_name, (None, None, None))[2]
            if (not f_model) and f_name and f_col:
                f_model = f_col._obj

        if rec.get('search'):
            q = self.eval(rec.get('search'))
            if not f_model:
                raise ValueError("Undefined ORM model for field %s in XML" % f_name)
            f_obj = self.parent.pool.get(f_model)
            # search-browse the objects, but amend for the case q == []
            if q == []:
                q = [True,]
            s = f_obj.browse(cr, self.parent.uid, q, context=self.parent.context)
            # column definitions of the "local" object
            _cols = parent_model._columns
            f_use = rec.get('use', 'id')
            f_val = False
            if not f_col:
                self.parent.logger.warn("Value for non-existent field %s.%s specified", parent_model._name, f_name)
            elif f_col.required and not len(s):
                raise ValueError("No values found for %s.%s: %s=%r" %(parent_model._name, f_name, f_model, q))
            # if the current field is many2many
            elif f_col and f_col._type=='many2many':
                f_val = [(6, 0, map(lambda x: x[f_use], s))]
            elif len(s):
                # otherwise (we are probably in a many2one field),
                # take the first element of the search
                f_val = s[0][f_use]
            return f_val
        elif rec.get("ref",''):
            f_ref = rec.get('ref')
            if f_ref == "null":
                return False
            else:
                if f_col and f_col._type == 'reference':
                    val = self.parent.model_id_get(cr, f_ref)
                    return val[0] + ',' + str(val[1])
                else:
                    return self.parent.id_get(cr, f_ref)
        elif rec.get('eval'):
            model = parent_model
            if f_model:
                model = self.parent.pool.get(f_model)
            return self.eval(rec.get('eval'), cr=cr, model=model)
        elif rec.get('datetime', False):
            return date_eval(rec.get('datetime'))
        elif rec.get('date', False):
            return date_eval(rec.get('date')).date()
        elif rec.get('time', False):
            return date_eval(rec.get('time')).time()
        elif rec.get('file'):
            fp = file_open(self.parent.module_name + '/' + rec.get('file'))
            try:
                return fp.read()
            finally:
                fp.close()
        else:
            model = parent_model
            if f_model:
                model = self.parent.pool.get(f_model)
            return self.eval_literal(cr, rec, model, context)

    __xml_reference_re = re.compile(r'(?<!%)%\((.*?)\)[ds]')

    def eval_literal(self, cr, rec, model=None, context=None):
        """ Parse the contained elements of <value> into some value

            In the most common text, the text of the element is literally
            passed as a value.
        """
        t = rec.get('type', 'char')
        if t == 'xml':
            def sub_fn(m):
                return str(self.parent.id_get(cr, m.group(1)))
            _fix_multiple_roots(rec)
            return '<?xml version="1.0"?>\n' \
                +"".join([ self.__xml_reference_re.sub(sub_fn,
                                    etree.tostring(n, encoding='utf-8'))
                                for n in rec])
        if t in ('char', 'int', 'float'):
            d = rec.text
            if t == 'int':
                d = d.strip()
                if d == 'None':
                    return None
                else:
                    return int(d)
            elif t == 'float':
                d = d.strip()
                if not d:
                    return None
                return float(d)
            return d
        elif t in ('list','tuple'):
            res=[]
            for n in rec.findall('./value'):
                res.append(self.eval_xml(cr, n, model, context))
            if t == 'tuple':
                return tuple(res)
            return res
        else:
            raise NotImplementedError("Unknown literal type \"%s\" for record: %s" % \
                    (t, etree.tostring(rec)))

class _tag_field(_TagService):
    """Process the <field> tag

        A 'field' tag is found in a `<record>` element or an `<ir_set>` one.

        It behaves mostly like the `<value>` one, accepts the same attributes
        and inner elements. But it returns a `(name, value)` pair instead,
        since the field represents a `dictionary entry`.
    """
    _name = 'fld.field'
    _inherit = 'val.value'

    def eval_xml(self, cr, rec, parent_model=None, context=None):
        if nodeattr2bool(rec, "noupdate", False) and self.parent.mode != 'init':
            return None
        f_name = rec.get("name",'')
        res = super(_tag_field, self).eval_xml(cr, rec, parent_model, context)
        return (f_name, res)

class _tag_assert_test(_TagService):
    """ Let the <test> element of <assert> parse like a value
    """
    _name = 'assert.test'
    _inherit = 'val.value'
#eof
