# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2011,2012 P. Christeas <xrg@linux.gr>
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

#.apidoc title: Objects Services (OSV)

import orm
import logging
import netsvc
import pooler
import copy
from psycopg2 import IntegrityError, errorcodes
from tools.func import wraps
from tools.translate import translate
from tools import expr_utils, config
import time

module_list = []
module_class_list = {}
class_pool = {}

class except_osv(Exception):
    def __init__(self, name, value, exc_type='warning'):
        self.name = name
        self.exc_type = exc_type
        self.value = value
        self.args = (exc_type, name)


class object_proxy(netsvc.Service):
    def __init__(self):
        self.logger = logging.getLogger('web-services')
        netsvc.Service.__init__(self, 'object_proxy', audience='')
        self.exportMethod(self.exec_workflow)
        self.exportMethod(self.execute)
        self.exportMethod(self.exec_dict)
        self.exportMethod(self.set_debug)
        self.exportMethod(self.obj_list)
        self.exportMethod(self.method_list)
        self.exportMethod(self.method_explain)
        self.exportMethod(self.list_workflow)

    def check(f):
        @wraps(f)
        def wrapper(self, dbname, *args, **kwargs):
            """ Wraps around OSV functions and normalises a few exceptions
            """

            def tr(src, ttype):
                # We try to do the same as the _(), but without the frame
                # inspection, since we aready are wrapping an osv function
                # trans_obj = self.get('ir.translation') cannot work yet :(
                ctx = {}
                if not kwargs:
                    if args and isinstance(args[-1], dict):
                        ctx = args[-1]
                elif isinstance(kwargs, dict):
                    ctx = kwargs.get('context', {})

                uid = 1
                if args and isinstance(args[0], (long, int)):
                    uid = args[0]

                lang = ctx and ctx.get('lang')
                if not (lang or hasattr(src, '__call__')):
                    return src

                # We open a *new* cursor here, one reason is that failed SQL
                # queries (as in IntegrityError) will invalidate the current one.
                cr = False

                if hasattr(src, '__call__'):
                    # callable. We need to find the right parameters to call
                    # the  orm._sql_message(self, cr, uid, ids, context) function,
                    # or we skip..
                    # our signature is f(osv_pool, dbname [,uid, obj, method, args])
                    try:
                        if args and len(args) > 1:
                            obj = self.get(args[1])
                            if len(args) > 3 and isinstance(args[3], (long, int, list)):
                                ids = args[3]
                            else:
                                ids = []
                        cr = pooler.get_db_only(dbname).cursor()
                        return src(obj, cr, uid, ids, context=(ctx or {}))
                    except Exception:
                        pass
                    finally:
                        if cr: cr.close()

                    return False # so that the original SQL error will
                                 # be returned, it is the best we have.

                try:
                    cr = pooler.get_db_only(dbname).cursor()
                    res = translate(cr, name=False, source_type=ttype,
                                    lang=lang, source=src)
                    if res:
                        return res
                    else:
                        return src
                finally:
                    if cr: cr.close()

            def _(src):
                return tr(src, 'code')

            try:
                if not pooler.get_pool(dbname)._ready:
                    raise except_osv('Database not ready', 'Currently, this database is not fully loaded and can not be used.')
                return f(self, dbname, *args, **kwargs)
            except orm.except_orm, inst:
                if inst.name == 'AccessError':
                    self.logger.debug("AccessError", exc_info=True)
                    self.abortResponse(1, _('Access Error'), 'warning', inst.value, do_traceback=False)
                else:
                    self.abortResponse(1, inst.name, 'warning', inst.value)
            except except_osv, inst:
                self.abortResponse(1, inst.name, inst.exc_type, inst.value)
            except IntegrityError, inst:
                osv_pool = pooler.get_pool(dbname)
                for key in osv_pool._sql_error.keys():
                    if key in inst[0]:
                        self.abortResponse(1, _('Constraint Error'), 'warning',
                                        tr(osv_pool._sql_error[key], 'sql_constraint') or inst[0])
                if inst.pgcode in (errorcodes.NOT_NULL_VIOLATION, errorcodes.FOREIGN_KEY_VIOLATION, errorcodes.RESTRICT_VIOLATION):
                    msg = inst.pgerror + '\n'
                    msg += _('The operation cannot be completed, probably due to the following:\n' \
                          '- deletion: you may be trying to delete a record while other records still reference it\n' \
                          '- creation/update: a mandatory field is not correctly set' )
                    self.logger.debug("IntegrityError", exc_info=False)
                    try:
                        if '"public".' in inst.pgerror:
                            context = inst.pgerror.split('"public".')[1]
                            model_name = table = context.split('"')[1]
                            model = table.replace("_",".")
                            model_obj = osv_pool.get(model)
                            if model_obj:
                                model_name = model_obj._description or model_obj._name
                            msg += _('\n\n[object with reference: %s - %s]') % (model_name, model)
                    except Exception:
                        pass
                    self.abortResponse(1, _('Integrity Error'), 'warning', msg)
                else:
                    self.abortResponse(1, _('Integrity Error'), 'warning', inst[0])
            except netsvc.OpenERPDispatcherException:
                raise
            except Exception:
                self.logger.exception("Uncaught exception")
                raise

        return wrapper

    def _get_cr_auth(self, dbname, kwargs=None):
        """ retrieve the cursor, but also decorate it with caller info from kwargs['auth_proxy']

            The overlying `objects_proxy` of `web_services` will call these methods
            with 'auth_proxy' mirrored in kwargs. If so, we keep a weak reference
            to that in cursor (that's `cr` all over the ORM code).
        """
        cr = pooler.get_db(dbname).cursor()

        if kwargs and 'auth_proxy' in kwargs:
            cr.auth_proxy = kwargs.pop('auth_proxy')

        return cr

    def execute_cr(self, cr, uid, obj, method, *args, **kw):
        object = pooler.get_pool(cr.dbname).get(obj)
        if not object:
            raise except_osv('Object Error', 'Object %s doesn\'t exist' % str(obj))
        try:
            return getattr(object, method)(cr, uid, *args, **kw)
        except expr_utils.DomainError, err:
            if 'context' in kw:
                context = kw['context']
            elif isinstance(args[-1], dict):
                context = args[-1]
            else:
                context = {}
            self.abortResponse(1, err.get_title(cr, uid, context=context),
                    'error', err.get_msg(cr, uid, context))
        except TypeError:
            try:
                import inspect
                fn = getattr(object, method)
                __fn_name = method
                __fn_file = inspect.getfile(fn)
                tb_s = "Function %s from file %s" %( __fn_name, __fn_file)
            except Exception:
                tb_s = "Object: %s Function: %s\n" % (object, getattr(object, method,'<%s ?>' % method))
            self.logger.exception(tb_s)
            raise

    @check
    def execute(self, db, uid, obj, method, *args, **kw):
        cr = self._get_cr_auth(db, kw)
        try:
            if method.startswith('_'):
                raise except_osv('Access Denied', 'Private methods (such as %s) cannot be called remotely.' % (method,))
            res = self.execute_cr(cr, uid, obj, method, *args, **kw)
            if res is None:
                self.logger.warning('Method %s.%s can not return a None value (crash in XML-RPC)', obj, method)
            cr.commit()
        except Exception:
            cr.rollback()
            raise
        finally:
            cr.close()
        return res

    @check
    def exec_dict(self, db, uid, obj, method, args, kwargs=None, **kw):
        """ Execute some ORM call, with support of both positional and keyword arguments
            Note that we strictly expect *2* positional arguments here, one list for
            args and one dict for kwargs
        """
        if not isinstance(args, list):
            raise ValueError("exec_dict() must be called with (args:list, kwargs: dict)")
        if kwargs is None:
            kwargs = {}
        elif not isinstance(kwargs, dict):
            raise ValueError("exec_dict() must be called with (args:list, kwargs: dict)")

        cr = self._get_cr_auth(db, kw)
        try:
            if method.startswith('_'):
                raise except_osv('Access Denied', 'Private methods (such as %s) cannot be called remotely.', method)
            res = self.execute_cr(cr, uid, obj, method, *args, **kwargs)
            if res is None:
                self.logger.warning('Method %s.%s can not return a None value (crash in XML-RPC)', obj, method)
            cr.commit()
        except Exception:
            cr.rollback()
            raise
        finally:
            cr.close()
        return res

    def exec_workflow_cr(self, cr, uid, obj, method, id, context=None):
        wf_service = netsvc.LocalService("workflow")
        return wf_service.trg_validate(uid, obj, id, method, cr, context=context)

    @check
    def exec_workflow(self, db, uid, obj, method, id, context=None, **kw):
        cr = self._get_cr_auth(db, kw)
        try:
            res = self.exec_workflow_cr(cr, uid, obj, method, id, context=context)
            cr.commit()
        except Exception:
            cr.rollback()
            raise
        finally:
            cr.close()
        return res

    def set_debug(self, db, obj, do_debug=True, **kw):
        object = pooler.get_pool(db).get(obj)
        if not object:
            raise except_osv('Object Error', 'Object %s doesn\'t exist' % str(obj))
        try:
            object._debug = do_debug
            return True
        except Exception:
            raise

    def obj_list(self, **kw):
        return []

    def method_list(self, db, obj, **kw):
        """ Lists the available methods for ORM model `obj` through RPC
        """
        if not config.get_misc('debug', 'introspection', False):
            raise except_osv('Access Error', 'Introspection is not enabled')

        obj2 = pooler.get_pool(db).get(obj)
        if not obj2:
            raise except_osv('Object Error', 'Object %s doesn\'t exist' % str(obj))
        try:
            import inspect
            ret = []
            for fnn, x in inspect.getmembers(obj2, inspect.ismethod):
                if fnn.startswith('_'):
                    continue
                ret.append(fnn)
            return ret
        except Exception:
            raise

    def method_explain(self, db, obj, method, **kw):
        """Return introspection information for ORM method `obj`.`method`

            @return a Dict with 'name', 'pretty' as the pythonic definition,
                'doc' with the docstring, 'ctype' with a keyword describing
                ORM conformance of the method
        """
        if not config.get_misc('debug', 'introspection', False):
            raise except_osv('Access Error', 'Introspection is not enabled')

        obj2 = pooler.get_pool(db).get(obj)
        if not object:
            raise except_osv('Object Error', 'Object %s doesn\'t exist' % str(obj))
        try:
            import inspect
            assert isinstance(method, basestring), method
            if method.startswith('_'):
                raise ValueError("Protected methods cannot be inspected")
            ifn = getattr(obj2, method, False)
            if not ifn:
                raise KeyError("No \"%s\" attribute in %s" % (method, obj2._name))
            if not inspect.ismethod(ifn):
                raise ValueError("Attribute \"%s\" is not a function" % method)
            argspec = inspect.getargspec(ifn)
            ctype = 'unknown'
            if argspec.args[0] != 'self':
                ctype = 'non-standard'
            elif argspec.args[:3] == ['self', 'cr', 'uid'] \
                    or argspec.args[:3] == ['self', 'cr', 'user']:

                args3 = (len(argspec.args) > 3  and argspec.args[3]) or False
                if args3 == 'id' and argspec.args[-1] == 'context':
                    ctype = 'record-context'
                elif args3 == 'ids' and argspec.args[-1] == 'context':
                    ctype = 'record-multi-context'
                elif args3 == 'id':
                    ctype = 'record'
                elif args3 == 'ids':
                    ctype = 'record-multi'
                elif argspec.args[-1] == 'context':
                    ctype = 'other-context'

            doc = inspect.getdoc(ifn) or ''
            return dict(name=ifn.__name__, pretty=ifn.__name__ + inspect.formatargspec(*argspec),
                doc=doc.rstrip(), ctype=ctype)
        except Exception:
            raise

    def list_workflow(self, db, obj, **kw):
        """ Lists installed workflows for ORM model `obj` through RPC
        """
        if not config.get_misc('debug', 'introspection', False):
            raise except_osv('Access Error', 'Introspection is not enabled')

        cr = self._get_cr_auth(db, kw)
        try:
            wf_service = netsvc.LocalService("workflow")
            res = wf_service.inspect(cr, [obj,])
            cr.commit()
        except Exception:
            cr.rollback()
            raise
        finally:
            cr.close()
        return res

object_proxy()

class osv_pool(object):
    def __init__(self):
        self._ready = False
        self.obj_pool = {}
        self.module_object_list = {}
        self.created = []
        self._sql_error = {}
        self._store_function = {}
        self._init = True
        self._init_parent = {}
        self.logger = logging.getLogger("pool")
        #: Store some values, temprarily, for the init phase
        self._init_values = {}

    def init_set(self, cr, mode):
        different = mode != self._init
        if different:
            if mode:
                self._init_parent = {}
            if not mode:
                for o in self._init_parent:
                    self.get(o)._parent_store_compute(cr)
            self._init = mode

        self._ready = True
        return different

    def obj_list(self):
        return self.obj_pool.keys()

    def stat_string(self):
        """Return a string describing current state of the pool
        """
        ret = '%s, %d models, %d objects' % \
                (self._ready and 'ready' or 'loading',
                    len(self.module_object_list),
                    len(self.obj_pool))
        return ret

    def __get_abstracts(self, obj_inst):
        """ Return the list of abstract classes obj_inst implements

            Also recurses into all models this one _inherits
        """
        ret = []
        if obj_inst._implements:
            ret += obj_inst._implements
        if obj_inst._inherits:
            for iname in obj_inst._inherits.keys():
                ret += self.__get_abstracts(self.obj_pool[iname])
        return ret

    def add(self, name, obj_inst):
        """Adds a new object instance to the object pool.

            If it already exists, the instance is replaced
        """
        assert isinstance(name, basestring)
        if name in self.obj_pool:
            del self.obj_pool[name]
        self.obj_pool[name] = obj_inst

        if obj_inst._implements or obj_inst._inherits:
            for ibase in self.__get_abstracts(obj_inst):
                if not ibase in self.obj_pool:
                    raise KeyError('Model %s should implement "%s", '
                            'but the latter does not exist in the pool!' % \
                            (name, ibase))

                self.obj_pool[ibase]._append_child(name)

        module = str(obj_inst.__class__)[6:]
        module = module[:len(module)-1]
        module = module.split('.')[0][2:]
        self.module_object_list.setdefault(module, []).append(obj_inst)

    def get(self, name):
        """ Returns None if object does not exist
        """
        if not name:
            return None
        assert isinstance(name, basestring), repr(name)
        obj = self.obj_pool.get(name, None)
        if not obj:
            self.logger.warning("Object %s not found by pooler!", name)
        return obj

    #TODO: pass a list of modules to load
    def instanciate(self, module, cr):
        res = []
        class_list = module_class_list.get(module, [])
        for klass in class_list:
            res.append(klass.createInstance(self, module, cr))
        return res

class osv_base(object):
    def __init__(self, pool, cr):
        pool.add(self._name, self)
        self.pool = pool
        super(osv_base, self).__init__(cr)

    def __new__(cls):
        module = str(cls)[6:]
        module = module[:len(module)-1]
        module = module.split('.')[0][2:]
        if not hasattr(cls, '_module'):
            cls._module = module
        module_class_list.setdefault(cls._module, []).append(cls)
        class_pool[cls._name] = cls
        if module not in module_list:
            module_list.append(cls._module)
        return None

class osv_memory(osv_base, orm.orm_memory):
    #
    # Goal: try to apply inheritancy at the instanciation level and
    #       put objects in the pool var
    #
    def createInstance(cls, pool, module, cr):
        parent_names = getattr(cls, '_inherit', None)
        if parent_names:
            if isinstance(parent_names, basestring):
                name = cls._name or parent_names
                parent_names = [parent_names]
            else:
                name = cls._name
            if not name:
                raise TypeError('_name is mandatory in case of multiple inheritance')

            for parent_name in (isinstance(parent_names, list) and parent_names or [parent_names]):
                assert pool.get(parent_name), "parent class %s does not exist in module %s !" % (parent_name, module)
                parent_obj = pool.get(parent_name)
                parent_class = parent_obj.__class__
                nattr = {}
                for s in ('_columns', '_defaults', '_virtuals', '_vtable'):
                    new = copy.copy(getattr(parent_obj, s))
                    if not getattr(cls, s, False):
                        pass
                    elif s == '_columns':
                        new_cols = getattr(cls, s)
                        for nn, nc in new_cols.items():
                            if isinstance(nc, orm.fields.inherit):
                                if parent_name != name:
                                    new[nn] = copy.copy(new[nn])
                                nc._adapt(new[nn])
                            elif nc is None:
                                new.pop(nn, None)
                            else:
                                new[nn] = nc
                    elif s == '_vtable':
                        if not new:
                            new = getattr(cls, s)
                        elif getattr(cls, s):
                            new.update(getattr(cls,s))
                    elif hasattr(new, 'update'):
                        nc = getattr(cls, s)
                        new.update(nc)
                        for k in nc.keys():
                            if nc[k] is None:
                                new.pop(k, None)
                    else:
                        new.extend(getattr(cls, s))
                    nattr[s] = new
                cls = type(name, (cls, parent_class), nattr)

        obj = object.__new__(cls)
        obj.__init__(pool, cr)
        return obj
    createInstance = classmethod(createInstance)

class osv(osv_base, orm.orm):
    #
    # Goal: try to apply inheritancy at the instanciation level and
    #       put objects in the pool var
    #
    def createInstance(cls, pool, module, cr):
        parent_names = getattr(cls, '_inherit', None)
        if parent_names:
            if isinstance(parent_names, (str, unicode)):
                name = cls._name or parent_names
                parent_names = [parent_names]
            else:
                name = cls._name
            if not name:
                raise TypeError('_name is mandatory in case of multiple inheritance')

            for parent_name in ((type(parent_names)==list) and parent_names or [parent_names]):
                parent_class = pool.get(parent_name).__class__
                assert pool.get(parent_name), "parent class %s does not exist in module %s !" % (parent_name, module)
                nattr = {}
                for s in ('_columns', '_defaults', '_inherits', '_indices',
                        '_constraints', '_sql_constraints', '_virtuals',
                        '_column_stats', '_vtable', '_field_group_acl'):
                    new = copy.copy(getattr(pool.get(parent_name), s))
                    if not getattr(cls, s, False):
                        pass
                    elif s == '_columns':
                        new_cols = getattr(cls, s)
                        for nn, nc in new_cols.items():
                            if isinstance(nc, orm.fields.inherit):
                                if parent_name != name:
                                    new[nn] = copy.copy(new[nn])
                                nc._adapt(new[nn])
                            elif nc is None:
                                new.pop(nn, None)
                            else:
                                new[nn] = nc
                    elif s == '_vtable':
                        if not new:
                            new = getattr(cls, s)
                        elif getattr(cls, s):
                            new.update(getattr(cls, s))
                    elif hasattr(new, 'update'):
                        nc = getattr(cls, s)
                        new.update(nc)
                        for k in nc.keys():
                            if nc[k] is None:
                                new.pop(k, None)
                    else:
                        if s=='_constraints':
                            for c in getattr(cls, s):
                                exist = False
                                for c2 in range(len(new)):
                                     #For _constraints, we should check field and methods as well
                                     if new[c2][2]==c[2] and (new[c2][0] == c[0] \
                                            or getattr(new[c2][0],'__name__', True) == \
                                                getattr(c[0],'__name__', False)):
                                        # If new class defines a constraint with
                                        # same function name, we let it override
                                        # the old one.
                                        new[c2] = c
                                        exist = True
                                        break
                                if not exist:
                                    new.append(c)
                        else:
                            new.extend(getattr(cls, s))
                    nattr[s] = new
                cls = type(name, (cls, parent_class), nattr)
        obj = object.__new__(cls)
        obj.__init__(pool, cr)
        return obj
    createInstance = classmethod(createInstance)


class osv_abstract(osv_base, orm.orm_abstract):
    """ Abstract OSV base

        A model of this class is NOT allowed to have records by itself,
        but rather serve as a basis of abstraction for other models.
        All `_columns` and methods of the abstract model need to be
        defined in the models that `_implements` it.

        Then, some elementary operations on the abstract class can be
        performed, being dispatched to the appropriate implementing classes.
    """

    def createInstance(cls, pool, module, cr):
        if getattr(cls, '_inherit', None):
            raise TypeError("Abstract class %s must not _inherit anything!" % cls._name)
        obj = object.__new__(cls)
        obj.__init__(pool, cr) # TODO: needed?
        return obj

    createInstance = classmethod(createInstance)

def defer(func, delay=0.1, cumulative_on=False):
    """Create a deferred version of an ORM function
    
       The returned function will have the same footprint, like:
           func(cr, uid, ... )
       but, instead of running immediately, it will trigger an Agent
       run in a separate thread+transaction, after some delay.
       
       If cumulative_on is specified, multiple calls to deferred() will
       accumulate into single `func()` calls
    """
    
    def deferred(self, dbname, uid, *args, **kwargs):
        log = logging.getLogger('osv')
        try:
            cr = None
            db, pool = pooler.get_db_and_pool(dbname)
            cr = db.cursor()
            # log.debug("Running %r for %s", func, dbname)
            func(self, cr, uid, *args, **kwargs)
            cr.commit()

        except Exception:
            log.exception("Failed")
            if cr is not None:
                cr.rollback()
        finally:
            if cr is not None:
                cr.close()

    if cumulative_on:
        deferred._cumulative_on = cumulative_on

    def trigger(self, cr, uid, *args, **kwargs):
        netsvc.Agent.setAlarmLater(deferred, time.time() + delay,
                cr, self, cr.dbname, uid, *args, **kwargs)
        return True

    return trigger

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

