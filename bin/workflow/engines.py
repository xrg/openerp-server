# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2012 P. Christeas <xrg@hellug.gr>
#
#    code taken from:
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2009 Albert Cervera i Areny <albert@nan-tic.com>
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

#.apidoc title: Workflow Engine interface

from tools import config

_loadables = [] # set of subclasses that need to be called on reload_models()

class WorkflowEngine(object):
    """A workflow definition, for a particular ORM object

        An instance of this class corresponds to (one) record of the `wkf` table

        This class is the 'dummy' engine, which defaults to a NoOp for any of
        the API calls.
    """

    def __init__(self, parent_obj):
        """
            @param parent_obj The ORM object holding this engine. May keep it in weakref
        """

        self._debug = config.get_misc('debug', 'workflows', False)

    def _reload(self, cr):
        """Called when the ir model has any changes

            Will NOT work if the workflow has changed base type
        """
        pass

    def create(self, cr, uid, ids, context):
        pass

    def pre_write(self, cr, uid, ids, vals, signals, context):
        """Populate `signals` with triggers for write()

            In orm.write(), the WorkflowEngine.write() will be called
            last, after the values are updated in the database. That
            means that the engine would have no means of knowing the
            `old` values of the records. We couldn't implement a rule
            like "when state goes from draft to open do..." .
            So, this function is called early. It knows the `ids` and
            `vals`, so can figure out which fields do any transition.

            @param ids A list of record IDs
            @param vals A dict of name:value data to write to all ids.
            @param signals an empty dict that will be populated with
                    'signal': [sub-ids,] entries for any records that
                    need to trigger a subsequent wkf transition.
        """
        pass

    def write(self, cr, uid, ids, signals, context):
        """
            @param signals coming from pre_write(), a dictionary of
                    'signal': [ids,] triggers to execute.
        """
        pass

    def validate(self, cr, uid, id, signal, context):
        pass

    def validate_byid(self, cr, uid, id, inst_id, signal=None, context=None, force_running=False):
        """ Validate a specific wkf_instance (inst_id)
        """
        pass

    def trigger(self, cr, uid, ids, context):
        raise NotImplementedError
        pass

    def delete(self, cr, uid, ids, context):
        pass

    def redirect(self, cr, uid, old_id, new_id, context):
        pass


    def get_instance(self, klass):
        """Get `self` or any contained engine, of `klass` type

            Useful when checking if an ORM model already contains some
            workflow definition. First use this function to obtain the
            object instance, then use class-specific functions to check
            or modify the workflow.
        """
        if isinstance(self, klass):
            return self
        else:
            return None

    @classmethod
    def reload_models(cls, service, pool, cr, models):
        """ Reload this engine's workflows for models

            @param pool the ORM pooler to get model objects from
            @param service the calling workflow_service instance
            @param models list of orm model names (strings)

            This method shall NOT touch the orm._workflow member, but instead
            use get_instance() and install_workflow() methods in order to
            test or add workflow instances.
        """
        raise NotImplementedError

    @classmethod
    def set_loadable(cls):
        """Registers this class as a loadable workflow engine
        """
        global _loadables
        _loadables.append(cls)

    @staticmethod
    def get_loadables():
        global _loadables
        return _loadables

    def inspect(self):
        """Human-readable description of this engine instance

            Subclasses should override this to provide meaningful descriptions
        """
        return "<%s>" % self.__class__.__name__

class WorkflowCompositeEngine(WorkflowEngine):
    """ Binds >1 Engines to one ORM object

        Should rarely be used. Useful to combine engines.
    """
    def __init__(self, parent_obj, engs):
        WorkflowEngine.__init__(self, parent_obj)
        for e in engs:
            assert isinstance(e, WorkflowEngine), "Not an engine: %s" % type(e)
        self._engines = engs

    def _reload(self, cr):
        """Called when the ir model has any changes

            Will NOT work if the workflow has changed base type
        """
        super(WorkflowCompositeEngine, self)._reload(cr)
        # TODO check base type

        for e in self._engines:
            e._reload(cr)

    def create(self, cr, uid, ids, context):
        for e in self._engines:
            e.create(cr, uid, ids, context)

    def pre_write(self, cr, uid, ids, vals, signals, context):
        for e in self._engines:
            e.pre_write(cr, uid, ids, vals, signals, context)

    def write(self, cr, uid, ids, signals, context):
        for e in self._engines:
            e.write(cr, uid, ids, signals, context)

    def validate(self, cr, uid, id, signal, context):
        first_result = False
        for e in self._engines:
            res = e.validate(cr, uid, id, signal, context)
            first_result = first_result or res
        return first_result

    def validate_byid(self, cr, uid, id, inst_id, signal=None, context=None, force_running=False):
        """ Validate a specific wkf_instance (inst_id)
        """
        first_result = False
        for e in self._engines:
            res = e.validate_byid(cr, uid, id, inst_id, signal=signal, context=context, force_running=force_running)
            first_result = first_result or res
        return first_result

    #def trigger(self, cr, uid, ids, context):
    #    for e in self._engs:
    #        e._trigger(cr, uid, ids, context)


    def delete(self, cr, uid, ids, context):
        for e in self._engines:
            e.delete(cr, uid, ids, context)


    def redirect(self, cr, uid, old_id, new_id, context):
        for e in self._engines:
            e.redirect(cr, uid, old_id, new_id, context)

    def get_instance(self, klass):
        """Recursively check for klass instance in contained engines
        """
        ret = super(WorkflowCompositeEngine, self).get_instance(klass)
        if ret is not None:
            return ret
        for e in self._engines:
            ret = e.get_instance(klass)
            if ret is not None:
                return ret
        return None

    def inspect(self):
        return '[ ' + ','.join([e.inspect() for e in self._engines]) + ' ]'

# eof
