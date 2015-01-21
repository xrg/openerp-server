# -*- coding: utf-8 -*-
##############################################################################
#    
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2012 P. Christeas <xrg@hellug.gr>
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

from tools.safe_eval import ExecContext

class _pool_actor:
    """ Helper that exposes ORM verbs within an ExecContext sandbox

        A requirement is that the ExecContext instance has been launched
        like `ectx = ExecContext(cr=cr, uid=uid, pool=self.pool, context=context, ...)`
    """
    def __init__(self, verb, parent):
        assert isinstance(verb, str), "Invalid verb: %r" % verb
        self._verb = verb
        self._parent = parent

    def __call__(self, model, *args, **kwargs):
        obj = self._parent.pool.get(model)
        if not obj:
            raise KeyError("No such model: %s" % model)
        fn = getattr(obj, self._verb)
        if 'context' not in kwargs:
            kwargs['context'] = self._parent.context
        return fn(self._parent.cr, self._parent.uid, *args, **kwargs)

class _pool_actor_browse:
    """ Actor for the `orm.browse()` verb, that also preserves the cache
    """
    def __init__(self, parent):
        self._parent = parent

    def __call__(self, model, *args, **kwargs):
        obj = self._parent.pool.get(model)
        if not obj:
            raise KeyError("No such model: %s" % model)
        if 'context' not in kwargs:
            kwargs['context'] = self._parent.context
        bro_cache = getattr(self._parent, 'browse_cache', None)
        if bro_cache is not None:
            kwargs['cache'] = bro_cache
        return obj.browse(self._parent.cr, self._parent.uid, *args, **kwargs)

class _actor_ref:
    def __init__(self, parent):
        self._parent = parent

    def __call__(self, xml_ref, model=False):
        module, name = xml_ref.split('.', 1)
        res = self._parent.pool.get('ir.model.data').get_object_reference(self._parent.cr, self._parent.uid, module, name)
        if model and res[0] != model:
            raise ValueError("Model reference for %s.%s is '%s' instead of '%s'" % \
                                (module, name, res[0], model))
        return res[1]

class _pool_actor_orm:
    """ Helper that exposes ORM objects within an ExecContext sandbox

        A requirement is that the ExecContext instance has been launched
        like `ectx = ExecContext(cr=cr, uid=uid, pool=self.pool, context=context, ...)`
    """
    def __init__(self, parent):
        self._parent = parent

    def __call__(self, model):
        return _pool_actor_orm._ORMProxy(self._parent, model)

    class _ORMProxy:
        def __init__(self, parent, model):
            """Dummy object representing an ORM model.
            
                Unlike the 'osv' instances, this is also bound to parent context
                so that (cr, uid, context) are passed to called methods.
                
                It will *only* expose methods without underscore prefix.
            """
            self.__parent = parent
            obj = parent.pool.get(model)
            if not obj:
                raise KeyError("No such model: %s" % model)
            self.__obj = obj

        def __getattr__(self, name):
            if name.startswith('_'):
                raise RuntimeError("Cannot expose \"%s\" to sandbox" % name)
            if name == 'browse':
                return _pool_actor_orm._ORM_browseproxy(self.__obj, self.__parent)
            else:
                return _pool_actor_orm._ORM_fnproxy(self.__obj, self.__parent, name)

    class _ORM_fnproxy:
        def __init__(self, obj, parent, fnname):
            self.__fn = getattr(obj, fnname)
            if not callable(self.__fn):
                raise AttributeError("%s.%s is not a function", obj._name, fnname)
            self.__cr = parent.cr
            self.__uid = parent.uid
            self.__context = parent.context

        def __call__(self, *args, **kwargs):
            if 'context' not in kwargs:
                kwargs['context'] = self.__context
            return self.__fn(self.__cr, self.__uid, *args, **kwargs)

    class _ORM_browseproxy:
        def __init__(self, obj, parent):
            self.__fn = obj.browse
            self.__cr = parent.cr
            self.__uid = parent.uid
            self.__context = parent.context
            self.__browse_cache = getattr(parent, 'browse_cache', None)

        def __call__(self, *args, **kwargs):
            if 'context' not in kwargs:
                kwargs['context'] = self.__context
            if self.__browse_cache is not None:
                kwargs['cache'] = self.__browse_cache
            return self.__fn(self.__cr, self.__uid, *args, **kwargs)

class ExecContext_orm(ExecContext):
    def _prepare_orm(self, context):
        """Call this from your class if you want ORM methods within the context
        """
        # local import, don't influence module's import sequence
        from tools.date_eval import date_eval
        from datetime import timedelta
        from tools.misc import to_date, to_datetime

        context['date_eval'] = date_eval
        context['timedelta'] = timedelta
        context['to_date'] = to_date
        context['to_datetime'] = to_datetime
        context['uid'] = self._kwargs['uid']
        context['context'] = self._kwargs.get('context', {})

        context['orm'] = _pool_actor_orm(self)
        context['browse'] = _pool_actor_browse(self)
        # Hint: all base ORM methods that DON'T operate on [ids]
        for verb in ('search', 'read', 'search_read', 'create', 'write',
                    'unlink',
                    'default_get', 'get_last_modified', 'name_search',
                    'name_get', 'read_group', 'merge_get_values',
                    'merge_records'):
            context[verb] = _pool_actor(verb=verb, parent=self)

        context['ref'] = _actor_ref(self)


import ir
import module
import res
import publisher_warranty

import amount_to_text
import amount_to_text_en


__all__ = ['ir', 'module', 'res', 'amount_to_text', 'ExecContext']

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

