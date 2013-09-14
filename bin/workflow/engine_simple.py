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

from tools.orm_utils import only_ids
from engines import WorkflowEngine
import weakref
import logging
from workitem import WkfActivity, WkfTransition

class WorkflowSimpleEngine(WorkflowEngine):
    """ Classic wkf engine. Keeps state data in wkf_* tables

        The one used in versions <= 6.0 .
    """
    _logger = logging.getLogger('workflow.simple')

    def __init__(self, parent_obj, wkf_id, wkf_name=''):
        """
            @param wkf_id   record id in the wkf table
        """
        WorkflowEngine.__init__(self, parent_obj)

        self._id = wkf_id
        assert parent_obj
        self._obj = weakref.ref(parent_obj)
        self._activities = {}
        self._subflows = {}
        self._act_starters = []
        self._transitions = []
        self._wkf_name = wkf_name

    def _reload(self, cr):
        """ reload?
        """
        super(WorkflowSimpleEngine, self)._reload(cr)
        for sf in self._subflows.values():
            sf._reload(cr)

        self._act_starters = []
        self._activities = {}
        # Activities:
        cr.execute('SELECT * FROM wkf_activity WHERE wkf_id=%s', (self._id,), debug=self._debug)
        for ar in cr.dictfetchall():
            ar.pop('create_uid', None)
            ar.pop('create_date', None)
            ar.pop('write_uid', None)
            ar.pop('write_date', None)
            newa = WkfActivity(self, ar)
            self._activities[newa._id] = newa
            if newa._flow_start:
                self._act_starters.append(newa._id)

        # Transitions:
        self._transitions = []
        cr.execute('SELECT * FROM wkf_transition WHERE act_from = ANY(%s)',
            (self._activities.keys(),), debug=self._debug)
        trids = []
        for tr in cr.dictfetchall():
            tr.pop('create_uid', None)
            tr.pop('create_date', None)
            tr.pop('write_uid', None)
            tr.pop('write_date', None)
            newtr = WkfTransition(self, tr)
            self._transitions.append(newtr)
            trids.append(tr['id'])

        # Just check:
        cr.execute('SELECT id FROM wkf_transition WHERE act_to = ANY(%s) AND act_from != ALL(%s)',
            (self._activities.keys(), self._activities.keys()), debug=self._debug)
        res = cr.fetchall()
        if res:
            log = logging.getLogger('workflow.activity')
            log.error('Data error: transitions %s connect activities accross different workflows. Will be ignored!',
                ','.join([str(r[0]) for r in res]))

        # end of reload()

    def create(self, cr, uid, ids, context):
        ret = []
        for id in only_ids(ids):
            cr.execute('INSERT INTO wkf_instance (res_type, res_id, uid, wkf_id) ' \
                    'VALUES (%s,%s,%s,%s) RETURNING id',
                    (self._obj()._name, id, uid, self._id,), debug=self._debug)
            id_new = cr.fetchone()[0]
            stack = []
            for act_id in self._act_starters:
                self._activities[act_id].create(cr, uid, id_new, res_id=id,
                        stack=stack, context=context)
            self.__update(cr, uid, 'id', '= %s', (id_new,), context=context) # results?
            ret.append(id_new)
        return ret

    def write(self, cr, uid, ids, signals, context):
        """
            @param instance_id If given, points to a specific instance, rather than
                those of current model+ids
        """
        inst_qry = "IN (SELECT id FROM wkf_instance " \
                "WHERE res_id=ANY(%s) AND wkf_id=%s AND state='active')"

        # TODO browse_cache?
        return self.__update(cr, uid, 'all_active', inst_qry, (only_ids(ids), self._id), context)

    def __update(self, cr, uid, inst_name='', inst_qry='', inst_args=(), signal=None, \
                force_running=False, context=None, results=None):
        """ Second part of write(), common with validate()

            @param inst_qry An SQL expression matching wkf_instance.id
            @param inst_args Arguments to above query
            @param inst_name a **unique** name to distinguish `inst_qry`
            @param results if given, must be list where results will be appended
        """
        # TODO inst_name

        if self._debug:
            self._logger.debug('Update of %s %s w. %r signal=%r', self._obj()._name, inst_name, inst_args, signal)

        cr.execute("SELECT wi.*, ii.res_id FROM wkf_workitem AS wi, wkf_instance AS ii "
                "WHERE wi.inst_id = ii.id AND wi.inst_id " + inst_qry,
                    inst_args, debug=self._debug)

        if self._debug:
            self._logger.debug("workitems found: %d", cr.rowcount)

        # instead of reusing inst_qry, we strictly match the ids of the first
        # query, because the other columns (like 'active') may change.
        wi_ids = []
        for witem in cr.dictfetchall():
            stack = []
            wi_ids.append(witem['inst_id'])
            if self._debug:
                self._logger.debug("Process activity %s: #%d %s", witem['act_id'], witem['id'], witem['inst_id'])
            pres = self._activities[witem['act_id']].process(cr, uid, \
                    witem, signal=signal, force_running=force_running, \
                    stack=stack, context=context)
            if pres and results is not None:
                results.append(pres)

        cr.execute("SELECT 1 WHERE ROW('complete', true) = ALL( " \
                "SELECT state, flow_stop " \
                "  FROM wkf_workitem w "\
                "      LEFT JOIN wkf_activity a ON (a.id=w.act_id) " \
                " WHERE w.inst_id = ANY(%s) );", (wi_ids,), debug=self._debug)

        ok = cr.fetchone()
        if self._debug:
            self._logger.debug("Result: %r", ok)
        if not (ok and ok[0]):
            return False

        if ok:
            cr.execute("SELECT DISTINCT a.name FROM wkf_activity a " \
                    "LEFT JOIN wkf_workitem w ON (a.id=w.act_id) " \
                    "WHERE w.inst_id = ANY(%s)", (wi_ids,), debug=self._debug)
            act_names = cr.fetchall()
            cr.execute("UPDATE wkf_instance SET state='complete' WHERE id = ANY(%s);",
                    (wi_ids,), debug=self._debug)
            cr.execute("UPDATE wkf_workitem SET state='complete' WHERE subflow_id = ANY(%s) ",
                    (wi_ids,), debug=self._debug)

            cr.execute("SELECT i.id, w.osv, i.res_id " \
                    "FROM wkf_instance i LEFT JOIN wkf w on (i.wkf_id=w.id) " \
                    "WHERE i.id IN (SELECT inst_id FROM wkf_workitem " \
                    "               WHERE subflow_id = ANY(%s))", (wi_ids,),
                    debug=self._debug)
            pool = self._obj().pool
            for b_iid, b_model, b_res_id in cr.fetchall():
                obj = pool.get(b_model)
                for act_name in act_names:
                    obj._workflow.validate_byid(cr, uid, b_res_id, b_iid, \
                            signal='subflow.'+act_name[0], context=context)

        return ok

    def validate(self, cr, uid, id, signal, context):
        """ ?
        """

        results = []
        inst_qry = "IN (SELECT id FROM wkf_instance " \
                "WHERE res_id=%s AND wkf_id=%s AND state='active')"
        ok = self.__update(cr, uid, 'all_active2', inst_qry, (id, self._id), signal=signal, \
                force_running=False,  context=context, results=results)

        # TODO do we care about 'ok' (meaning wkf is complete) ?
        if results:
            return results[-1]
        else:
            return False

    def validate_byid(self, cr, uid, id, inst_id, signal=None, context=None, force_running=False):
        results = []

        ok = self.__update(cr, uid, 'id', '= %s', (inst_id,),signal=signal, context=context,
                force_running=force_running, results=results)

        if results:
            return results[-1]
        else:
            return False

    def delete(self, cr, uid, ids, context):
        cr.execute('DELETE FROM wkf_instance WHERE wkf_id = %s AND res_id =ANY(%s)',
                (self._id, ids), debug=self._debug)

    def redirect(self, cr, uid, old_id, new_id, context):

        #CHECKME: shouldn't we get only active instances?
        cr.execute("""UPDATE wkf_workitem SET subflow_id = wfi_new.id
                      FROM wkf_instance AS wfi_old, wkf_instance AS wfi_new
                     WHERE subflow_id = wfi_old.id
                        AND wfi_old.wkf_id = %s AND wfi_old.res_id = %s
                        AND wfi_new.wkf_id = %s AND wfi_new.res_id = %s
                        AND wfi_new.state = 'active');
                    """,
                    (self._id, old_id, self._id, new_id),
                    debug=self._debug)

    @classmethod
    def reload_models(cls, service, pool, cr, models):

        wkfs = dict.fromkeys(models) # all to None, because [] is mutable
        wkf_subflows = {}
        cr.execute('SELECT osv, id, name, on_create FROM wkf WHERE osv=ANY(%s)', (models,))
        for r_osv, r_id, r_name, r_onc in cr.fetchall():
            obj = pool.get(r_osv)
            if not obj:
                cls._logger.warning("Object '%s' referenced in workflow #%d, but doesn't exist in pooler!",
                        r_osv, r_id)
                continue
            if True:
                neng = WorkflowSimpleEngine(obj, r_id, r_name)
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
                assert model not in wkf_subflows, "Subflow but not simple workflow for %s!" % model
                continue
            if len(engs) == 1:
                service.install_workflow(obj, engs[0])
            else:
                service.install_workflow(obj, engs, default=True)

            if model in wkf_subflows:
                # This line will bork if we have a subflow on a model
                # without a simple workflow
                engs[0]._subflows = wkf_subflows.pop(model)
            for eng in engs:
                eng._reload(cr)

        return

    def inspect(self):
        ret = "Simple#%d %s(%d acts" % (self._id, self._wkf_name, len(self._activities))
        if self._subflows:
            ret += ", %d subflows" % len(self._subflows)
        ret += ')'
        return ret

WorkflowSimpleEngine.set_loadable()
#eof
