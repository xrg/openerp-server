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

from engines import WorkflowEngine
import weakref
import netsvc

#.apidoc title: Workflow-based cache engine

""" Originally written for the Jinja2 templates, this is a minimal caching
    engine based on the workflow ORM hooks.
"""

class RecordValid(object):
    """Mini-object indicating that an ORM record is still valid
    """
    def __init__(self):
        self.iz_valid = True

    def __call__(self):
        return self.iz_valid

    def expire(self):
        self.iz_valid = False

class WkfAntiCacheEngine(WorkflowEngine):
    """ The AntiCache is an engine that expires Cache objects

        Hence the negative name. We manage the 'expiry' of cached objects,
        through a set of weak references.

        Normally, you shouldn't care about this class or maintaining this
        engine. You just call the `get_expiry()` classmethod for the record
        you are interested at::

            obj = self.pool.get('some.model')
            ids = obj.search(cr, uid, [('name', '=', 'foo')])

            result = {'ids': ids, 'expired': WkfAntiCacheEngine.get_expiry(obj, ids) }
            return result
            ...
            # later on in some other function:
            if not result['expired']():
                use(result['ids'])
            else:
                search_again()
    """

    def __init__(self, parent_obj):
        WorkflowEngine.__init__(self, parent_obj)
        self._items = {} # weakrefs, by id
        self._ecounter = 0

    def __del__(self):
        """If this engine is killed, all cached objects must expire
        """
        for it in self._items.values():
            if it():
                it().expire()
        self._items = None # dereference

    def __clear_ids(self, ids):
        for id in ids:
            for it in self._items.get(id, []):
                if it():
                    it().expire()
                    self._ecounter += 1

        if self._ecounter > 20:
            fl = lambda x: x is not None
            for k in self._items.keys():
                self._items[k] = filter(fl, self._items[k])
                if not self._items[k]:
                    del self._items[k]

    def write(self, cr, uid, ids, signals, context):
        self.__clear_ids(ids)

    def delete(self, cr, uid, ids, context):
        self.__clear_ids(ids)

    def inspect(self):
        return "AntiCache"

    @classmethod
    def get_expiry(cls, mobj, ids):
        """Return expiry object for ids of `mobj`

            The expiry object can be tied to a set of ids (not just one).
            You are responsible of releasing the object at your own code,
            when it's no longer needed.
        """
        meng = mobj._workflow.get_instance(WkfAntiCacheEngine)
        if not meng:
            wf_service = netsvc.LocalService('workflow')
            meng = WkfAntiCacheEngine(mobj)
            wf_service.install_workflow(mobj, meng)

        ret = RecordValid()
        for id in ids:
            meng._items.setdefault(id, []).append(weakref.ref(ret))

        return ret
