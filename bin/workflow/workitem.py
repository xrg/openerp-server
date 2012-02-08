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

#.apidoc title: Workflow Activity class

#
# TODO:
# cr.execute('delete from wkf_triggers where model=%s and res_id=%s', (res_type,res_id))
#

from tools.safe_eval import safe_eval
import weakref
import logging

log = logging.getLogger('workflow.activity')

class WkfActivity(object):
    """Cached wkf_activity record and pythonic worker
    """
    def __init__(self, parent, war_res):
        wkf_id = war_res.pop('wkf_id')
        assert wkf_id == parent._id, "%r != %r" % (wkf_id, parent._id)
        for k, v in war_res.items():
            setattr(self, '_'+k, v)
        self._parent = weakref.ref(parent)
    
    def create(self, cr, uid, inst_id, res_id, stack, context=None):
        """
            @param parent the WorkflowSimpleEngine instance
            @param inst_id the wkf_instance id
            @param stack a list
        """
    
        cr.execute("INSERT INTO wkf_workitem (act_id, inst_id, state) " \
                "VALUES (%s,%s,'active') RETURNING *", (self._id, inst_id), debug=self._parent()._debug)
        res = cr.dictfetchone()
        res['res_id'] = res_id
        # wkf_logs.log(cr,ident,act['id'],'active')
        self.process(cr, uid, res, stack=stack, context=context)

    def process(self, cr, uid, workitem, signal=None, force_running=False, stack=None, context=None):
        if stack is None:
            raise RuntimeError('No stack!')
        result = True

        triggers = False
        if workitem['state']=='active':
            triggers = True
            result = self._execute(cr, uid, workitem, stack, context)
            if not result:
                return False

        if workitem['state']=='running':
            pass

        if workitem['state']=='complete' or force_running:
            ok = self._split_test(cr, uid, workitem, signal=signal, stack=stack, context=context)
            triggers = triggers and not ok

        if triggers:
            alltrans = [t for t in self._parent()._transitions if t._act_from == workitem['act_id']]
            for trans in alltrans:
                if trans._trigger_model:
                    env = Env(cr, uid, self._parent()._obj(), workitem['res_id'] , context)
                    ids = safe_eval(trans._trigger_expr_id, env, nocopy=True, mode='eval')
                    for res_id in ids:
                        cr.execute('INSERT INTO wkf_triggers ' \
                                '(model, res_id, instance_id, workitem_id) '
                                'values (%s,%s,%s,%s)', 
                            (trans._trigger_model, res_id, workitem['inst_id'], workitem['id']),
                            debug=self._parent()._debug)

        return result

    def __state_set(self, cr, uid, workitem, state, context=None):
        cr.execute('UPDATE wkf_workitem SET state=%s where id=%s', (state,workitem['id']))
        workitem['state'] = state
        # wkf_logs.log(cr,ident,activity['id'],state)

    def _execute(self, cr, uid, workitem, stack, context):
        result = True
        #
        # send a signal to parent workflow (signal: subflow.signal_name)
        #
        signal_todo = []
        if ( workitem['state'] == 'active') and self._signal_send:
            cr.execute("SELECT i.id, w.osv, i.res_id FROM wkf_instance AS i "
                            "LEFT JOIN wkf w on (i.wkf_id=w.id) " \
                            "WHERE i.id IN " \
                            "    (SELECT inst_id FROM wkf_workitem WHERE subflow_id=%s)",
                        (workitem['inst_id'],), debug=self._parent()._debug)
            for inst_id, res_model, res_id in cr.fetchall():
                signal_todo.append((inst_id, res_model, res_id, self._signal_send))

        if self._kind=='dummy':
            if workitem['state'] == 'active':
                self.__state_set(cr, uid, workitem, 'complete', context=context)
                if self._action_id:
                    res2 = self._execute_action(cr, uid, workitem, context)
                    if res2:
                        stack.append(res2)
                        result=res2
        elif self._kind == 'function':
            if workitem['state'] == 'active':
                self.__state_set(cr, uid, workitem, 'running', context)
                self._expr_execute(cr, uid, workitem, mode='exec', context=context)
                if self._action_id:
                    res2 = self._execute_action(cr, uid, workitem, context)
                    # A client action has been returned
                    if res2:
                        stack.append(res2)
                        result=res2
                self.__state_set(cr, uid, workitem, 'complete', context)
        elif self._kind == 'stopall':
            if workitem['state']=='active':
                self.__state_set(cr, uid, workitem, 'running', context)
                cr.execute('DELETE FROM wkf_workitem WHERE inst_id=%s AND id<>%s', 
                        (workitem['inst_id'], workitem['id']), debug=self._parent()._debug)
                if self._action:
                    self._expr_execute(cr, uid, workitem, mode='exec', context=context)
                self.__state_set(cr, uid, workitem, 'complete', context)
        elif self._kind == 'subflow':
            if workitem['state']=='active':
                self.__state_set(cr, uid, workitem, 'running', context)
                if self._action:
                    id_new = self._expr_execute(cr, uid, workitem, mode='eval', context=context)
                    if not id_new:
                        cr.execute('DELETE FROM wkf_workitem WHERE id=%s', (workitem['id'],))
                        return False
                    assert isinstance(id_new, (int, long)), 'Wrong return value: %s %r '% (type(id_new), id_new)
                    cr.execute('SELECT id FROM wkf_instance WHERE res_id=%s AND wkf_id=%s', 
                            (id_new, self._subflow_id), debug=self._parent()._debug)
                    id_new = cr.fetchone()[0]
                    assert id_new, "New record has no workflow instance!"
                else:
                    # obtain the topmost _workflow of the ORM object
                    obj_wkf = self._parent()._obj()._workflow
                    # Note: in this case, the subflow MUST be on the same orm model
                    id_new = obj_wkf._subflows[self._subflow_id].\
                            create(cr, uid, [workitem['res_id'],], context)[0]
                
                cr.execute('UPDATE wkf_workitem SET subflow_id=%s where id=%s', 
                        (id_new, workitem['id']), debug=self._parent()._debug)
                workitem['subflow_id'] = id_new
            if workitem['state']=='running':
                cr.execute("SELECT state FROM wkf_instance WHERE id=%s", 
                        (workitem['subflow_id'],), debug=self._parent()._debug)
                state= cr.fetchone()[0]
                if state=='complete':
                    self.__state_set(cr, uid, workitem, 'complete', context)

        if signal_todo:
            pool = self._parent()._obj().pool
            for inst_id, res_model, res_id, signal in signal_todo:
                pool.get(res_model)._workflow.\
                        validate_byid(cr, uid, res_id, inst_id, \
                                signal=signal, force_running=True, context=context)

        return result

    def _split_test(self, cr, uid, workitem, signal=None, stack=None, context=None):
        """ Attempt to spawn one or more transitions from current state
        """
        if stack is None:
            raise RuntimeError('No stack!')
        
        test = False
        parent = self._parent()
        transitions = []
        alltrans = [t for t in parent._transitions if t._act_from == workitem['act_id']]
        
        if self._split_mode == 'XOR' or self._split_mode == 'OR':
            for t in alltrans:
                if self._transition_check(cr, uid, workitem, t, signal, context):
                    test = True
                    transitions.append(t)
                    if self._split_mode == 'XOR':
                        break
        else:
            test = True
            for t in alltrans:
                if not self._transition_check(cr, uid, workitem, t, signal, context):
                    test = False
                    break
                cr.execute('SELECT trans_id FROM wkf_witm_trans ' \
                            'WHERE trans_id=%s and inst_id=%s LIMIT 1', 
                            (t._id, workitem['inst_id']), debug=parent._debug)
                if not cr.fetchone():
                    transitions.append(t)
        if test and len(transitions):
            cr.executemany('INSERT INTO wkf_witm_trans (trans_id, inst_id) ' \
                    'VALUES (%s,%s)', [(t._id, workitem['inst_id']) for t in transitions])
                    # FIXME
            cr.execute('DELETE FROM wkf_workitem where id=%s', (workitem['id'],), debug=parent._debug)
            for t in transitions:
                # Retrieve the target activity. It will be the same class as `self`
                # but different instance
                # A KeyError indicates that either _activities or _transitions were
                # not loaded correctly into cache
                act = parent._activities[t._act_to]
                act._join_test(cr, uid, workitem['inst_id'], workitem['res_id'], t, stack, context)
                
            return True
        return False

    def _join_test(self, cr, uid, inst_id, res_id, trans, stack, context):
        """ Attempt to join one or more conditions into a new state
            
            @param wkf_instance id
            @param res_id the id of the ORM record
            @param trans a WkfTransition
        """
        if self._join_mode == 'XOR':
            self.create(cr, uid, inst_id, res_id, stack, context)
            cr.execute('DELETE FROM wkf_witm_trans WHERE inst_id=%s AND trans_id=%s', 
                    (inst_id, trans._id))
            return True
        else:
            trans_ids = [ t._id for t in self._parent()._transitions if t._act_to == self._id]
            # In one query, see if there is any transition targeted to self, but not ready
            # at that wkf_instance through a witm entry
            #
            #    Note: we /could/ have embedded the "t._act_to == self._id" lookup
            #          in the SQL below, but that would trigger the wild case that 
            #          a transition not in parent._transitions would block the flow.
            
            cr.execute('SELECT EXISTS (SELECT id, trans_id FROM wkf_transition AS trans ' \
                            'LEFT JOIN wkf_witm_trans ON (trans.id = trans_id AND inst_id = %s) ' \
                            'WHERE trans.id = ANY(%s) AND trans_id IS NULL);',
                            (inst_id, trans_ids), debug=self._parent()._debug)
            if cr.fetchone()[0]:
                return False
            
            cr.execute('DELETE FROM wkf_witm_trans WHERE trans_id = ALL(%s) AND inst_id = %s',
                        (trans_ids, inst_id), debug=self._parent()._debug)
            self.create(cr, uid, inst_id, res_id, stack=stack, context=context)
            return True

    def _execute_action(self, cr, uid, workitem, context):
        """ Execute a server action, as in self.action_id
        """
        obj = self._parent()._obj().pool.get('ir.actions.server')
        ctx = context.copy()
        ctx.update( {'active_id': workitem['res_id'], 'active_ids':[workitem['res_id']]} )
        result = obj.run(cr, uid, [self._action_id], ctx)
        return result

    def _expr_execute(self, cr, uid, workitem, mode, context=None):
        """ Execute the expression (code), as in self.action
        """
        ret=False
        assert self._action, 'You used a NULL action in a workflow, use dummy node instead.'
        env = Env(cr, uid, self._parent()._obj(), workitem['res_id'] , context)
        return safe_eval(self._action, env, nocopy=True, mode=mode)

    def _transition_check(self, cr, uid, workitem, transition, signal, context):
        if self._parent()._debug:
            log.debug("transition check for witem: %d, transition: %d, signal=%s",
                    workitem['id'], transition._id, signal)
        if transition._signal and signal != transition._signal:
            return False

        if transition._group_id and uid != 1:
            if not self._parent()._obj().pool.get('res.groups').\
                    check_user_groups(cr, uid, [transition._group_id,], context=context):
                return False

        if not transition._condition:
            return True
        else:
            env = Env(cr, uid, self._parent()._obj(), workitem['res_id'] , context)
            
            return safe_eval(transition._condition, env, nocopy=True, mode='eval')

class WkfTransition(object):
    """Cached wkf_activity record and pythonic worker
    """
    def __init__(self, parent, war_res):
        for k, v in war_res.items():
            setattr(self, '_'+k, v)
        self._parent = weakref.ref(parent)

    def __repr__(self):
        return "<transition %d %s for %s>" %(self._id, self._signal or '', self._parent()._obj()._name)

class Env(dict):
    def __init__(self, cr, uid, obj, id, context):
        self.cr = cr
        self.uid = uid
        self._id = id
        self.context = context
        self.obj = obj
        self.columns = self.obj._columns.keys() + self.obj._inherit_fields.keys()

    def __getitem__(self, key):
        if (key in self.columns) or (key in dir(self.obj)):
            res = self.obj.browse(self.cr, self.uid, self._id, self.context)
            return res[key]
        else:
            return super(Env, self).__getitem__(key)

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

