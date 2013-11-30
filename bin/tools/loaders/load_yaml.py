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

#.apidoc title: YAML import

import types
import time # used to eval time.strftime expressions
# from datetime import datetime, timedelta
import logging

import netsvc
from tools import misc
from tools.config import config
from tools import yaml_tag
import yaml
from tools.data_loaders import DataLoader

try:
    from osv.osv import except_osv
except ImportError:
    class except_osv(Exception):
        pass

# YAML import needs both safe and unsafe eval, but let's
# default to /safe/. But always debug, YAML is for tests.
unsafe_eval = eval
from tools.safe_eval import safe_evalD as eval
from tools.safe_eval import ExecContext

logger_channel = 'tests'

class YamlImportException(Exception):
    pass

class YamlImportAbortion(Exception):
    pass

def _is_yaml_mapping(node, tag_constructor):
    value = isinstance(node, types.DictionaryType) \
        and len(node.keys()) == 1 \
        and isinstance(node.keys()[0], tag_constructor)
    return value

def is_comment(node):
    return isinstance(node, types.StringTypes)

def is_assert(node):
    return isinstance(node, yaml_tag.Assert) \
        or _is_yaml_mapping(node, yaml_tag.Assert)

def is_record(node):
    return _is_yaml_mapping(node, yaml_tag.Record)

def is_python(node):
    return _is_yaml_mapping(node, yaml_tag.Python)

def is_menuitem(node):
    return isinstance(node, yaml_tag.Menuitem) \
        or _is_yaml_mapping(node, yaml_tag.Menuitem)

def is_function(node):
    return isinstance(node, yaml_tag.Function) \
        or _is_yaml_mapping(node, yaml_tag.Function)

def is_report(node):
    return isinstance(node, yaml_tag.Report)

def is_workflow(node):
    return isinstance(node, yaml_tag.Workflow)

def is_act_window(node):
    return isinstance(node, yaml_tag.ActWindow)

def is_delete(node):
    return isinstance(node, yaml_tag.Delete)

def is_context(node):
    return isinstance(node, yaml_tag.Context)

def is_url(node):
    return isinstance(node, yaml_tag.Url)

def is_eval(node):
    return isinstance(node, yaml_tag.Eval)

def is_ref(node):
    return isinstance(node, yaml_tag.Ref) \
        or _is_yaml_mapping(node, yaml_tag.Ref)

def is_ir_set(node):
    return _is_yaml_mapping(node, yaml_tag.IrSet)

def is_repeat(node):
    return isinstance(node, yaml_tag.Repeat) \
        or _is_yaml_mapping(node, yaml_tag.Repeat)

def is_string(node):
    return isinstance(node, basestring)

class TestReport(object):
    def __init__(self):
        self._report = {}

    def record(self, success, severity):
        """
        Records the result of an assertion for the failed/success count.
        Returns success.
        """
        if isinstance(severity, basestring) and severity.isdigit():
            severity = int(severity)
        if isinstance(severity, int):
            severity = logging.getLevelName(severity)
        if severity in self._report:
            self._report[severity][success] += 1
        else:
            self._report[severity] = {success: 1, not success: 0}
        return success

    def __str__(self):
        res = []
        res.append('\nAssertions report:\nLevel\tsuccess\tfailure')
        success = failure = 0
        for severity in self._report:
            res.append("%s\t%s\t%s" % (severity, self._report[severity][True], self._report[severity][False]))
            success += self._report[severity][True]
            failure += self._report[severity][False]
        res.append("total\t%s\t%s" % (success, failure))
        res.append("end of report (%s assertion(s) checked)" % (success + failure))
        return "\n".join(res)

class RecordDictWrapper(dict):
    """
    Used to pass a record as locals in eval:
    records do not strictly behave like dict, so we force them to.
    """
    def __init__(self, record):
        self.record = record 
    def __getitem__(self, key):
        if key in self.record:
            return self.record[key]
        return dict.__getitem__(self, key)

class _LoaderExecContext(ExecContext):
    _name = 'loader_yaml'
    _inherit = 'loader_xml'

    def _obj(self, ids):
        return self.parent.pool.get(self.model).browse(self.cr, self.parent.uid, ids, \
                            context=self.parent.context)

    def prepare_context(self, context):
        super(_LoaderExecContext, self).prepare_context(context)
        if 'ref' in context:
            context['_ref'] = context['ref']
        
class YamlInterpreter(DataLoader):
    _name = 'yml'
    
    def __init__(self,*args, **kwargs):
        super(YamlInterpreter, self).__init__(*args, **kwargs)
        self.assert_report = TestReport()
        self.logger = logging.getLogger("%s.%s" % (logger_channel, self.module_name))
        self.exec_context = ExecContext['loader_yaml'](parent=self)
        self._eval_context = None # local

    def _get_eval_context(self, cr=None, model=None):
        if self._eval_context is None:
            self.exec_context.cr = cr
            self._eval_context = {}
            self.exec_context.prepare_context(self._eval_context)
        if model is not None:
            self.exec_context.cr = cr
            ret = self._eval_context.copy()
            ret['_obj'] = self._obj
            del self.exec_context.cr
            return ret
        else:
            return self._eval_context

    def _ref(self, cr):
        return lambda xml_id: self.get_id(cr, xml_id)

    def get_model(self, model_name):
        model = self.pool.get(model_name)
        assert model, "The model %s does not exist." % (model_name,)
        return model

    def validate_xml_id(self, cr, xml_id):
        id = xml_id
        if '.' in xml_id:
            module, id = xml_id.split('.', 1)
            assert '.' not in id, "The ID reference '%s' must contains maximum one dot.\n" \
                                  "It is used to refer to other modules ID, in the form: module.record_id" \
                                  % (xml_id,)
            if module != self.module_name:
                module_count = self.pool.get('ir.module.module').search_count(cr, self.uid, \
                        ['&', ('name', '=', module), ('state', 'in', ['installed'])])
                assert module_count == 1, 'The ID "%s" refers to an uninstalled module.' % (xml_id,)
        if len(id) > 64: # TODO where does 64 come from (DB is 128)? should be a constant or loaded form DB
            self.logger.log(logging.ERROR, 'id: %s is to long (max: 64)', id)

    def get_id(self, cr, xml_id):
        if xml_id is False:
            return False
        if is_eval(xml_id):
            xml_id = self.process_eval(cr, xml_id)
            # and continue, allow to be parsed/dereferenced

        if not xml_id:
            raise YamlImportException("The xml_id should be a non empty string.")
        if isinstance(xml_id, types.IntType):
            id = xml_id
        elif xml_id in self.idref:
            id = self.idref[xml_id]
        else:
            if '.' in xml_id:
                module, checked_xml_id = xml_id.split('.', 1)
            else:
                module = self.module_name
                checked_xml_id = xml_id
            _, id = self.pool.get('ir.model.data').get_object_reference(cr, self.uid, module, checked_xml_id)
            self.idref[xml_id] = id
        return id
    
    id_get = get_id # compatibility with XML loader
    
    def get_context(self, node, eval_dict):
        if self.context is None:
            context = {}
        else:
            context = self.context.copy()
        if node.context:
            if isinstance(node.context, dict):
                context.update(node.context)
            else:
                context.update(eval(node.context, eval_dict))
        return context

    def isnoupdate(self, node):
        return self.noupdate or node.noupdate or False
    
    def _get_first_result(self, results, default=False):
        if len(results):
            value = results[0]
            if isinstance(value, types.TupleType):
                value = value[0]
        else:
            value = default
        return value
    
    def process_comment(self, cr, node):
        return node

    def _log_assert_failure(self, severity, msg, *args):
        if isinstance(severity, basestring):
            levelname = severity.strip().upper()
            level = logging.getLevelName(levelname)
        else:
            level = severity
            levelname = logging.getLevelName(level)
        self.assert_report.record(False, levelname)
        self.logger.log(level, msg, *args)
        if level >= config['assert_exit_level']:
            raise YamlImportAbortion('Severe assertion failure (%s), aborting.' % levelname)
        return

    def _get_assertion_id(self, cr, assertion):
        if assertion.id:
            ids = [self.get_id(cr, assertion.id)]
        elif assertion.search:
            q = eval(assertion.search, self._get_eval_context(cr))
            ids = self.pool.get(assertion.model).search(cr, self.uid, q, context=assertion.context)
        else:
            raise YamlImportException('Nothing to assert: you must give either an id or a search criteria.')
        return ids

    def process_assert(self, cr, node):
        if isinstance(node, dict):
            assertion, expressions = node.items()[0]
        else:
            assertion, expressions = node, []

        if self.isnoupdate(assertion) and self.mode != 'init':
            self.logger.warn('This assertion was not evaluated ("%s").' % assertion.string)
            return
        model = self.get_model(assertion.model)
        ids = self._get_assertion_id(cr, assertion)
        if assertion.count is not None and len(ids) != assertion.count:
            msg = 'assertion "%s" failed!\n'   \
                  ' Incorrect search count:\n' \
                  ' expected count: %d\n'      \
                  ' obtained count: %d\n'
            args = (assertion.string, assertion.count, len(ids))
            self._log_assert_failure(assertion.severity, msg, *args)
        else:
            context = self.get_context(assertion, self._get_eval_context(cr))
            for id in ids:
                record = model.browse(cr, self.uid, id, context)
                for test in expressions:
                    try:
                        success = unsafe_eval(test, self._get_eval_context(cr), RecordDictWrapper(record))
                    except Exception, e:
                        self.logger.debug('Exception during evaluation of !assert block in yaml_file %s.', self.filename, exc_info=True)
                        raise YamlImportAbortion(e)
                    if not success:
                        msg = 'Assertion "%s" FAILED\ntest: %s\n'
                        args = (assertion.string, test)
                        for aop in ('==', '!=', '<>', 'in', 'not in', '>=', '<=', '>', '<'):
                            if aop in test:
                                left, right = test.split(aop,1)
                                lmsg = ''
                                rmsg = ''
                                try:
                                    lmsg = unsafe_eval(left, self._get_eval_context(cr), RecordDictWrapper(record))
                                except Exception, e:
                                    lmsg = '<exc>'

                                try:
                                    rmsg = unsafe_eval(right, self._get_eval_context(cr), RecordDictWrapper(record))
                                except Exception, e:
                                    rmsg = '<exc>'

                                msg += 'values: ! %s %s %s'
                                args += ( lmsg, aop, rmsg )
                                break

                        self._log_assert_failure(assertion.severity, msg, *args)
                        return
            else: # all tests were successful for this assertion tag (no break)
                self.assert_report.record(True, assertion.severity)

    def _coerce_bool(self, value, default=False):
        if isinstance(value, types.BooleanType):
            b = value
        if isinstance(value, types.StringTypes):
            b = value.strip().lower() not in ('0', 'false', 'off', 'no')
        elif isinstance(value, types.IntType):
            b = bool(value)
        else:
            b = default
        return b

    def create_osv_memory_record(self, cr, record, fields):
        model = self.get_model(record.model)
        record_dict = self._create_record(cr, model, fields)
        id_new=model.create(cr, self.uid, record_dict, context=self.context)
        self.idref[record.id] = int(id_new)
        return record_dict

    def process_record(self, cr, node):
        import osv
        record, fields = node.items()[0]
        rec_id = record.id
        if isinstance(rec_id, yaml_tag.Eval):
            record = yaml_tag.Record(**record.__dict__)
            record.id = self.process_eval(cr, rec_id)
        model = self.get_model(record.model)
        if isinstance(model, osv.osv.osv_memory):
            record_dict=self.create_osv_memory_record(cr, record, fields)
        else:
            self.validate_xml_id(cr, record.id)
            if self.isnoupdate(record) and self.mode != 'init':
                id = self.pool.get('ir.model.data')._update_dummy(cr, 1, record.model, self.module_name, record.id)
                # check if the resource already existed at the last update
                if id:
                    self.idref[record] = int(id)
                    return None
                else:
                    if not self._coerce_bool(record.forcecreate, (self.mode == 'test')):
                        return None

            record_dict = self._create_record(cr, model, fields)
            context = self.get_context(record, self._get_eval_context(cr))
            id = self.pool.get('ir.model.data')._update(cr, 1, record.model, \
                    self.module_name, record_dict, record.id, noupdate=self.isnoupdate(record), mode=self.mode, context=context)
            if model._debug:
                self.logger.debug("RECORD_DICT %s: #%r %s" % ( record.id, id, record_dict))
            self.idref[record.id] = int(id)

    def _create_record(self, cr, model, fields):
        record_dict = {}
        for field_name, expression in fields.items():
            field_value = self._eval_field(cr, model, field_name, expression)
            record_dict[field_name] = field_value
        return record_dict

    def process_ref(self, cr, node, column=None):
        if node.search:
            if node.model:
                model_name = node.model
            elif column:
                model_name = column._obj
            else:
                raise YamlImportException('You need to give a model for the search, or a column to infer it.')
            model = self.get_model(model_name)
            q = eval(node.search, self._get_eval_context(cr))
            ids = model.search(cr, self.uid, q)
            if node.use:
                instances = model.browse(cr, self.uid, ids)
                value = [inst[node.use] for inst in instances]
            else:
                value = ids
        elif node.id:
            value = self.get_id(cr, node.id)
        else:
            value = None
        return value

    def process_eval(self, cr, node, context=None):
        return eval(node.expression, context or self._get_eval_context(cr))

    def _eval_field(self, cr, model, field_name, expression):
        # TODO this should be refactored as something like model.get_field() in bin/osv
        if field_name in model._columns:
            column = model._columns[field_name]
        elif field_name in model._inherit_fields:
            column = model._inherit_fields[field_name][2]
        else:
            raise KeyError("Object '%s' does not contain field '%s'" % (model, field_name))
        if is_ref(expression):
            elements = self.process_ref(expression, column)
            if column._type in ("many2many", "one2many"):
                value = [(6, 0, elements)]
            else: # many2one
                value = self._get_first_result(elements)
        elif is_repeat(expression):
            elements = self._process_repeat_field(cr, expression, model, field_name)
            if column._type in ("many2many", "one2many"):
                value = elements
            else:
                value = self._get_first_result(elements)
        elif column._type == "many2one":
            value = self.get_id(cr, expression)
        elif column._type == "one2many":
            other_model = self.get_model(column._obj)
            value = [(0, 0, self._create_record(cr, other_model, fields)) for fields in expression]
        elif column._type == "many2many":
            ids = [self.get_id(cr, xml_id) for xml_id in expression]
            value = [(6, 0, ids)]
        elif column._type == "date" and is_string(expression):
            # enforce ISO format for string date values, to be locale-agnostic during tests
            time.strptime(expression, misc.DEFAULT_SERVER_DATE_FORMAT)
            value = expression
        elif column._type == "datetime" and is_string(expression):
            # enforce ISO format for string datetime values, to be locale-agnostic during tests
            time.strptime(expression, misc.DEFAULT_SERVER_DATETIME_FORMAT)
            value = expression
        else: # scalar field
            if is_eval(expression):
                value = self.process_eval(cr, expression)
            else:
                value = expression
            # raise YamlImportException('Unsupported column "%s" or value %s:%s' % (field_name, type(expression), expression))
        return value

    def process_context(self, cr, node):
        self.context = node.__dict__
        if node.uid:
            self.uid = self.get_id(cr, node.uid)
        if node.noupdate:
            self.noupdate = node.noupdate

    def process_python(self, cr, node):
        def log(msg, *args):
            self.logger.log(logging.TEST, msg, *args)
        python, statements = node.items()[0]
        model = self.get_model(python.model)
        statements = statements.replace("\r\n", "\n")
        if self.uid != 1:
            raise RuntimeError("Raw python is only permitted to admin!")
        code_context = {'model': model, 'cr': cr, 'uid': self.uid, 'log': log, 'context': self.context}
        code_context.update({'self': model}) # remove me when no !python block test uses 'self' anymore
        try:
            code_obj = compile(statements, self.filename, 'exec')
            unsafe_eval(code_obj, {'ref': lambda x: self.get_id(cr, x)}, code_context)
        except AssertionError, e:
            self._log_assert_failure(python.severity, 'AssertionError in Python code %s: %s', python.name, e)
            return
        except except_osv, eo:
            self.logger.debug('Exception during evaluation of !python block in yaml_file %s.', self.filename, exc_info=True)
            raise YamlImportAbortion(eo.value)
        except Exception, e:
            self.logger.debug('Exception during evaluation of !python block in yaml_file %s.', self.filename, exc_info=True)
            raise
        else:
            self.assert_report.record(True, python.severity)
    
    def process_workflow(self, cr, node):
        workflow, values = node.items()[0]
        if self.isnoupdate(workflow) and self.mode != 'init':
            return
        if workflow.ref:
            id = self.get_id(cr, workflow.ref)
        else:
            if not values:
                raise YamlImportException('You must define a child node if you do not give a ref.')
            if not len(values) == 1:
                raise YamlImportException('Only one child node is accepted (%d given).' % len(values))
            value = values[0]
            if not 'model' in value and (not 'eval' in value or not 'search' in value):
                raise YamlImportException('You must provide a "model" and an "eval" or "search" to evaluate.')
            value_model = self.get_model(value['model'])
            local_context = {'obj': lambda x: value_model.browse(cr, self.uid, x, context=self.context)}
            local_context.update(self.idref)
            id = eval(value['eval'], self._get_eval_context(cr), local_context)
        
        if workflow.uid is not None:
            uid = workflow.uid
        else:
            uid = self.uid
        cr.execute('select distinct signal from wkf_transition')
        signals=[x['signal'] for x in cr.dictfetchall()]
        if workflow.action not in signals:
            raise YamlImportException('Incorrect action %s. No such action defined' % workflow.action)
        wf_service = netsvc.LocalService("workflow")
        wf_service.trg_validate(uid, workflow.model, id, workflow.action, cr)
    
    def _eval_params(self, cr, model, params, context=None):
        args = []
        if context is None:
            context = self._get_eval_context(cr)
        for i, param in enumerate(params):
            if isinstance(param, types.ListType):
                value = self._eval_params(cr, model, param, context=context)
            elif is_ref(param):
                value = self.process_ref(param)
            elif is_eval(param):
                value = self.process_eval(cr, param, context=context)
            elif isinstance(param, types.DictionaryType): # supports XML syntax
                param_model = self.get_model(param.get('model', model))
                if 'search' in param:
                    q = eval(param['search'], context)
                    ids = param_model.search(cr, self.uid, q)
                    value = self._get_first_result(ids)
                elif 'eval' in param:
                    local_context = {'obj': lambda x: param_model.browse(cr, self.uid, x, self.context)}
                    local_context.update(self.idref)
                    value = eval(param['eval'], context, local_context)
                else:
                    raise YamlImportException('You must provide either a !ref or at least a "eval" or a "search" to function parameter #%d.' % i)
            else:
                value = param # scalar value
            args.append(value)
        return args
        
    def process_function(self, cr, node):
        function, params = node.items()[0]
        if self.isnoupdate(function) and self.mode != 'init':
            return
        model = self.get_model(function.model)
        context = self.get_context(function, self._get_eval_context(cr))
        if function.eval:
            args = self.process_eval(cr, function.eval, context=context)
        else:
            args = self._eval_params(cr, function.model, params, context=context)
        method = function.name
        getattr(model, method)(cr, self.uid, *args)
    
    def _set_group_values(self, cr, node, values):
        if node.groups:
            group_names = node.groups.split(',')
            groups_value = []
            for group in group_names:
                if group.startswith('-'):
                    group_id = self.get_id(cr, group[1:])
                    groups_value.append((3, group_id))
                else:
                    group_id = self.get_id(cr, group)
                    groups_value.append((4, group_id))
            values['groups_id'] = groups_value

    def process_menuitem(self, cr, node):
        self.validate_xml_id(cr, node.id)

        if not node.parent:
            parent_id = False
            cr.execute('select id from ir_ui_menu where parent_id is null and name=%s', (node.name,))
            res = cr.fetchone()
            values = {'parent_id': parent_id, 'name': node.name}
        else:
            parent_id = self.get_id(cr, node.parent)
            values = {'parent_id': parent_id}
            if node.name:
                values['name'] = node.name
            try:
                res = [ self.get_id(cr, node.id) ]
            except: # which exception ?
                res = None

        if node.action:
            action_type = node.type or 'act_window'
            icons = {
                "act_window": 'STOCK_NEW',
                "report.xml": 'STOCK_PASTE',
                "wizard": 'STOCK_EXECUTE',
                "url": 'STOCK_JUMP_TO',
            }
            values['icon'] = icons.get(action_type, 'STOCK_NEW')
            if action_type == 'act_window':
                action_id = self.get_id(cr, node.action)
                cr.execute('select view_type,view_mode,name,view_id,target from ir_act_window where id=%s', (action_id,))
                ir_act_window_result = cr.fetchone()
                assert ir_act_window_result, "No window action defined for this id %s !\n" \
                        "Verify that this is a window action or add a type argument." % (node.action,)
                action_type, action_mode, action_name, view_id, target = ir_act_window_result
                if view_id:
                    cr.execute('SELECT type FROM ir_ui_view WHERE id=%s', (view_id,))
                    # TODO guess why action_mode is ir_act_window.view_mode above and ir_ui_view.type here
                    action_mode = cr.fetchone()
                cr.execute('SELECT view_mode FROM ir_act_window_view WHERE act_window_id=%s ORDER BY sequence LIMIT 1', (action_id,))
                if cr.rowcount:
                    action_mode = cr.fetchone()
                if action_type == 'tree':
                    values['icon'] = 'STOCK_INDENT'
                elif action_mode and action_mode.startswith('tree'):
                    values['icon'] = 'STOCK_JUSTIFY_FILL'
                elif action_mode and action_mode.startswith('graph'):
                    values['icon'] = 'terp-graph'
                elif action_mode and action_mode.startswith('calendar'):
                    values['icon'] = 'terp-calendar'
                if target == 'new':
                    values['icon'] = 'STOCK_EXECUTE'
                if not values.get('name', False):
                    values['name'] = action_name
            elif action_type == 'wizard':
                action_id = self.get_id(cr, node.action)
                cr.execute('select name from ir_act_wizard where id=%s', (action_id,))
                ir_act_wizard_result = cr.fetchone()
                if (not values.get('name', False)) and ir_act_wizard_result:
                    values['name'] = ir_act_wizard_result[0]
            else:
                raise YamlImportException("Unsupported type '%s' in menuitem tag." % action_type)
        if node.sequence:
            values['sequence'] = node.sequence
        if node.icon:
            values['icon'] = node.icon

        self._set_group_values(cr, node, values)
        
        pid = self.pool.get('ir.model.data')._update(cr, 1, \
                'ir.ui.menu', self.module_name, values, node.id, mode=self.mode, \
                noupdate=self.isnoupdate(node), res_id=res and res[0] or False)

        if node.id and parent_id:
            self.idref[node.id] = int(parent_id)

        if node.action and pid:
            action_type = node.type or 'act_window'
            action_id = self.get_id(cr, node.action)
            action = "ir.actions.%s,%d" % (action_type, action_id)
            self.pool.get('ir.model.data').ir_set(cr, 1, 'action', \
                    'tree_but_open', 'Menuitem', [('ir.ui.menu', int(parent_id))], action, True, True, xml_id=node.id)

    def process_act_window(self, cr, node):
        assert getattr(node, 'id'), "Attribute %s of act_window is empty !" % ('id',)
        assert getattr(node, 'name'), "Attribute %s of act_window is empty !" % ('name',)
        assert getattr(node, 'res_model'), "Attribute %s of act_window is empty !" % ('res_model',)
        self.validate_xml_id(cr, node.id)
        view_id = False
        if node.view:
            view_id = self.get_id(cr, node.view)
        if not node.context:
            node.context={}
        context = eval(str(node.context), self._get_eval_context(cr))
        values = {
            'name': node.name,
            'type': node.type or 'ir.actions.act_window',
            'view_id': view_id,
            'domain': node.domain,
            'context': context,
            'res_model': node.res_model,
            'src_model': node.src_model,
            'view_type': node.view_type or 'form',
            'view_mode': node.view_mode or 'tree,form',
            'usage': node.usage,
            'limit': node.limit,
            'auto_refresh': node.auto_refresh,
            'multi': getattr(node, 'multi', False),
        }

        self._set_group_values(cr, node, values)

        if node.target:
            values['target'] = node.target
        id = self.pool.get('ir.model.data')._update(cr, 1, \
                'ir.actions.act_window', self.module_name, values, node.id, mode=self.mode)
        self.idref[node.id] = int(id)

        if node.src_model:
            keyword = 'client_action_relate'
            value = 'ir.actions.act_window,%s' % id
            replace = node.replace or True
            self.pool.get('ir.model.data').ir_set(cr, 1, 'action', keyword, \
                    node.id, [node.src_model], value, replace=replace, noupdate=self.isnoupdate(node), isobject=True, xml_id=node.id)
        # TODO add remove ir.model.data

    def process_delete(self, cr, node):
        assert getattr(node, 'model'), "Attribute %s of delete tag is empty !" % ('model',)
        mod_obj = self.pool.get(node.model)
        if mod_obj:
            if node.search and len(node.search):
                ids = mod_obj.search(cr, self.uid, eval(node.search, self._get_eval_context(cr)))
            else:
                ids = [self.get_id(cr, node.id)]
            if mod_obj._debug:
                self.logger.debug("Asking to unlink from %s ids: %r", node.model, ids)
            if len(ids):
                mod_obj.unlink(cr, self.uid, ids)
                self.pool.get('ir.model.data')._unlink(cr, 1, node.model, ids)
        else:
            self.logger.log(logging.TEST, "Record not deleted. No model %s" % node.model)
    
    def process_url(self, cr, node):
        self.validate_xml_id(cr, node.id)

        res = {'name': node.name, 'url': node.url, 'target': node.target}

        id = self.pool.get('ir.model.data')._update(cr, 1, \
                "ir.actions.url", self.module_name, res, node.id, mode=self.mode)
        self.idref[node.id] = int(id)
        # ir_set
        if (not node.menu or eval(node.menu)) and id:
            keyword = node.keyword or 'client_action_multi'
            value = 'ir.actions.url,%s' % id
            replace = node.replace or True
            self.pool.get('ir.model.data').ir_set(cr, 1, 'action', \
                    keyword, node.url, ["ir.actions.url"], value, replace=replace, \
                    noupdate=self.isnoupdate(node), isobject=True, xml_id=node.id)
    
    def process_ir_set(self, cr, node):
        if not self.mode == 'init':
            return False
        _, fields = node.items()[0]
        res = {}
        for fieldname, expression in fields.items():
            if is_eval(expression):
                value = eval(expression.expression, self._get_eval_context(cr))
            else:
                value = expression
            res[fieldname] = value
        self.pool.get('ir.model.data').ir_set(cr, 1, res['key'], res['key2'], \
                res['name'], res['models'], res['value'], replace=res.get('replace',True), \
                isobject=res.get('isobject', False), meta=res.get('meta',None))

    def process_report(self, cr, node):
        values = {}
        for dest, f in (('name','string'), ('model','model'), ('report_name','name')):
            values[dest] = getattr(node, f)
            assert values[dest], "Attribute %s of report is empty !" % (f,)
        for field,dest in (('rml','report_rml'),('file','report_rml'),('xml','report_xml'),('xsl','report_xsl'),('attachment','attachment'),('attachment_use','attachment_use')):
            if getattr(node, field):
                values[dest] = getattr(node, field)
        if node.auto:
            values['auto'] = eval(node.auto)
        if node.sxw:
            sxw_file = misc.file_open(node.sxw)
            try:
                sxw_content = sxw_file.read()
                values['report_sxw_content'] = sxw_content
            finally:
                sxw_file.close()
        if node.header:
            values['header'] = eval(node.header)
        values['multi'] = node.multi and eval(node.multi)
        xml_id = node.id
        self.validate_xml_id(cr, xml_id)

        self._set_group_values(cr, node, values)

        id = self.pool.get('ir.model.data')._update(cr, 1, "ir.actions.report.xml", \
                self.module_name, values, xml_id, noupdate=self.isnoupdate(node), mode=self.mode)
        self.idref[xml_id] = int(id)

        if not node.menu or eval(node.menu):
            keyword = node.keyword or 'client_print_multi'
            value = 'ir.actions.report.xml,%s' % id
            replace = node.replace or True
            self.pool.get('ir.model.data').ir_set(cr, 1, 'action', \
                    keyword, values['name'], [values['model']], value, replace=replace, isobject=True, xml_id=xml_id)
            
    def process_none(self):
        """
        Empty node or commented node should not pass silently.
        """
        self._log_assert_failure(logging.WARNING, "You have an empty block in your tests.")

    def process_repeat(self, cr, node, **kwargs):
        repeat, body = node.items()[0]
        subnode = {}
        seqs = {}
        ret_list = []
        if isinstance(body, types.DictionaryType):
            for key, val in body.items():
                if isinstance(key, basestring) \
                        and isinstance(val, basestring) \
                        and '%d' in val:
                    seqs[key] = val
                else:
                    subnode[key] = val
        else:
            subnode = body
        try:
            self._get_eval_context(cr) # intialize context, if needed
            old_context = self._eval_context
            self._eval_context = self._eval_context.copy()
            for r in range(repeat.num):
                for key, val in seqs.items():
                    self._eval_context[key] = val % r
                ret = self._process_node(cr, subnode, multi=True, **kwargs)
                if ret is not None:
                    ret_list.append(ret)
        finally:
            self._eval_context = old_context
        return ret_list

    def _process_repeat_field(self, cr, node, model, field_name):
        repeat, body = node.items()[0]
        subnode = body.copy()
        seqs = {}
        ret_list = []
        if isinstance(body, types.DictionaryType):
            for key, val in body.items():
                if isinstance(key, basestring) \
                        and isinstance(val, basestring) \
                        and '%d' in val:
                    seqs[key] = val
        for r in range(repeat.num):
            for key, val in seqs.items():
                subnode[key] = val % r
                # self.eval_context[key] = subnode[key]
            ret = self._eval_field(cr, model=model, field_name=field_name, expression=[subnode,])
            if ret is not None:
                if ret and isinstance(ret, list) and isinstance(ret[0], tuple):
                    ret_list.extend(ret)
                else:
                    ret_list.append(ret)
        return ret_list

    def parse(self, cr, fname, fp):
        self.filename = fname
        yaml_tag.add_constructors()

        is_preceded_by_comment = False
        for node in yaml.load(fp):
            is_preceded_by_comment = self._log(node, is_preceded_by_comment)
            try:
                self._eval_context = None
                self._process_node(cr, node)
            except YamlImportException, e:
                self.logger.exception(misc.ustr(e))
                if self.extra_args.get('fatal', False):
                    raise
            except Exception, e:
                self.logger.exception(misc.ustr(e))
                raise
            finally:
                self._eval_context = None
    
    def _process_node(self, cr, node, multi=False):
        if is_comment(node):
            self.process_comment(cr, node)
        elif is_assert(node):
            self.process_assert(cr, node)
        elif is_record(node):
            self.process_record(cr, node)
        elif is_python(node):
            self.process_python(cr, node)
        elif is_menuitem(node):
            self.process_menuitem(cr, node)
        elif is_delete(node):
            self.process_delete(cr, node)
        elif is_url(node):
            self.process_url(cr, node)
        elif is_context(node):
            self.process_context(cr, node)
        elif is_ir_set(node):
            self.process_ir_set(cr, node)
        elif is_act_window(node):
            self.process_act_window(cr, node)
        elif is_report(node):
            self.process_report(cr, node)
        elif is_workflow(node):
            if isinstance(node, types.DictionaryType):
                self.process_workflow(cr, node)
            else:
                self.process_workflow(cr, {node: []})
        elif is_function(node):
            if isinstance(node, types.DictionaryType):
                self.process_function(cr, node)
            else:
                self.process_function(cr, {node: []})
        elif is_repeat(node):
            self.process_repeat(cr, node)
        elif node is None:
            self.process_none(cr)
        elif multi and isinstance(node, dict) and len(node) > 1:
            for k, v in node.items():
                self._process_node(cr, {k: v}, multi=True)
        else:
            raise YamlImportException("Can not process YAML block: %s" % node)
    
    def _log(self, node, is_preceded_by_comment):
        if is_comment(node):
            is_preceded_by_comment = True
            self.logger.log(logging.TEST, node.rstrip())
        elif not is_preceded_by_comment:
            if isinstance(node, types.DictionaryType):
                msg = "Creating %s\n with %s"
                args = node.items()[0]
                self.logger.log(logging.TEST, msg, *args)
            else:
                self.logger.log(logging.TEST, node)
        else:
            is_preceded_by_comment = False
        return is_preceded_by_comment

class YamlInterpreter2(DataLoader):
    """Alias for "yaml" extension
    """
    _name = 'yaml'
    _inherit = 'yml'

#eof
