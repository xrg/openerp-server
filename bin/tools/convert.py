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

import cStringIO
import csv
import logging
import os.path
import pickle
import re
# import sys

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
from lxml import etree
# import ir
import misc
import netsvc
import osv
import pooler
from config import config
from tools.translate import _
from yaml_import import convert_yaml_import

# List of etree._Element subclasses that we choose to ignore when parsing XML.
from tools import SKIPPED_ELEMENT_TYPES, cache

# Import of XML records requires the unsafe eval as well,
# almost everywhere, which is ok because it supposedly comes
# from trusted data, but at least we make it obvious now.
unsafe_eval = eval
from tools.safe_eval import safe_eval as eval

class ConvertError(Exception):
    def __init__(self, doc, orig_excpt):
        self.d = doc
        self.orig = orig_excpt

    def __str__(self):
        return 'Exception:\n\t%s\nUsing file:\n%s' % (self.orig, self.d)

def _ref(self, cr):
    return lambda x: self.id_get(cr, x)

def _obj(pool, cr, uid, model_str, context=None):
    model = pool.get(model_str)
    return lambda x: model.browse(cr, uid, x, context=context)

def _get_idref(self, cr, uid, model_str, context, idref):
    idref2 = dict(idref,
                  time=time,
                  DateTime=datetime,
                  timedelta=timedelta,
                  version=release.major_version,
                  ref=_ref(self, cr),
                  pytz=pytz)
    if len(model_str):
        idref2['obj'] = _obj(self.pool, cr, uid, model_str, context=context)
    return idref2

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

def _eval_xml(self, node, pool, cr, uid, idref, context=None):
    if context is None:
        context = {}
    if node.tag in ('field','value'):
        t = node.get('type','char')
        f_model = node.get('model', '')
        if node.get('search'):
            f_search = node.get("search",'')
            f_use = node.get("use",'id')
            f_name = node.get("name",'')
            idref2 = {}
            if f_search:
                idref2 = _get_idref(self, cr, uid, f_model, context, idref)
            q = unsafe_eval(f_search, idref2)
            ids = pool.get(f_model).search(cr, uid, q)
            if f_use != 'id':
                ids = map(lambda x: x[f_use], pool.get(f_model).read(cr, uid, ids, [f_use]))
            _cols = pool.get(f_model)._columns
            if (f_name in _cols) and _cols[f_name]._type=='many2many':
                return ids
            f_val = False
            if len(ids):
                f_val = ids[0]
                if isinstance(f_val, tuple):
                    f_val = f_val[0]
            return f_val
        a_eval = node.get('eval','')
        idref2 = {}
        if a_eval:
            idref2 = _get_idref(self, cr, uid, f_model, context, idref)
            try:
                return unsafe_eval(a_eval, idref2)
            except Exception:
                logger = logging.getLogger('init')
                logger.warning('could not eval(%s) for %s in %s' % (a_eval, node.get('name'), context), exc_info=True)
                return ""
        if t == 'xml':
            def _process(s, idref):
                m = re.findall('[^%]%\((.*?)\)[ds]', s)
                for id in m:
                    if not id in idref:
                        idref[id]=self.id_get(cr, id)
                return s % idref
            _fix_multiple_roots(node)
            return '<?xml version="1.0"?>\n'\
                +_process("".join([etree.tostring(n, encoding='utf-8')
                                   for n in node]),
                          idref)
        if t in ('char', 'int', 'float'):
            d = node.text
            if t == 'int':
                d = d.strip()
                if d == 'None':
                    return None
                else:
                    return int(d.strip())
            elif t == 'float':
                return float(d.strip())
            return d
        elif t in ('list','tuple'):
            res=[]
            for n in node.findall('./value'):
                res.append(_eval_xml(self,n,pool,cr,uid,idref))
            if t=='tuple':
                return tuple(res)
            return res
    elif node.tag == "getitem":
        for n in node:
            res=_eval_xml(self,n,pool,cr,uid,idref)
        if not res:
            raise LookupError
        elif node.get('type') in ("int", "list"):
            return res[int(node.get('index'))]
        else:
            return res[node.get('index','')]
    elif node.tag == "function":
        args = []
        a_eval = node.get('eval','')
        if a_eval:
            idref['ref'] = lambda x: self.id_get(cr, x)
            args = unsafe_eval(a_eval, idref)
        for n in node:
            return_val = _eval_xml(self,n, pool, cr, uid, idref, context)
            if return_val is not None:
                args.append(return_val)
        model = pool.get(node.get('model',''))
        method = node.get('name','')
        res = getattr(model, method)(cr, uid, *args)
        return res
    elif node.tag == "test":
        return node.text

escape_re = re.compile(r'(?<!\\)/')
def escape(x):
    return x.replace('\\/', '/')

class assertion_report(object):
    def __init__(self):
        self._report = {}

    def record_assertion(self, success, severity):
        """
            Records the result of an assertion for the failed/success count
            returns success
        """
        if severity in self._report:
            self._report[severity][success] += 1
        else:
            self._report[severity] = {success:1, not success: 0}
        return success

    def get_report(self):
        return self._report

    def __str__(self):
        res = '\nAssertions report:\nLevel\tsuccess\tfailed\n'
        success = failed = 0
        for sev in self._report:
            res += sev + '\t' + str(self._report[sev][True]) + '\t' + str(self._report[sev][False]) + '\n'
            success += self._report[sev][True]
            failed += self._report[sev][False]
        res += 'total\t' + str(success) + '\t' + str(failed) + '\n'
        res += 'end of report (' + str(success + failed) + ' assertion(s) checked)'
        return res

class xml_import(object):
    __logger = logging.getLogger('tools.convert.xml_import')
    @staticmethod
    def nodeattr2bool(node, attr, default=False):
        if not node.get(attr):
            return default
        val = node.get(attr).strip()
        if not val:
            return default
        return val.lower() not in ('0', 'false', 'off')

    def isnoupdate(self, data_node=None):
        return self.noupdate or (len(data_node) and self.nodeattr2bool(data_node, 'noupdate', False))

    def get_context(self, data_node, node, eval_dict):
        data_node_context = (len(data_node) and data_node.get('context',''))
        node_context = node.get("context",'')
        context = {}
        for ctx in (data_node_context, node_context):
            if ctx:
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

    def get_uid(self, cr, uid, data_node, node):
        node_uid = node.get('uid','') or (len(data_node) and data_node.get('uid',''))
        if node_uid:
            return self.id_get(cr, node_uid)
        return uid

    def _test_xml_id(self, xml_id):
        id = xml_id
        if '.' in xml_id:
            module, id = xml_id.split('.', 1)
            assert '.' not in id, """The ID reference "%s" must contain
maximum one dot. They are used to refer to other modules ID, in the
form: module.record_id""" % (xml_id,)
            if module != self.module:
                modcnt = self.pool.get('ir.module.module').search_count(self.cr, self.uid, ['&', ('name', '=', module), ('state', 'in', ['installed'])])
                assert modcnt == 1, """The ID "%s" refers to an uninstalled module""" % (xml_id,)

        if len(id) > 64:
            self.logger.error('id: %s is to long (max: 64)', id)

    def _tag_delete(self, cr, rec, data_node=None):
        d_model = rec.get("model",'')
        d_search = rec.get("search",'')
        d_id = rec.get("id",'')
        ids = []

        if d_search:
            idref = _get_idref(self, cr, self.uid, d_model, context={}, idref={})
            ids = self.pool.get(d_model).search(cr, self.uid, unsafe_eval(d_search, idref))
        if d_id:
            try:
                ids.append(self.id_get(cr, d_id))
            except Exception:
                # d_id cannot be found. doesn't matter in this case
                pass
        if ids:
            self.pool.get(d_model).unlink(cr, self.uid, ids)
            self.pool.get('ir.model.data')._unlink(cr, self.uid, d_model, ids)

    def _remove_ir_values(self, cr, name, value, model):
        ir_value_ids = self.pool.get('ir.values').search(cr, self.uid, [('name','=',name),('value','=',value),('model','=',model)])
        if ir_value_ids:
            self.pool.get('ir.values').unlink(cr, self.uid, ir_value_ids)
            self.pool.get('ir.model.data')._unlink(cr, self.uid, 'ir.values', ir_value_ids)

        return True

    def _tag_report(self, cr, rec, data_node=None):
        res = {}
        for dest,f in (('name','string'),('model','model'),('report_name','name')):
            res[dest] = rec.get(f,'')
            assert res[dest], "Attribute %s of report is empty !" % (f,)
        for field,dest in (('rml','report_rml'),('file','report_rml'),('xml','report_xml'),('xsl','report_xsl'),('attachment','attachment'),('attachment_use','attachment_use')):
            if rec.get(field):
                res[dest] = rec.get(field)
        if rec.get('auto'):
            res['auto'] = eval(rec.get('auto','False'))
        if rec.get('sxw'):
            sxw_content = misc.file_open(rec.get('sxw')).read()
            res['report_sxw_content'] = sxw_content
        if rec.get('header'):
            res['header'] = eval(rec.get('header','False'))
        if rec.get('report_type'):
            res['report_type'] = rec.get('report_type')

        res['multi'] = rec.get('multi') and eval(rec.get('multi','False'))

        xml_id = rec.get('id','')
        self._test_xml_id(xml_id)

        if rec.get('groups'):
            g_names = rec.get('groups','').split(',')
            groups_value = []
            # groups_obj = self.pool.get('res.groups')
            for group in g_names:
                if group.startswith('-'):
                    group_id = self.id_get(cr, group[1:])
                    groups_value.append((3, group_id))
                else:
                    group_id = self.id_get(cr, group)
                    groups_value.append((4, group_id))
            res['groups_id'] = groups_value

        id = self.pool.get('ir.model.data')._update(cr, self.uid, 
                "ir.actions.report.xml", self.module, res, xml_id, 
                noupdate=self.isnoupdate(data_node), mode=self.mode,
                context=self.context)
        self.idref[xml_id] = int(id)

        if not rec.get('menu') or eval(rec.get('menu','False')):
            keyword = str(rec.get('keyword', 'client_print_multi'))
            # keys = [('action',keyword),('res_model',res['model'])]
            value = 'ir.actions.report.xml,'+str(id)
            replace = rec.get('replace', True)
            self.pool.get('ir.model.data').ir_set(cr, self.uid, 'action', keyword, res['name'], [res['model']], value, replace=replace, isobject=True, xml_id=xml_id)
        elif self.mode=='update' and eval(rec.get('menu','False'))==False:
            # Special check for report having attribute menu=False on update
            value = 'ir.actions.report.xml,'+str(id)
            self._remove_ir_values(cr, res['name'], value, res['model'])
        return False

    def _tag_function(self, cr, rec, data_node=None):
        if self.isnoupdate(data_node) and self.mode != 'init':
            return
        context = self.get_context(data_node, rec, {'ref': _ref(self, cr)})
        uid = self.get_uid(cr, self.uid, data_node, rec)
        _eval_xml(self,rec, self.pool, cr, uid, self.idref, context=context)
        return

    def _tag_wizard(self, cr, rec, data_node=None):
        string = rec.get("string",'')
        model = rec.get("model",'')
        name = rec.get("name",'')
        xml_id = rec.get('id','')
        self._test_xml_id(xml_id)
        multi = rec.get('multi','') and eval(rec.get('multi','False'))
        res = {'name': string, 'wiz_name': name, 'multi': multi, 'model': model}

        if rec.get('groups'):
            g_names = rec.get('groups','').split(',')
            groups_value = []
            # groups_obj = self.pool.get('res.groups')
            for group in g_names:
                if group.startswith('-'):
                    group_id = self.id_get(cr, group[1:])
                    groups_value.append((3, group_id))
                else:
                    group_id = self.id_get(cr, group)
                    groups_value.append((4, group_id))
            res['groups_id'] = groups_value

        id = self.pool.get('ir.model.data')._update(cr, self.uid,
                "ir.actions.wizard", self.module, res, xml_id,
                noupdate=self.isnoupdate(data_node), mode=self.mode,
                context=self.context)
        self.idref[xml_id] = int(id)
        # ir_set
        if (not rec.get('menu') or eval(rec.get('menu','False'))) and id:
            keyword = str(rec.get('keyword','') or 'client_action_multi')
            value = 'ir.actions.wizard,'+str(id)
            replace = rec.get("replace",'') or True
            self.pool.get('ir.model.data').ir_set(cr, self.uid, 'action', keyword, string, [model], value, replace=replace, isobject=True, xml_id=xml_id)
        elif self.mode=='update' and (rec.get('menu') and eval(rec.get('menu','False'))==False):
            # Special check for wizard having attribute menu=False on update
            value = 'ir.actions.wizard,'+str(id)
            self._remove_ir_values(cr, string, value, model)

    def _tag_url(self, cr, rec, data_node=None):
        url = rec.get("string",'')
        target = rec.get("target",'')
        name = rec.get("name",'')
        xml_id = rec.get('id','')
        self._test_xml_id(xml_id)

        res = {'name': name, 'url': url, 'target':target}

        id = self.pool.get('ir.model.data')._update(cr, self.uid,
                "ir.actions.url", self.module, res, xml_id, 
                noupdate=self.isnoupdate(data_node), mode=self.mode,
                context=self.context)
        self.idref[xml_id] = int(id)
        # ir_set
        if (not rec.get('menu') or eval(rec.get('menu','False'))) and id:
            keyword = str(rec.get('keyword','') or 'client_action_multi')
            value = 'ir.actions.url,'+str(id)
            replace = rec.get("replace",'') or True
            self.pool.get('ir.model.data').ir_set(cr, self.uid, 
                    'action', keyword, url, ["ir.actions.url"], value, 
                    replace=replace, isobject=True, xml_id=xml_id)
        elif self.mode=='update' and (rec.get('menu') and eval(rec.get('menu','False'))==False):
            # Special check for URL having attribute menu=False on update
            value = 'ir.actions.url,'+str(id)
            self._remove_ir_values(cr, url, value, "ir.actions.url")

    def _tag_act_window(self, cr, rec, data_node=None):
        name = rec.get('name','')
        xml_id = rec.get('id','')
        self._test_xml_id(xml_id)
        type = rec.get('type','') or 'ir.actions.act_window'
        view_id = False
        if rec.get('view_id'):
            view_id = self.id_get(cr, rec.get('view_id',''))
        domain = rec.get('domain','') or '[]'
        res_model = rec.get('res_model','')
        src_model = rec.get('src_model','')
        view_type = rec.get('view_type','') or 'form'
        view_mode = rec.get('view_mode','') or 'tree,form'
        usage = rec.get('usage','')
        limit = rec.get('limit','')
        auto_refresh = rec.get('auto_refresh','')
        uid = self.uid
        # def ref() added because , if context has ref('id') eval wil use this ref

        active_id = str("active_id") # for further reference in client/bin/tools/__init__.py

        def ref(str_id):
            return self.id_get(cr, str_id)

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
            'ref' : ref,
        }
        context = self.get_context(data_node, rec, eval_context)

        try:
            domain = unsafe_eval(domain, eval_context)
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
                    group_id = self.id_get(cr, group[1:])
                    groups_value.append((3, group_id))
                else:
                    group_id = self.id_get(cr, group)
                    groups_value.append((4, group_id))
            res['groups_id'] = groups_value

        if rec.get('target'):
            res['target'] = rec.get('target','')
        if rec.get('multi'):
            res['multi'] = rec.get('multi', False)
        id = self.pool.get('ir.model.data')._update(cr, self.uid,
                'ir.actions.act_window', self.module, res, xml_id,
                noupdate=self.isnoupdate(data_node), mode=self.mode,
                context=self.context)
        self.idref[xml_id] = int(id)

        if src_model:
            #keyword = 'client_action_relate'
            keyword = rec.get('key2','') or 'client_action_relate'
            value = 'ir.actions.act_window,'+str(id)
            replace = rec.get('replace','') or True
            self.pool.get('ir.model.data').ir_set(cr, self.uid, 'action', keyword, xml_id, [src_model], value, replace=replace, isobject=True, xml_id=xml_id)
        # TODO add remove ir.model.data

    def _tag_ir_set(self, cr, rec, data_node=None):
        if self.mode != 'init':
            return
        res = {}
        for field in rec.findall('./field'):
            f_name = field.get("name",'')
            f_val = _eval_xml(self,field,self.pool, cr, self.uid, self.idref)
            res[f_name] = f_val
        self.pool.get('ir.model.data').ir_set(cr, self.uid, res['key'], res['key2'], res['name'], res['models'], res['value'], replace=res.get('replace',True), isobject=res.get('isobject', False), meta=res.get('meta',None))

    def _tag_workflow(self, cr, rec, data_node=None):
        if self.isnoupdate(data_node) and self.mode != 'init':
            return
        model = str(rec.get('model',''))
        w_ref = rec.get('ref','')
        if w_ref:
            id = self.id_get(cr, w_ref)
        else:
            number_children = len(rec)
            assert number_children > 0,\
                'You must define a child node if you dont give a ref'
            assert number_children == 1,\
                'Only one child node is accepted (%d given)' % number_children
            id = _eval_xml(self, rec[0], self.pool, cr, self.uid, self.idref)

        uid = self.get_uid(cr, self.uid, data_node, rec)
        wf_service = netsvc.LocalService("workflow")
        wf_service.trg_validate(uid, model, id, str(rec.get('action','')), cr)

    #
    # Support two types of notation:
    #   name="Inventory Control/Sending Goods"
    # or
    #   action="action_id"
    #   parent="parent_id"
    #
    def _tag_menuitem(self, cr, rec, data_node=None):
        rec_id = rec.get("id",'')
        self._test_xml_id(rec_id)
        m_l = map(escape, escape_re.split(rec.get("name",'')))

        values = {'parent_id': False}
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
                    pid = self.pool.get('ir.ui.menu').create(cr, self.uid, {'parent_id' : pid, 'name' : menu_elem})
            values['parent_id'] = pid
        else:
            # The parent attribute was specified, if non-empty determine its ID, otherwise
            # explicitly make a top-level menu
            if rec.get('parent'):
                menu_parent_id = self.id_get(cr, rec.get('parent',''))
            else:
                # we get here with <menuitem parent="">, explicit clear of parent, or
                # if no parent attribute at all but menu name is not a menu path
                menu_parent_id = False
            values = {'parent_id': menu_parent_id}
            if rec.get('name'):
                values['name'] = rec.get('name')
            try:
                res = [ self.id_get(cr, rec.get('id','')) ]
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
                a_id = self.id_get(cr, a_action)
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
                if action_type=='tree':
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
                a_id = self.id_get(cr, a_action)
                cr.execute('select name from ir_act_wizard where id=%s', (int(a_id),))
                resw = cr.fetchone()
                if (not values.get('name', False)) and resw:
                    values['name'] = resw[0]
        if rec.get('sequence'):
            values['sequence'] = int(rec.get('sequence'))
        if rec.get('icon'):
            values['icon'] = str(rec.get('icon'))
        if rec.get('web_icon'):
            values['web_icon'] = "%s,%s" %(self.module, str(rec.get('web_icon')))
        if rec.get('web_icon_hover'):
            values['web_icon_hover'] = "%s,%s" %(self.module, str(rec.get('web_icon_hover')))

        if rec.get('groups'):
            g_names = rec.get('groups','').split(',')
            groups_value = []
            # groups_obj = self.pool.get('res.groups')
            for group in g_names:
                if group.startswith('-'):
                    group_id = self.id_get(cr, group[1:])
                    groups_value.append((3, group_id))
                else:
                    group_id = self.id_get(cr, group)
                    groups_value.append((4, group_id))
            values['groups_id'] = groups_value

        xml_id = rec.get('id','')
        self._test_xml_id(xml_id)
        pid = self.pool.get('ir.model.data')._update(cr, self.uid, 
                'ir.ui.menu', self.module, values, xml_id,
                noupdate=self.isnoupdate(data_node), mode=self.mode,
                res_id=res and res[0] or False,
                context=self.context)

        if rec_id and pid:
            self.idref[rec_id] = int(pid)

        if rec.get('action') and pid:
            a_action = rec.get('action')
            a_type = rec.get('type','') or 'act_window'
            a_id = self.id_get(cr, a_action)
            action = "ir.actions.%s,%d" % (a_type, a_id)
            self.pool.get('ir.model.data').ir_set(cr, self.uid, 'action', 'tree_but_open', 'Menuitem', [('ir.ui.menu', int(pid))], action, True, True, xml_id=rec_id)
        return ('ir.ui.menu', pid)

    def _assert_equals(self, f1, f2, prec=4):
        return not round(f1 - f2, prec)

    def _tag_assert(self, cr, rec, data_node=None):
        if self.isnoupdate(data_node) and self.mode != 'init':
            return

        rec_model = rec.get("model",'')
        model = self.pool.get(rec_model)
        assert model, "The model %s does not exist !" % (rec_model,)
        rec_id = rec.get("id",'')
        self._test_xml_id(rec_id)
        rec_src = rec.get("search",'')
        rec_src_count = rec.get("count")

        severity = rec.get("severity",'') or netsvc.LOG_ERROR
        rec_string = rec.get("string",'') or 'unknown'

        ids = None
        eval_dict = {'ref': _ref(self, cr)}
        context = self.get_context(data_node, rec, eval_dict)
        uid = self.get_uid(cr, self.uid, data_node, rec)
        if rec_id:
            ids = [self.id_get(cr, rec_id)]
        elif rec_src:
            q = unsafe_eval(rec_src, eval_dict)
            ids = self.pool.get(rec_model).search(cr, uid, q, context=context)
            if rec_src_count:
                count = int(rec_src_count)
                if len(ids) != count:
                    self.assert_report.record_assertion(False, severity)
                    msg = 'assertion "%s" failed!\n'    \
                          ' Incorrect search count:\n'  \
                          ' expected count: %d\n'       \
                          ' obtained count: %d\n'       \
                          % (rec_string, count, len(ids))
                    sevval = getattr(logging, severity.upper())
                    self.logger.log(sevval, msg)
                    if sevval >= config['assert_exit_level']:
                        # TODO: define a dedicated exception
                        raise Exception('Severe assertion failure')
                    return

        assert ids is not None,\
            'You must give either an id or a search criteria'
        ref = _ref(self, cr)
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
                expected_value = _eval_xml(self, test, self.pool, cr, uid, self.idref, context=context) or True
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
            self.assert_report.record_assertion(True, severity)

    def _tag_record(self, cr, rec, data_node=None):
        rec_model = rec.get("model")
        model = self.pool.get(rec_model)
        assert model, "The model %s does not exist !" % (rec_model,)
        rec_id = rec.get("id",'')
        rec_context = self.context.copy()
        if rec.get("context", None):
            rec_context.update(unsafe_eval(rec.get('context')))
        self._test_xml_id(rec_id)
        if self.isnoupdate(data_node) and self.mode != 'init':
            # check if the xml record has an id string
            if rec_id:
                if '.' in rec_id:
                    module,rec_id2 = rec_id.split('.')
                else:
                    module = self.module
                    rec_id2 = rec_id
                id = self.pool.get('ir.model.data')._update_dummy(cr, self.uid, rec_model, module, rec_id2)
                # check if the resource already existed at the last update
                if id:
                    # if it existed, we don't update the data, but we need to
                    # know the id of the existing record anyway
                    self.idref[rec_id] = int(id)
                    return None
                else:
                    # if the resource didn't exist
                    if not self.nodeattr2bool(rec, 'forcecreate', True):
                        # we don't want to create it, so we skip it
                        return None
                    # else, we let the record to be created

            else:
                # otherwise it is skipped
                return None
        res = {}
        for field in rec.findall('./field'):
#TODO: most of this code is duplicated above (in _eval_xml)...
            f_name = field.get("name",'')
            f_ref = field.get("ref",'')
            f_search = field.get("search",'')
            f_model = field.get("model",'')
            f_fname = field.get("file", None)
            if not f_model and model._columns.get(f_name,False):
                f_model = model._columns[f_name]._obj
            f_use = field.get("use",'') or 'id'
            f_val = False

            if f_search:
                q = unsafe_eval(f_search, self.idref)
                field = []
                assert f_model, 'Define an attribute model="..." in your .XML file !'
                f_obj = self.pool.get(f_model)
                # search-browse the objects, but amend for the case q == []
                if q == []:
                    q = [True,]
                s = f_obj.browse(cr, self.uid, q)
                # column definitions of the "local" object
                _cols = self.pool.get(rec_model)._columns
                # if the current field is many2many
                if (f_name in _cols) and _cols[f_name]._type=='many2many':
                    f_val = [(6, 0, map(lambda x: x[f_use], s))]
                elif len(s):
                    # otherwise (we are probably in a many2one field),
                    # take the first element of the search
                    f_val = s[0][f_use]
            elif f_ref:
                if f_ref=="null":
                    f_val = False
                else:
                    if f_name in model._columns \
                              and model._columns[f_name]._type == 'reference':
                        val = self.model_id_get(cr, f_ref)
                        f_val = val[0] + ',' + str(val[1])
                    else:
                        f_val = self.id_get(cr, f_ref)
            elif f_fname:
                fp = misc.file_open(self.module+'/' + f_fname)
                f_val = fp.read()
                fp.close()
            else:
                f_val = _eval_xml(self,field, self.pool, cr, self.uid, self.idref)
                if model._columns.has_key(f_name):
                    if isinstance(model._columns[f_name], osv.fields.integer):
                        f_val = int(f_val)
            res[f_name] = f_val

        id = self.pool.get('ir.model.data')._update(cr, self.uid,
                rec_model, self.module, res, rec_id or False,
                not self.isnoupdate(data_node),
                noupdate=self.isnoupdate(data_node),
                mode=self.mode, context=rec_context )
        if rec_id:
            self.idref[rec_id] = int(id)
        if config.get('import_partial', False):
            cr.commit()
        return rec_model, id

    def id_get(self, cr, id_str):
        if id_str in self.idref:
            return self.idref[id_str]
        elif isinstance(id_str, (int, long)):
            return id_str
        mid = self.model_id_get(cr, id_str)
        if not mid:
            raise KeyError("Id %s not found in db" %(id_str, ))
        return mid[1]

    def model_id_get(self, cr, id_str):
        model_data_obj = self.pool.get('ir.model.data')
        mod = self.module
        if '.' in id_str:
            mod,id_str = id_str.split('.')
        return model_data_obj.get_object_reference(cr, self.uid, mod, id_str)

    def parse(self, de):
        if not de.tag in ['terp', 'openerp']:
            self.logger.error("Mismatch xml format")
            raise Exception( "Mismatch xml format: only terp or openerp as root tag" )

        if de.tag == 'terp':
            self.logger.warning("The tag <terp/> is deprecated, use <openerp/>")

        for n in de.findall('./data'):
            if n.get('with-modules'):
                need_modules = [ x.strip() for x in n.get('with-modules').split(',')]
                need_modules = filter(bool, need_modules) # no empty entries
                if need_modules:
                    mod_obj = self.pool.get('ir.module.module')
                    mids = mod_obj.search(self.cr, self.uid, [('name','in', need_modules),
                                                        ('state','in', ('installed', 'to upgrade'))])
                    if len(mids) < len(need_modules):
                        self.logger.warning("Record at module %s skipped, because not all of %s module(s) are installed",
                            self.module, ', '.join(need_modules))
                        continue

            for rec in n:
                    if rec.tag in self._tags:
                        try:
                            self._tags[rec.tag](self.cr, rec, n)
                        except Exception:
                            self.__logger.debug("Tag exception:", exc_info=True)
                            self.__logger.error('Parse error in %s:%d: \n%s',
                                                rec.getroottree().docinfo.URL,
                                                rec.sourceline,
                                                etree.tostring(rec).strip(), exc_info=True)
                            self.cr.rollback()
                            cache.clean_caches_for_db(self.cr.dbname)
                            raise
        return True

    def __init__(self, cr, module, idref, mode, report=None, noupdate=False, context=None):

        self.logger = logging.getLogger('init')
        self.mode = mode
        self.module = module
        self.cr = cr
        self.idref = idref
        self.pool = pooler.get_pool(cr.dbname)
        self.uid = 1
        if report is None:
            report = assertion_report()
        self.assert_report = report
        self.noupdate = noupdate
        self.context = context or {}
        self._tags = {
            'menuitem': self._tag_menuitem,
            'record': self._tag_record,
            'assert': self._tag_assert,
            'report': self._tag_report,
            'wizard': self._tag_wizard,
            'delete': self._tag_delete,
            'ir_set': self._tag_ir_set,
            'function': self._tag_function,
            'workflow': self._tag_workflow,
            'act_window': self._tag_act_window,
            'url': self._tag_url
        }

def convert_csv_import(cr, module, fname, csvcontent, idref=None, mode='init',
        noupdate=False, context=None):
    '''Import csv file :
        quote: "
        delimiter: ,
        encoding: utf-8'''
    if not idref:
        idref={}
    logger = logging.getLogger('init')
    model = ('.'.join(fname.split('.')[:-1]).split('-'))[0]
    #remove folder path from model
    head, model = os.path.split(model)

    pool = pooler.get_pool(cr.dbname)

    if isinstance(csvcontent, unicode):
        csvcontent = csvcontent
    input = cStringIO.StringIO(csvcontent) #FIXME
    reader = csv.reader(input, quotechar='"', delimiter=',')
    fields = reader.next()
    fname_partial = ""
    if config.get('import_partial'):
        fname_partial = module + '/'+ fname
        if not os.path.isfile(config.get('import_partial')):
            pickle.dump({}, file(config.get('import_partial'),'w+'))
        else:
            data = pickle.load(file(config.get('import_partial')))
            if fname_partial in data:
                if not data[fname_partial]:
                    return
                else:
                    for i in range(data[fname_partial]):
                        reader.next()

    if not (mode == 'init' or 'id' in fields):
        logger.error("Import specification does not contain 'id' and we are in init mode, Cannot continue.")
        return

    uid = 1
    datas = []
    for line in reader:
        if (not line) or not reduce(lambda x,y: x or y, line) :
            continue
        try:
            datas.append(map(lambda x: misc.ustr(x), line))
        except Exception:
            logger.error("Cannot import the line: %s", line)
    result, rows, warning_msg, dummy = pool.get(model).import_data(cr, uid,
        fields, datas,mode, module, noupdate, filename=fname_partial, context=context)
    if result < 0:
        # Report failed import and abort module install
        raise Exception(_('Module loading failed: file %s/%s could not be processed:\n %s') % (module, fname, warning_msg))
    if config.get('import_partial'):
        data = pickle.load(file(config.get('import_partial')))
        data[fname_partial] = 0
        pickle.dump(data, file(config.get('import_partial'),'wb'))
        cr.commit()

#
# xml import/export
#
def convert_xml_import(cr, module, xmlfile, idref=None, mode='init', noupdate=False, report=None, context=None):
    doc = etree.parse(xmlfile)
    relaxng = etree.RelaxNG(
        etree.parse(os.path.join(config['root_path'],'import_xml.rng' )))
    try:
        relaxng.assert_(doc)
        # TODO: perhaps explicitly catch only RelaxNG exceptions
    except Exception:
        logger = logging.getLogger('init')
        logger.error('The XML file does not fit the required schema !\n%s', \
                misc.ustr(relaxng.error_log.last_error))
        raise

    if idref is None:
        idref={}
    obj = xml_import(cr, module, idref, mode, report=report, noupdate=noupdate, context=context)
    obj.parse(doc.getroot())
    return True

