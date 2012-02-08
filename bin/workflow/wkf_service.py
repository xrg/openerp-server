# -*- coding: utf-8 -*-
##############################################################################
#    
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2009 Albert Cervera i Areny <albert@nan-tic.com>
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

import netsvc
import pooler
import warnings
from engines import WorkflowEngine, WorkflowCompositeEngine
from engine_simple import WorkflowSimpleEngine
import logging

class workflow_service(netsvc.Service):
    """ ORM workflows service and old-style API
    
    Sometimes you might want to fire a signal or re-evaluate the current state
    of a workflow using the service's API. You can access the workflow services
    using::

        import netsvc
        wf_service = netsvc.LocalService("workflow")

    Then you can use the following methods.
    """
    def __init__(self, name='workflow'):
        netsvc.Service.__init__(self, name)
        self.exportMethod(self.trg_write)
        self.exportMethod(self.trg_delete)
        self.exportMethod(self.trg_create)
        self.exportMethod(self.trg_validate)
        self.exportMethod(self.trg_redirect)
        self.exportMethod(self.trg_trigger)
        self.exportMethod(self.reload_models)
        self.exportMethod(self.freeze)
        self.exportMethod(self.thaw)
        self.exportMethod(self.init_dummy)
        self.exportMethod(self.thaw_dummy)
        self._logger = logging.getLogger('workflow.service')
        self._freezer = {}
        
    def _instance(self, cr, model):
        """Get the engine instance of that model, into the new API
        """
        
        obj = pooler.get_pool(cr.dbname).get(model)
        if not obj:
            raise KeyError("Model %s not in pool of database \"%s\"" % (model, cr.dbname))
        if not obj._workflow:
            if cr.dbname in self._freezer:
                # temporarily use a dummy engine, needed so that we don't have
                # to thaw the whole freezer too early
                self._logger.debug("Using a temporary wkf engine for %s", model)
                obj._workflow = WorkflowEngine(obj)
                return obj._workflow
            else:
                raise RuntimeError("orm %s doesn't have an initialized workflow" % obj._name)
        return obj._workflow

    def reload_models(self, cr, models):
        """Reloads workflow for the specified models
            
            It will replace existing workflows for them.

            @param models list of orm model names
            
            If the service is `frozen`, reloading for the models will be deferred.
        """
        if cr.dbname in self._freezer:
            self._freezer[cr.dbname].extend(models)
            return
    
        pool = pooler.get_pool(cr.dbname)
        wkfs = dict.fromkeys(models) # all to None, because [] is mutable
        wkf_subflows = dict()
        self._logger.debug("Reloading %d models: %s ...", len(wkfs.keys()), ','.join(wkfs.keys()[:20]))
        
        cr.execute('SELECT osv, id, on_create FROM wkf WHERE osv=ANY(%s)', (models,))
        for r_osv, r_id, r_onc in cr.fetchall():
            obj = pool.get(r_osv)
            if not obj:
                self._logger.warning("Object '%s' referenced in workflow #%d, but doesn't exist in pooler!",
                        r_osv, r_id)
                continue
            if True:
                neng = WorkflowSimpleEngine(obj, r_id)
            if r_onc:
                if wkfs[r_osv] is None:
                    wkfs[r_osv] = []
                wkfs[r_osv].append(neng)
            else:
                wkf_subflows.setdefault(r_osv, {})[r_id] = neng
        
        for model, engs in wkfs.items():
            obj = pool.get(model)
            if not obj:
                continue
            if not engs:
                obj._workflow = WorkflowEngine(obj)
            elif len(engs) > 1:
                obj._workflow = WorkflowCompositeEngine(obj, engs)
            elif len(engs) > 0:
                obj._workflow = engs[0]
            else:
                self._logger.warning("engs: %r", engs)
                raise RuntimeError("unreachable code")

            if model in wkf_subflows:
                obj._workflow._subflows = wkf_subflows.pop(model)
            obj._workflow._reload(cr)

        self._logger.debug("Workflows reloaded")

    def freeze(self, cr):
        self._logger.debug("Workflow service freeze for updates of %s", cr.dbname)
        if cr.dbname not in self._freezer:
            self._freezer[cr.dbname] = []
    
    def thaw(self, cr):
        if cr.dbname in self._freezer:
            self._logger.debug("Workflow service thawing of %s", cr.dbname)
            models = self._freezer.pop(cr.dbname)
            if models:
                self.reload_models(cr, models)

    def init_dummy(self, cr, obj):
        """ Return the default workflow engine for an ORM model
        """
        return WorkflowEngine(obj)

    def thaw_dummy(self, cr):
        """ If dbname is frozen, init all models to dummy wkf engine
        """
        if cr.dbname not in self._freezer:
            return
        pool = pooler.get_pool(cr.dbname)
        for model in self._freezer[cr.dbname]:
            obj = pool.get(model)
            if not obj._workflow:
                obj._workflow = WorkflowEngine(obj)
        
        return

    def trg_write(self, uid, res_type, res_id, cr, context=None):
        """
        Reevaluates the specified workflow instance. Thus if any condition for
        a transition have been changed in the backend, then running ``trg_write``
        will move the workflow over that transition.

        :param res_type: the model name
        :param res_id: the model instance id the workflow belongs to
        :param cr: a database cursor
        """
        self._instance(cr, res_type).write(cr, uid, [res_id,], context)

    def trg_trigger(self, uid, res_type, res_id, cr, context=None):
        """
        Activate a trigger.

        If a workflow instance is waiting for a trigger from another model, then this
        trigger can be activated if its conditions are met.

        :param res_type: the model name
        :param res_id: the model instance id the workflow belongs to
        :param cr: a database cursor
        """
        cr.execute('SELECT id, res_type, res_id FROM wkf_instance '
                'WHERE id in (SELECT instance_id  FROM wkf_triggers AS wts '
                        'WHERE res_id = %s AND model=%s);', 
                        (res_id, res_type) )
        pool = pooler.get_pool(cr.dbname)
        for inst_id, res_model, res_id in cr.fetchall():
            pool.get(res_model)._workflow.validate_byid(cr, uid, res_id, inst_id, context=context)

    def trg_delete(self, uid, res_type, res_id, cr, context=None):
        """
        Delete a workflow instance

        :param res_type: the model name
        :param res_id: the model instance id the workflow belongs to
        :param cr: a database cursor
        """
        self._instance(cr, res_type).delete(cr, uid, [res_id,], context)

    def trg_create(self, uid, res_type, res_id, cr, context=None):
        """
        Create a new workflow instance

        :param res_type: the model name
        :param res_id: the model instance id to own the created worfklow instance
        :param cr: a database cursor
        """
        self._instance(cr, res_type).create(cr, uid, [res_id,], context)

    def trg_validate(self, uid, res_type, res_id, signal, cr, context=None):
        """
        Fire a signal on a given workflow instance

        :param res_type: the model name
        :param res_id: the model instance id the workflow belongs to
        :param signal: the signal name to be fired
        :param cr: a database cursor
        """
        return self._instance(cr, res_type).validate(cr, uid, res_id, signal, context)

    def trg_redirect(self, uid, res_type, res_id, new_rid, cr, context=None):
        """
        Re-bind a workflow instance to another instance of the same model.

        Make all workitems which are waiting for a (subflow) workflow instance
        for the old resource point to the (first active) workflow instance for
        the new resource.

        :param res_type: the model name
        :param res_id: the model instance id the workflow belongs to
        :param new_rid: the model instance id to own the worfklow instance
        :param cr: a database cursor
        """
        # get ids of wkf instances for the old resource (res_id)
        
        self._instance(cr, res_type).redirect(cr, uid, res_id, new_rid, context)

workflow_service()

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

