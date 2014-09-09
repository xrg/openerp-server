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

class _pool_actor(object):
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

class _pool_actor_browse(object):
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

class ExecContext_orm(ExecContext):
    def _prepare_orm(self, context):
        """Call this from your class if you want ORM methods within the context
        """
        context['uid'] = self._kwargs['uid']
        context['context'] = self._kwargs.get('context', {})

        context['browse'] = _pool_actor_browse(self)
        # Hint: all base ORM methods that DON'T operate on [ids]
        for verb in ('search', 'read', 'search_read', 'create', 'write',
                    'default_get', 'get_last_modified', 'name_search',
                    'name_get', 'read_group', 'merge_get_values',
                    'merge_records'):
            context[verb] = _pool_actor(verb=verb, parent=self)

import ir
import module
import res
import publisher_warranty

import amount_to_text
import amount_to_text_en

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

