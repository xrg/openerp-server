# -*- encoding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2011 P. Christeas <xrg@hellug.gr>
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################


#.apidoc title: SQL modelling

""" This module does the (temporary) representation of a SQL model,
    so that the ORM can maintain it in an efficient way.
"""

import weakref
import logging
from psycopg2 import DatabaseError, IntegrityError

IDLE_STATES = ('sql', 'done', 'dropped', 'skipped')
drop_guard = True # ORM must unlock it explicitly

POSTGRES_CONFDELTYPES = {
    'r': 'RESTRICT',
    'a': 'NO ACTION',
    'c': 'CASCADE',
    'n': 'SET NULL',
    'd': 'SET DEFAULT',
}

def ifupper(s):
    if s is None:
        return None
    else:
        return s.upper()

class Schema(object):
    def __init__(self, debug=False):
        self.tables = collection('tables', Relation, self)
        self.commands = collection('commands', Command, self)
        self.state = None
        self._debug = debug or []
        self._logger = logging.getLogger('init.sql')
        self.hints = { 'tables': [] }
        self.epoch = 0

    def set_debug(self, elem_name):
        """Sets an element name (table) that will produce more debugging

            Only meaningful if our global debug is not enabled
        """
        if self._debug is not True:
            self._debug.append(elem_name)

    def load_from_db(self, cr):
        """ Load the relations from the database into element structures
            
            @return dict (tables, indices, views, ...), each of them being
                a collection of elements
        """
        
        self.tables.set_state('@sql')
        try:
            self._load_from_db2(cr)
        finally:
            self.tables.commit_state()
            self.commands.set_state('create')
            self.tables._state = 'after-sql'
            self.epoch += 1
            self.tables.get_depends() # quick cleanup
            self.commands.get_depends()
    
    def _load_from_db2(self, cr):
        # Load tables from db
        relnames = self.hints.pop('tables')
        if self._debug:
            self._logger.debug("Fetching %s tables from db", ', '.join(relnames))
        
        cr.execute("""SELECT c.relname, c.oid, c.relkind
            FROM pg_class c
            WHERE c.relname = ANY(%s) AND relkind IN ('r','v')
                  AND c.relnamespace IN (SELECT oid from pg_namespace 
                        WHERE nspname = ANY(current_schemas(false)))""",
                (relnames,), debug=self._debug)

        tbl_reloids = {} # map oid: relname
        for r in cr.dictfetchall():
            if r['relkind'] == 'r':
                self.tables.append(Table(r['relname'], oid=r['oid']))
                tbl_reloids[r['oid']] = r['relname']
            elif r['relkind'] == 'v':
                # pg_get_viewdef(c.oid) AS definition
                self.tables.append(View(r['relname'], oid=r['oid']))
                # need to analyze any further?
            else:
                raise NotImplementedError(r['relkind'])
            
        if tbl_reloids and True:
            # Scan columns
            cr.execute("""SELECT a.attrelid AS reloid, a.attname AS name,
                    a.attnum AS num, t.typname AS ctype,
                    a.attnotnull AS not_null,
                    a.atthasdef AS has_def, pg_get_expr(d.adbin, a.attrelid) AS "default",
                    CASE WHEN a.attlen=-1 THEN a.atttypmod-4 
                        ELSE a.attlen END AS size
                FROM pg_attribute a LEFT JOIN pg_type t ON (a.atttypid=t.oid)
                                LEFT JOIN pg_attrdef d ON (d.adrelid = a.attrelid AND d.adnum = a.attnum)
                WHERE a.attrelid = ANY(%s) AND a.attisdropped = false
                  AND a.attnum > 0""",
                      (tbl_reloids.keys(),), debug=self._debug)
            for c in cr.dictfetchall():
                tbl = self.tables[tbl_reloids[c['reloid']]]
                tbl._get_column(c.copy()) # dopy converts dictRow() to dict

        if tbl_reloids and True:
            # Scan Indices
            cr.execute("""SELECT c.relname AS name, indisunique AS is_unique,
                        i.indrelid AS reloid, indisprimary, indkey
                    FROM pg_index AS i, pg_class AS c
                    WHERE i.indexrelid = c.oid
                      AND c.relnamespace IN (SELECT oid from pg_namespace 
                            WHERE nspname = ANY(current_schemas(false)))
                      AND i.indrelid = ANY(%s) """,
                      (tbl_reloids.keys(),), debug=self._debug)
            for idx in cr.dictfetchall():
                tbl = self.tables[tbl_reloids[idx['reloid']]]
                idx_cols = map(tbl.find_colname, idx.pop('indkey').split(' '))
                if idx['indisprimary'] and (len(idx_cols) == 1) and idx_cols[0]:
                    tbl.columns[idx_cols[0]].primary_key = True
                #elif (len(idx_cols) == 1) and idx_cols[0]:
                #    column index?
                else:
                    tbl.indices.append(Index(colnames=idx_cols,state=tbl.indices._state, **idx))
        
        if tbl_reloids and True:
            # Scan constraints
            cr.execute("""SELECT conname AS name, conrelid AS reloid,
                        contype, conindid AS idx_oid,
                        confupdtype, confdeltype, conkey, fc.relname AS fcname,
                        (SELECT array_agg(attname) FROM (SELECT attname FROM pg_attribute
                                                    WHERE attrelid = fc.oid 
                                                      AND attnum = ANY(confkey)) AS foocols)
                                    AS fc_colnames,
                        pg_get_constraintdef(s.oid) AS definition
                    FROM pg_constraint AS s
                        LEFT JOIN pg_class AS fc ON (s.confrelid = fc.oid)
                    WHERE s.connamespace IN (SELECT oid from pg_namespace 
                            WHERE nspname = ANY(current_schemas(false)))
                      AND s.conrelid = ANY(%s)
                     """,
                    (tbl_reloids.keys(), ), debug=self._debug)
            for con in cr.dictfetchall():
                tbl = self.tables[tbl_reloids[con['reloid']]]
                con_cols = map(tbl.find_colname, con.pop('conkey'))
                if len(con_cols) == 1: # most usual, column constraint
                    if con['contype'] == 'p':
                        # primary key constraint, must already be set by indexes
                        assert tbl.columns[con_cols[0]].primary_key
                        continue
                    elif False and con['contype'] == 'u': # TODO
                        # unique constraint
                        # Not supported yet on column level
                        tbl.columns[con_cols[0]].constraints.append(UniqueColumnConstraint(con['name']))
                        continue
                    elif con['contype'] == 'f':
                        # foreign key constraint
                        assert len(con['fc_colnames']) == 1, con['fc_colnames']
                        tbl.columns[con_cols[0]].constraints.append( \
                                FkColumnConstraint(con['name'], fcname=con['fcname'],
                                                fc_colname=con['fc_colnames'][0],
                                                on_delete=POSTGRES_CONFDELTYPES.get(con['confdeltype']),
                                                on_update=POSTGRES_CONFDELTYPES.get(con['confupdtype'])))
                        continue
                    elif False and con['contype'] == 'c':
                        # check constraint
                        # Not supported yet at column level
                        tbl.columns[con_cols[0]].constraints.append(\
                                CheckColumnConstraint(con['name'], con['definition']))
                        continue
                # table constraint
                if con['contype'] == 'c':
                    # check constraint
                    tbl.constraints.append(CheckConstraint(con['name'], con['definition']))
                    continue
                elif con['contype'] == 'u' and (False not in con_cols):
                    tbl.constraints.append(PlainUniqueConstraint(con['name'], con_cols))
                    continue
                elif con['contype'] == 't':
                    # constraint trigger
                    pass
                elif con['contype'] == 'x':
                    # exclusion constraint
                    pass
                tbl.constraints.append(OtherTableConstraint(**con))

    def commit_to_db(self, cr, dry_run=False):
        """ Apply the changes of schema on the database
        
            @param schema is the dictionary returned by load_from_db(), modified
                as needed
        """
        
        logger = self._logger
        
        def does_debug(tbl_name):
            if self._debug is True:
                return True
            elif not self._debug:
                return False
            else:
                return (tbl_name in self._debug)
        
        need_more = True
        max_epoch = self.epoch + 5000
        while need_more:
            done_actions = False
            need_more = False
            if self.epoch >= max_epoch: # would we ever need more levels of epochs?
                logger.error("DB epoch overflow, cannot initialize/update database")
                logger.info("Actions remaining, please fix them manually!\n%r",
                        self._dump_todo())
                raise RuntimeError("Cannot update schema")
        
            logger.debug("Creating or updating, epoch %d", self.epoch)
            # Section 1: tables
            for tbl in self.tables:
                if tbl.is_idle():
                    continue
                tstate = tbl._state # make a copy
                need_more = True
                
                if not tbl.get_depends():
                    continue
                
                sql = args = None
                logger.debug("Working on relation %s", tbl._name)
                
                try:
                    if tstate in ('create', 'alter', 'drop'):
                        sql, args = tbl.pop_sql(epoch=self.epoch, partial=False)
                    else:
                        logger.error("What is %s state?", tbl._state)
                    
                    if not sql:
                        continue

                    if dry_run or does_debug(tbl._name):
                        logger.debug("Command for %s: %s +%r", tbl._name, sql, args)

                    if not dry_run:
                        cr.execute('SAVEPOINT "full_%s";' % tstate)
                        cr.execute(sql, args)
                        cr.execute('RELEASE SAVEPOINT "full_%s";' % tstate)
                    
                    done_actions = True
                    tbl.commit_state()
                    if does_debug(tbl._name):
                        logger.debug("After create, state: \n %s", pretty_print(tbl))
                    sql = False # reset it, we're done
                except DatabaseError:
                    cr.execute('ROLLBACK TO SAVEPOINT "full_%s";' % tstate)
                    logger.warning("Full command %s... failed. Re-trying with separate queries", sql[:25])
                    logger.debug("Object: %s", pretty_print(tbl))
                    tbl.rollback_state()

            # Section 2: commands
            for cmd in self.commands:
                if cmd.is_idle():
                    continue
                need_more = True

                if not cmd.get_depends():
                    continue

                try:
                    if cmd._state != 'create':
                        logger.warning("What is %s state?", tbl._state)

                    if dry_run:
                        logger.debug("Command: %s", cmd.get_dry())
                    else:
                        cr.execute('SAVEPOINT "cmd_run";')
                        cmd.set_state('@'+cmd._state)
                        cmd.execute(cr)
                        cr.execute('RELEASE SAVEPOINT "cmd_run";')

                    done_actions = True
                    cmd.commit_state()
                except DatabaseError:
                    cr.execute('ROLLBACK TO SAVEPOINT "cmd_run";')
                    cmd.rollback_state()

            # TODO: here go indices etc..
            
            if done_actions:
                # move on with other elements that can fully proceed
                self.epoch += 1
                continue

            # slow part of the loop:

            for tbl in self.tables:
                if tbl.is_idle():
                    continue
                tstate = tbl._state # make a copy
                need_more = True
                
                if not tbl.get_depends(partial=True):
                    if does_debug(tbl._name):
                        logger.debug("skip table %s because of partial depends", tbl._name)
                    continue

                sql = args = None
                logger.debug("Working on relation %s", tbl._name)
                
                try:
                    if tstate in ('create', 'alter', 'drop'):
                        sql, args = tbl.pop_sql(epoch=self.epoch, partial=True)
                    else:
                        logger.error("What is %s state?", tbl._state)
                    
                    if not sql:
                        continue

                    if dry_run or does_debug(tbl._name):
                        logger.debug("Command for %s: %s +%r", tbl._name, sql, args)

                    if not dry_run:
                        cr.execute('SAVEPOINT "partial_%s";' % tstate)
                        cr.execute(sql, args)
                        cr.execute('RELEASE SAVEPOINT "partial_%s";' % tstate)
                    
                    done_actions = True
                    tbl.commit_state()
                except DatabaseError:
                    cr.execute('ROLLBACK TO SAVEPOINT "partial_%s";' % tstate)
                    logger.error("Command failed, inspect your data and try to execute it manually: %s +%r", sql, args)
                    logger.debug("Object: %s", pretty_print(tbl))
                    tbl.commit_state(failed=True)
            
            # TODO indices, constraints etc. components (partial)

            if need_more and not done_actions:
                logger.error("Have nothing to do at epoch #%d", self.epoch)
                raise RuntimeError("Idle at epoch %d" % self.epoch)
            self.epoch += 1

        return not need_more
        
    def _dump_todo(self):
        """ Dump all pending database actions in a single SQL-like string
        """
        return self.pretty_print(todo_only=True)
        
    def pretty_print(self, todo_only=False):
        ret = "--- Tables: %d \n" % len(self.tables)
        for tbl in self.tables:
            r2 = pretty_print(tbl, todo_only=todo_only) 
            if r2:
                ret += r2 + '\n'
        if self.commands:
            ret += '\nCommands: %d\n' % len(self.commands)
            for cmd in self.commands:
                r2 = pretty_print(cmd, todo_only=todo_only)
                if r2:
                    ret += r2 + '\n'
        return ret

def pretty_print(elem, indent=0, todo_only=False):
    """ Recursively print the collection
        @return a multi-line string
    """
    assert isinstance(elem, _element), "elem is %s: %r" %(type(elem), elem)
    if todo_only and elem._state == 'sql':
        return ''
    ret = (' ' * indent) + repr(elem)
    if elem._state not in ('sql',):
        dep = ''
        if elem._depends:
            dep = 'depends on: '
            dep += ','.join(['%s' % e() for e in elem._depends])
        ret += ' [%s %s]' % (elem._state, dep)
    if todo_only and elem._state in IDLE_STATES:
        return ret
    if isinstance(elem, collection):
        if indent > 80:
            return ret
        ret += '{'
        for e2 in elem:
            r2 = pretty_print(e2, indent+4, todo_only=todo_only)
            if r2:
                ret += '\n' + r2
        
        ret += '\n' +(' ' * indent) + '}'
    elif isinstance(elem, Relation):
        if indent > 80:
            return ret
        ret += '{'
        if not elem.columns.is_idle():
            ret += '\n'+ (' ' * indent) + 'columns: %s' % elem.columns._state
        for e2 in elem.columns:
            r2 = pretty_print(e2, indent+4, todo_only=todo_only)
            if r2:
                ret += '\n' + r2

        if isinstance(elem, Table):
            if not elem.indices.is_idle():
                ret += '\n'+ (' ' * indent) + 'indices: %s' % elem.indices._state
            ret += '\n'
            for e2 in elem.indices:
                r2 = pretty_print(e2, indent+4, todo_only=todo_only)
                if r2:
                    ret += '\n' + r2
            
            if not elem.constraints.is_idle():
                ret += '\n'+ (' ' * indent) + 'constraints: %s' % elem.constraints._state
            if len(elem.constraints):
                for e2 in elem.constraints:
                    r2 = pretty_print(e2, indent+4, todo_only=todo_only)
                    if r2:
                        ret += '\n' + r2

        ret += '\n' +(' ' * indent) + '}'
    
    return ret

class _element(object):
    """ mostly for tracing
    
        @attribute _state will mark the phase in which that element was last
            modified. Used to distinguish ones that are already in the SQL
            db from ones that need to be CREATEd/ALTERed
        @attribute _depends a list of weak references to other elements
            on which we depend
    """
    
    _elem_attrs = [] #: names of our attributes, which are elements (see: Column, Relation)
    _wait_depends = True #: unlock ourself one epoch later than our dependencies
    _wait_for_me = True #: this element can block parents depends

    def __init__(self, name):
        self._name = name
        self._state = None
        self.parent = None
        self._depends = []
        self._depends_on_alter = False
        self.last_epoch = None #: last time the operations had been attempted

    def __repr__(self):
        return "%s(%s)" % (self.__class__.__name__, self._name)

    def __str__(self):
        return "%s(%s)" % (self.__class__.__name__, self._name)

    def _sub_elems(self):
        return [getattr(self, e) for e in self._elem_attrs]
    
    def parent_schema(self):
        
        par = self.parent()
        while par:
            if isinstance(par, Schema):
                return par
            elif not par.parent():
                raise ValueError("No parent for %s", par)
            else:
                par = par.parent()
        
        raise ValueError("No parent schema for %r" % self)
        
    def set_state(self, state):
        """ setter, overridable
        """
        self._state = state
        for elem in self._sub_elems():
            elem.set_state(state)

    def is_idle(self):
        return self._state in IDLE_STATES or self._state.startswith('failed:')

    def mark(self):
        """Set the state as 'done', so that we know we need this element
        
            Used to distinguish the elements which are redundant in the
            database and need to be dropped.
        """
        if self._state in ('sql', 'drop'):
            self._state = 'done'
        elif self._state == 'dropped':
            if self.parent():
                parent_name = self.parent()._name + '.'
            else:
                parent_name = ''
                logging.getLogger('init.sql').error( \
                    'Element %s%s is already dropped, but may still be needed!',
                    parent_name, self._name)

    def drop(self):
        """Mark this element to drop.
        """
        if self._state == 'create':
            self.set_state('dropped')
        elif self._state == 'rename':
            raise NotImplementedError
        else:
            self.set_state('drop')

    def commit_state(self, failed=False):
        """ Set state as "done" for each kind of operation

            Please, override this if we have sub-components!
            @return if we had any state changes
        """
        if not self._state.startswith('@'):
            for e in self._sub_elems():
                assert not e._state.startswith('@'), '%s.%s' %(self._name, e._name)
            return False

        child_changes = False
        need_alter = False
        for e in self._sub_elems():
            pcs = e._state
            if e.commit_state(failed=failed):
                child_changes = True
            if not e.is_idle():
                need_alter = True
        if failed:
            if not child_changes:
                # If we couldn't identify the single sub-element that failed
                self._state = 'failed:' + self._state[1:]
            else:
                self._state = self._state[1:] # retry
        elif self._state == '@sql':
            self._state = 'sql'
        elif self._state in ('@create', '@alter', '@rename'):
            if need_alter:
                self._state = 'alter'
            else:
                self._state = 'done'
        elif self._state == '@drop':
            self._state = 'dropped'
        #elif self._state == 'create' and not need_alter:
        #    self._state = 'sql'
        else:
            print "strange state:", self._state
            return child_changes
        return True

    def rollback_state(self):
        """ Set back state, we failed to update
        """
        if self._state.startswith('@'):
            self._state = self._state[1:]
            
        for elem in self._sub_elems():
            elem.rollback_state()

    def set_depends(self, other, on_alter=False):
        """ self depends on other
        
            @param other an element
            @param on_alter fire even if the other is at 'alter', aka. just
                    after it has been created
        """
        # TODO revise the algorithm
        if other.is_idle():
            # we can depend on that right now, not bother about epochs
            return
        
        if on_alter and other._state == 'alter':
            return
        self._depends.append(weakref.ref(other))
        if on_alter:
            self._depends_on_alter = True

    def get_depends(self, partial=False):
        """Return if we can proceed, dependencies are satisfied
        """
        def _flt(d):
            if not d():
                return False
            if d().is_idle():
                return False
            if self._depends_on_alter and d()._state in ('alter', '@alter'):
                return False
            return True

        self._depends = filter( _flt, self._depends)
        if self._wait_depends:
            if self._depends:
                return False
        else:
            for d in self._depends:
                if d()._state.startswith('@'):
                    pass
                else:
                    return False
        
        for e in self._sub_elems():
            if isinstance(e, collection) and len(e) == 0 and not e.is_idle():
                # fix empty collections being non-idle
                e._state = 'done'

        if partial:
            # proceed if any of the elements is non-idle
            clear = True
            for e in self._sub_elems():
                if e.is_idle():
                    continue
                if e.get_depends(partial=partial):
                    return True
                else:
                    clear = False
            return clear
        else:
            for e in self._sub_elems():
                if e.is_idle():
                    continue
                if not e.get_depends(partial=partial):
                    return False
            return True

class collection(_element):
    """ hybrid dict/unordered list
    
        No setitem, use the append()
    """
    
    def __init__(self, name, klass, parent):
        """
            @param klass all elements of this collection should be instances
                    of that class
        """
        _element.__init__(self, name)
        self.parent = weakref.ref(parent)
        assert parent
        self._d = dict()
        assert issubclass(klass, _element), klass
        self._baseclass = klass
    
    def __getitem__(self, name):
        return self._d[name]
    
    def __iter__(self):
        return self._d.itervalues()
        
    def __len__(self):
        return len(self._d)

    def __contains__(self, name):
        return name in self._d

    def append(self, elem):
        assert (elem is not None) and elem._name not in self._d, elem._name
        assert isinstance(elem, self._baseclass)
        assert self._state is not None
        if self._state == '@sql':
            elem.set_state(self._state) # mark child element with our state
        else:
            elem.set_state('create')
        elem.parent = weakref.ref(self)
        self._d[elem._name] = elem
        return elem

    def commit_state(self, failed=False):
        
        has_changes = False
        need_alter = False
        if not self._state.startswith('@'):
            for e in self:
                assert not e._state.startswith('@'), '%s.%s' %(self._name, e._name)
            return False

        for e in self:
            if e.commit_state(failed=failed):
                has_changes = True
            if not e.is_idle():
                need_alter = True
        
        if _element.commit_state(self, failed=(failed and not has_changes)):
            has_changes = True
        if (self._state in ('sql', 'done')) and need_alter:
            assert len(self),"%s: %s" %(self, self._state)
            self._state = 'alter'
        #elif False and self._state == 'create' and not need_alter:
        #    self._state = 'done'
        #    has_changes = True

        return has_changes
        
    def cleanup(self):
        """ remove 'dropped' elements
        """
        for k in self._d.keys():
            if self._d[k]._state == 'dropped':
                del self._d[k]

    def rename(self, oldname, newname):
        """Rename element `oldname` to `newname`
        
            The element will shift its name, take `newname` as its `_name`
            immediately.
            
            @return element
        """
        
        assert oldname in self._d, oldname
        assert self._d[oldname]._state not in ('dropped', 'drop', 'skipped', 'rename'), \
            "%s element %s is already in %s state" % (self._baseclass.__name__, oldname, self._d[oldname]._state)
        assert newname not in self._d, "element %s already exists" % newname
        elem = self._d.pop(oldname)
        elem._name = newname
        elem.oldname = oldname
        elem._state = 'rename' # use the shallow setter, not set_state()
        self._d[newname] = elem
        return elem
        
    def rollback_state(self):
        _element.rollback_state(self)
        for elem in self:
            elem.rollback_state()
    
    def get_depends(self, partial=False):
        clear = super(collection, self).get_depends(partial=partial)
        if not clear:
            return False
        
        if partial:
            # Just look for one element that would be able to proceed
            clear = True
            for e in self:
                if e.is_idle():
                    continue
                if e.get_depends(partial=partial):
                    return True
                elif e._wait_for_me:
                    clear = False
            return clear
        else:
            # Check that all elements can proceed
            for e in self:
                if e.is_idle():
                    continue
                if not e.get_depends(partial=partial):
                    return False
            return True

class Column(_element):
    """ represents a table/view column, with all its properties
    """
    
    _elem_attrs = ['constraints',]
    
    class now(object):
        """ Placeholder for the "now()" default value function
        """
        pass
    # TODO perhaps more functions
    
    def __init__(self, name, ctype, num=None, size=None, has_def=None,
                not_null=None, default=None, primary_key=None, constraint=None):
        _element.__init__(self, name)
        self.num = num # used as an index for other references
        self.ctype = ctype
        self.size = size
        self.has_def = has_def or (default is not None)
        self.default = default
        self.primary_key = primary_key
        self.constraints = collection('constraints', ColumnConstraint, self)
        # self.constraints._wait_for_me = False # We can do things without them ?
        self.not_null = not_null
        self._todo_attrs = {}
        if constraint:
            self.constraints.append(constraint)

    PG_TYPE_ALIASES = {
        'CHAR': 'char',
        #'VARCHAR': 'character varying'
        'DOUBLE PRECISION': 'float8',
        'BIGINT': 'int8',
        'BOOLEAN': 'bool',
        'INTEGER': 'int4',
        # 'NUMERIC': 'decimal', ?
        'REAL': 'float4',
        }

    def set_state(self, newstate):
        super(Column, self).set_state(newstate)
        if newstate == '@create':
            # When we create the column, we implicitly create all constraints
            for c in self.constraints:
                if isinstance(c, NotNullColumnConstraint):
                    continue
                c.set_state('@create')

    def rollback_state(self):
        super(Column, self).rollback_state()
        if getattr(self, '_old_todo', False):
            self._todo_attrs = self._old_todo

    def __repr__(self):
        return self._to_create_sql([])

    def _to_create_sql(self, args):
        """ return the SQL string that could create the column
            @param args append corresponding SQL arguments there
        """
        ret = '"%s" %s' % (self._name, self.ctype.upper())
        if self.ctype.lower() in ('char', 'varchar') and self.size:
            ret += '(%d)' % self.size
        if self.has_def:
            if self.default is None:
                ret += " DEFAULT null"
            elif isinstance(self.default, Column.now):
                ret += " DEFAULT now()"
                # TODO: more non-scalar ones
            else:
                ret += " default %s"
                args.append(self.default)

        # These are constraints, go after the 'default'
        if self.not_null:
            ret += ' NOT NULL'
        if self.primary_key:
            ret += ' PRIMARY KEY'
        for c in self.constraints:
            if isinstance(c, NotNullColumnConstraint):
                if self._state == '@create':
                    raise RuntimeError("Not null applies too early for %s", self._name)
                continue
            ret += ' ' + c._to_create_sql(args)
        return ret
   
    def pop_sql(self, args, epoch=None, partial=False, dry_run=False):
        """ Returns list of commands to adapt the column
        """
        ret = []
        self._old_todo = self._todo_attrs.copy()
        
        def alter_column(cmd):
            ret.append('ALTER COLUMN "%s" %s' % (self._name, cmd))
        for t in [True,]:
            # one time loop, allows us to break from it
        
            if 'default' in self._todo_attrs:
                val = self._todo_attrs.pop('default')
                if val is None:
                    alter_column('DROP DEFAULT')
                elif isinstance(val,Column.now):
                    alter_column('SET DEFAULT now()')
                else:
                    alter_column('SET DEFAULT %s')
                    args.append(val)
            if ret and partial:
                break
                
            if 'not_null' in self._todo_attrs:
                if self._todo_attrs.pop('not_null'):
                    alter_column('SET NOT NULL')
                else:
                    alter_column('DROP NOT NULL')
            if ret and partial:
                break

            if 'size' in self._todo_attrs or 'type' in self._todo_attrs:
                newtype = self._todo_attrs.pop('type', self.ctype)
                newsize = self._todo_attrs.pop('size', self.size)
                if newsize and newtype.lower() in ('char', 'varchar', 'character varying'):
                    alter_column('TYPE %s(%s)' % (newtype, newsize))
                else:
                    alter_column('TYPE %s' % newtype)
            
            if ret and partial:
                break
            
        if ret and not dry_run:
            self._state = '@' + self._state
            self.last_epoch = epoch
        
        if not self.constraints.is_idle():
            for con in self.constraints:
                if con.is_idle() or not con.get_depends():
                    continue
                if isinstance(con, NotNullColumnConstraint):
                    assert con._state == 'create', "%s: %s" % (self._name, con._state)
                    alter_column('SET NOT NULL')
                elif con._state == 'create':
                    ret.append('ADD CONSTRAINT "%s" %s' %(con._name, con._to_table_constraint(self._name, args)))
                elif con._state == 'drop':
                    ret.append('DROP CONSTRAINT "%s"' % con._name)
                else:
                    raise RuntimeError('How to handle column "%s" constraint in state "%s"?' % \
                            (self._name, con._state))
                if not dry_run:
                    con._state = '@' + con._state
                if partial:
                    break
            
            if ret and not dry_run:
                if not self._state.startswith('@'):
                    self._state = '@' + self._state
                if not self.constraints._state.startswith('@'):
                    self.constraints._state = self._state
                self.last_epoch = epoch

        if self._todo_attrs and not partial:
            raise NotImplementedError("Cannot know how to alter %s for %r" % (self._name, self._todo_attrs))
        return ret

class Relation(_element):
    """
        @attr name
        @attr kind
    """
    
    _elem_attrs = ['columns', ]
    def __init__(self, name, oid=None, comment=None):
        _element.__init__(self, name=name)
        self._oid = oid
        #state = 'create' | 'ok' | 'update' | 'drop'
        #indices (table)
        self.columns = collection('columns', Column, self)
        #constraints (table)
        self.comment = comment

    def _get_column(self, c):
        """ Called when scanning the tables' columns, populates this struct
        """
        c.pop('reloid')
        self.columns.append(Column(**c)) # TODO

    def find_colname(self, cnum):
        """ return the column name of column #cnum
        """
        if not (cnum and int(cnum)):
            # indkey may contain zeroes
            return False
        for c in self.columns:
            if c.num == int(cnum):
                return c._name
        raise IndexError(cnum)

class ColumnConstraint(_element):
    """Single-column constraint
    """

    _wait_depends = False
    def __repr__(self):
        return self._to_create_sql([])

    def _to_create_sql(self, args):
        raise NotImplementedError

    def _to_table_constraint(self, colname, args):
        """ reformat this constraint as a table-wide expression
        
            When we add this constraint late, we cannot write the same
            expression as the column shorthand.
            @param colname name of column
        """
        raise NotImplementedError

    def commit_state(self, failed=False):
        """ Column constraints go from @alter -> create
        
        """
        if not self._state.startswith('@'):
            return False
        if failed:
            self._state = 'failed:' + self._state[1:]
        elif self._state == '@sql':
            self._state = 'sql'
        elif self._state  == '@create':
            self._state = 'done'
        elif self._state == '@alter':
            self._state = 'create'
        elif self._state == '@drop':
            self._state = 'dropped'
        else:
            return False
        return True

class UniqueColumnConstraint(ColumnConstraint):

    def _to_create_sql(self, args):
        return 'UNIQUE'
    
    def _to_table_constraint(self, colname, args):
        return 'UNIQUE("%s")' % colname

class CheckColumnConstraint(ColumnConstraint):
    def __init__(self, name, definition):
        _element.__init__(self, name)
        self.definition = definition

    def _to_create_sql(self, args):
        return self.definition

class FkColumnConstraint(ColumnConstraint):
    """A single-column foreign-key constraint
        Stores the necessary info for the REFERENCES clause
    """
    def __init__(self, name, fcname, fc_colname, on_update=None, on_delete=None):
        _element.__init__(self, name)
        self.fcname = fcname
        self.fc_colname = fc_colname
        self.on_update = ifupper(on_update)
        self.on_delete = ifupper(on_delete)

    def __str__(self):
        return 'REFERENCES %s(%s)' % (self.fcname, self.fc_colname)

    def _to_table_constraint(self, colname, args):
        return ('FOREIGN KEY("%s") ' % colname) + self._to_create_sql(args)

    def _to_create_sql(self, args):
        ret = 'REFERENCES %s(%s)' % (self.fcname, self.fc_colname)
        if self.on_update:
            ret += ' ON UPDATE %s' % self.on_update
        if self.on_delete:
            ret += ' ON DELETE %s' % self.on_delete
        return ret

class NotNullColumnConstraint(ColumnConstraint):
    """ Special, transient element for the NOT NULL constraint
    
        This is mostly covered through the 'not_null' attribute of the
        Column, but sometimes this is not possible to apply until the
        data are updated with default values. Then, use this element
        to have the 'not null' depend on the update command
    """
    _wait_depends = True # cannot apply at the same epoch as the column
    _wait_for_me = False

    def __init__(self):
        ColumnConstraint.__init__(self, name=False)

    def commit_state(self, failed=False):
        """ Column constraints go from @alter -> create
        
        """
        if not self._state.startswith('@'):
            return False
        
        if failed:
            self._state = 'failed:' + self._state[1:]
        elif self._state == '@create':
            self._state = 'dropped'
        else:
            raise RuntimeError("state: %s?" % self._state)
            # return False
        return True

class Table(Relation):
    """ Represents a regular SQL table
    """
    
    _elem_attrs = ['columns', 'constraints', 'indices']
    
    def __init__(self, name, **kwargs):
        Relation.__init__(self, name=name, **kwargs)
        self.indices = collection('indices', Index, self)
        self.constraints = collection('constraints', TableConstraint, self)
        self._logger = logging.getLogger('init.sql')

    def check_column(self, colname, ctype, not_null=None, references=None,
            size=None, default=None, select=None, comment=None,
            do_create=True, do_alter=True, do_reset=True):
        """ Checks if colname is among our columns, repairs if needed
        
            Note: this function only works for plain columns
            
            @param colname the name of the column
            @param ctype the column type, in "create table" format
            @param references a dict for the foreign key. It shall
                be like {'table': fk_table, ['column': 'id' (default),] 
                        ['on_delete': 'set null',] ['on_update': ...'] }
            @param size for char/varchar fields
            @param default Default value expression
            @param select if this column needs a simple index
            @param comment When creating the column, use this comment (TODO)
            @param do_create If missing, create column
            @param do_ater If different, alter column
            @param do_reset if cannot match, move the old column away and
                create new one
            
            @return Boolean, if all actions were feasible
        """
        
        can_do = True
        moved_col = None
        casts = { 'text': ('char', 'varchar'),
                  'varchar': ('char', 'text' ),
                  'int4': ('float', 'float8', 'numeric'),
                  'date': ('datetime', 'timestamp'),
                  'timestamp': ('date', ),
                  'numeric': ('float', 'integer', 'float8'),
                  'float8': ('float', ),
                }
        if colname in self.columns:
            col = self.columns[colname]
            if col.ctype.lower() not in (ctype.lower(), Column.PG_TYPE_ALIASES.get(ctype.upper(), 'any')):
                self._logger.info("Column %s.%s must change type from %s to %s aka. %s",
                        self._name, colname, col.ctype, ctype, Column.PG_TYPE_ALIASES.get(ctype, '-'))
                # TODO code for non-trivial casting
                allowed_casts = casts.get(col.ctype.lower(),())
                if not allowed_casts:
                    self._logger.warning("are we sure there can be no cast from type %s?", col.ctype)
                if ctype.lower() in allowed_casts\
                        or Column.PG_TYPE_ALIASES.get(ctype.upper(), 'any') in allowed_casts:
                    col._todo_attrs['type'] = ctype
                else:
                    i = 0
                    while i < 100:
                        newname = colname + '_moved' + str(i)
                        if newname not in self.columns:
                            break
                        i+=1
                    self._logger.warning("column '%s' in table '%s' could not change type (DB=%s, def=%s), data moved instead to %s !" % \
                            (colname, self._name, col.ctype, ctype, newname))
                    moved_col = self.columns.rename(colname, newname)
                    moved_col._todo_attrs['not_null'] = False

        if colname not in self.columns:
            if not do_create:
                return False
            plain_default = None
            if default and not isinstance(default, Command):
                plain_default = default
            newcol = self.columns.append(Column(name=colname, ctype=ctype,
                size=size, default=plain_default,
                not_null=not_null and (self._state == 'create' or plain_default is not None)))
            if moved_col:
                newcol.set_depends(moved_col)
            if comment:
                newcol.comment = comment
            if references:
                new_name = "%s_%s_fkey" %(self._name, colname)
                newcol.constraints.append(FkColumnConstraint(new_name, references['table'],
                        references.get('column', 'id'), 
                        on_delete=references.get('on_delete', None),
                        on_update=references.get('on_update', None)))
                if references['table'] in self.parent():
                    ref_tbl = self.parent()[references['table']]
                    ref_colname = references.get('column', 'id')
                    if ref_colname in ref_tbl.columns:
                        newcol.set_depends(ref_tbl.columns[ref_colname])
                    else:
                        self._logger.debug("Table %s.%s column is missing for foreign key of %s.%s",
                                references['table'], ref_colname, self._name, colname)
                        newcol.set_depends(ref_tbl, on_alter=True)
                else:
                    self._logger.warning("Column %s.%s set to reference %s(%s), but the latter table is not known",
                            self._name, colname, references['table'], references.get('column', 'id'))
            
            if self._state != 'create' and isinstance(default, Command):
                # We need to execute the command (to update defaults) _after_
                # the column has been added, and then enforce the NOT NULL constraint
                self.parent_schema().commands.append(default)
                default.set_depends(newcol, on_alter=True)
                if not_null:
                    nnc = newcol.constraints.append(NotNullColumnConstraint())
                    nnc.set_depends(default)
            if select:
                idx = self.indices.append(Index('%s_%s_idx' % (self._name, colname),
                                            colnames=[colname], state='create'))
                idx.set_depends(newcol)
            
            assert newcol._state == 'create', "%s %s" % (newcol._state, self._state)
            if self._state in IDLE_STATES:
                self._state = 'alter'
        else:
            col = self.columns[colname]
            plain_default = None
            if default and not isinstance(default, Command):
                plain_default = default
            if not_null is not None:
                if not_null != col.not_null:
                    if (not not_null) or plain_default:
                        col._todo_attrs['not_null'] = not_null
                    if not not_null:
                        for c in col.constraints:
                            if isinstance(c, NotNullColumnConstraint):
                                c.drop()
                else:
                    col._todo_attrs.pop('not_null', None)
            if references is not None:
                if references:
                    found_ref = False
                    for con in col.constraints:
                        if isinstance(con, FkColumnConstraint):
                            con_delete = ifupper(references.get('on_delete', None))
                            con_update = ifupper(references.get('on_update', None))
                            if ((not found_ref) and con.fcname == references['table'] \
                                    and con.fc_colname == references.get('column', 'id') \
                                    and (con_delete is None or con.on_delete == con_delete) \
                                    and (con_update is None or con.on_update == con_update)):
                                found_ref = True
                            else:
                                self._logger.debug("column constraint mismatch on %s: %r -> %r", self._name, references, con)
                                con.set_state('drop')
                    if not found_ref:
                        new_name = "%s_%s_fkey" %(self._name, colname)
                        i = 1
                        while i and new_name in col.constraints:
                            new_name = "%s_%s%d_fkey" %(self._name, colname, i)
                            i += 1
                            if i > 3:
                                break
                        new_con = col.constraints.append(FkColumnConstraint(new_name, references['table'],
                                references.get('column', 'id'), 
                                on_delete=references.get('on_delete', None),
                                on_update=references.get('on_update', None)))
                        if references['table'] in self.parent():
                            new_con.set_depends(self.parent()[references['table']])
                else:
                    # try to remove foreign key constraints from column
                    for con in col.constraints:
                        if isinstance(con, FkColumnConstraint):
                            print "column constraint need not be: ", con
                            con.drop()
            if size is not None and \
                    (col._todo_attrs.get('ctype', col.ctype).lower() \
                            in ('char', 'varchar', 'character varying')):
                if size > col.size:
                    self._logger.info("column '%s' in table '%s' increased size to %d.",
                            colname, self._name, size)
                    col._todo_attrs['size'] = size
                #elif size < col.size: TODO may need to check data, non-trivial
                else:
                    col._todo_attrs.pop('size', None)
            if plain_default is not None:
                if plain_default != col.default:
                    col._todo_attrs['default'] = default
                else:
                    col._todo_attrs.pop('default', None)
            elif col.default:
                # we have to drop the SQL static default
                col._todo_attrs['default'] = None
            if isinstance(default, Command) and not col.not_null:
                # We need to execute the command (to update defaults) _after_
                # the column has been added, and then enforce the NOT NULL constraint
                # If the not_null is already set for the column, we assume all
                # data is already updated.
                assert not default.parent
                self.parent_schema().commands.append(default)
                default.set_depends(col)
                if not_null:
                    for c in col.constraints:
                        if isinstance(c, NotNullColumnConstraint):
                            break
                    else:
                        nnc = col.constraints.append(NotNullColumnConstraint())
                        nnc.set_depends(default)
            if select is not None:
                found_indices = [ i for i in self.indices \
                        if len(i.columns) == 1 and colname in i.columns]
                if select:
                    # add index, if needed
                    if not found_indices:
                        self.indices.append(Index('%s_%s_idx' % (self._name ,colname),
                                colnames=[colname],state='create'))
                    else:
                        for idx in found_indices:
                            idx.mark()
                else:
                    # remove indices
                    for idx in found_indices:
                        if idx.indirect:
                            idx.mark()
                            continue
                        idx.drop()
                    self.indices.cleanup()
            
            if col._state not in ('create', 'rename') and col._todo_attrs:
                col._state = 'alter'
            else:
                col.mark()
        
            if col._state not in IDLE_STATES and self._state in IDLE_STATES:
                self._state = 'alter'
        return can_do
    
    def check_constraint(self, conname, obj, condef):
        """ verify or create an sql constraint
        
            @param conname Name of the constraint
            @param obj ORM model
            @param condef Definition of the constraint (text?)
        """
        if conname in self.constraints:
            # For the time being, assume just the same name is OK
            self.constraints[conname].mark()
        else:
            con = self.constraints.append(OtherTableConstraint(name=conname, definition=condef))
            # con.set_depends(self) needed?
            
        return True

    def column_or_renamed(self, colname, oldname=None):
        """ Retrieve the named column, or `oldname` through a rename
        
            @return a column reference, if either of the names match,
                or None
        """
        
        if colname in self.columns:
            return colname
        elif oldname and oldname in self.columns:
            return self.columns.rename(oldname, colname)
        else:
            return None

    def pop_sql(self, epoch, partial=False, dry_run=False):
        """ Return the SQL command (string) for this table
            
            This function is stateful. It will also mark the elements
            as 'in process'.
            
            @param dry_run For testing purposes, don't alter any state
        """
        ret = ''
        args = []
        do_columns = do_constraints = False

        if self._state == 'create':
            ret = 'CREATE TABLE %s (' % self._name
                
            ret_col = []
            for col in self.columns:
                if col._state != 'create':
                    self._logger.warning("Column: %s.%s: what is state %s when table is not created yet?",
                            self._name, col._name, col._state)
                    continue
                if not col.get_depends(partial=partial):
                    continue
                if partial and epoch == col.last_epoch:
                    # This column cannot be created yet
                    continue
                ret_col.append(col._to_create_sql(args))
                if not dry_run:
                    col.set_state('@' + col._state)
                    col.last_epoch = epoch
                    do_columns = True
            
            for con in self.constraints:
                if con._state != 'create':
                    self._logger.warning("Table Constraint: %s.%s: what is state %s when table is not created yet?",
                        self._name, con._name, con._state)
                    continue
                elif partial and ret_col and epoch == con.last_epoch:
                    continue
                elif not con.get_depends(partial=partial):
                    continue
                ret_col.append('CONSTRAINT ' + con._to_create_sql(args))
                if not dry_run:
                    con._state = '@' + con._state
                    con.last_epoch = epoch
                    do_constraints = True
            
            assert ret_col, "Why can't we %s create anything at epoch %d? %r" % (\
                    partial and 'partially' or '', epoch, \
                    [ "%s: %s %s %s" % (c._name, c._state, c.get_depends(), c.last_epoch) \
                            for c in self.columns])

            ret += ',\n\t'.join(ret_col)
            
            ret += ');\n'
        elif self._state == 'alter':
            ret += 'ALTER TABLE %s ' % self._name
            
            ret_col = []
            for col in self.columns:
                if col.is_idle():
                    continue
                if not col.get_depends(partial=partial):
                    # This column cannot be altered yet
                    continue
                elif partial and ret_col and col.last_epoch == epoch:
                    continue
                if col._state == 'create':
                    col_sql = col._to_create_sql(args)
                    if not dry_run:
                        col.set_state('@' + col._state) # recursive, including constraints
                        col.last_epoch = epoch
                    ret_col.append('ADD COLUMN %s' % col_sql)
                elif col._state == 'alter':
                    col_sqls = col.pop_sql(args, epoch=epoch, partial=partial, dry_run=dry_run)
                    if not col_sqls:
                        continue
                    ret_col.extend(col_sqls)
                elif col._state == 'rename':
                    if ret_col:
                        # ALTER TABLE.. RENAME column must only perform this
                        # single operation
                        continue
                    else:
                        ret_col.append('RENAME COLUMN "%s" TO "%s" ' % (col.oldname, col._name))
                        partial = True # switch to partial mode, avoid
                                       # any other operations at this pass
                        col._state = '@rename'
                        col.last_epoch = epoch
                elif col._state == 'drop':
                    if drop_guard:
                        self._logger.warning("Column %s.%s should be dropped, but drop_guard won't allow that!", self._name, col._name)
                        col._state = 'skipped'
                        continue
                    ret_col.append('DROP COLUMN "%s" ' % col._name)
                    col._state = '@drop'
                    col.last_epoch = epoch
                else:
                    self._logger.warning("How can I alter column %s state %s?", col._name, col._state)
                    continue
                if not dry_run:
                    assert ret_col, "%s.%s" %(self._name, col._name)
                    do_columns = True
                if partial:
                    break
            
            if not (partial and ret_col):
                for con in self.constraints:
                    if con.is_idle():
                        continue
                    if not con.get_depends(partial=partial):
                        # This constraint cannot be created yet
                        continue
                    elif partial and con.last_epoch == epoch:
                        continue
                    if con._state == 'create':
                        ret_col.append('ADD CONSTRAINT ' +con._to_create_sql(args))
                    elif con._state in ('alter', 'drop'):
                        # we cannot "alter" a constraint, so we drop + create it
                        ret_col.append('DROP CONSTRAINT "%s"' % con._name)
                    else:
                        self._logger.warning("How can I alter constraint %s state %s?", con._name, con._state)
                        continue
                    if not dry_run:
                        con.set_state('@' + con._state)
                        con.last_epoch = epoch
                        do_constraints = True
                    if partial:
                        break

            if not ret_col:
                for idx in self.indices:
                    if idx.is_idle():
                        continue
                    if not idx.get_depends():
                        continue
                    elif partial and idx.last_epoch == epoch:
                        continue
                    
                    if idx._state == 'create':
                        ret = idx._to_create_sql(self._name, args)
                    elif idx._state == 'drop' and idx.indirect:
                        # We cannot drop it here, assume the constraint
                        # will go and cascade the index, too
                        ret = ''
                        if not dry_run:
                            idx.set_state('done')
                    elif idx._state == 'drop':
                        ret = 'DROP INDEX "%s"' % idx._name
                    else:
                        raise NotImplementedError('What is %s state for index "%s"?' % \
                                (idx._state, idx._name))
                    if not ret:
                        continue
                    if not dry_run:
                        idx.set_state('@' + idx._state)
                        self._state = '@' + self._state
                        self.indices._state = self._state
                        idx.last_epoch = epoch
                    # shortcut: only return this index, as a separate SQL command
                    return ret + ';', args
            
            if not ret_col:
                self._logger.debug( "Why can't we %s alter anything on %s %s? %r ", \
                    partial and 'partially' or '', self._name, epoch, \
                    [ [ "%s: %s %s %s" % (c._name, c._state, c.get_depends(partial=partial), c.last_epoch) for c in coll] \
                        for coll in (self.columns, self.indices, self.constraints)] )
                raise RuntimeError("Table %s marked as 'alter' but cannot proceed at epoch %d" % \
                        (self._name, epoch))
                return '', []
            
            ret += ',\n\t'.join(ret_col)
            
            ret += ';\n'
    
        elif self._state == 'drop':
            if drop_guard:
                self._logger.warning("Table %s would be dropped, but drop_guard saved it", self._name)
                self._state = 'skipped'
                return '', []
            ret = 'DROP TABLE %s;' % self._name
        else:
            self._logger.warning("Table(%s) pop_sql called on state %s", self._name, self._state)

        if not dry_run:
            self._state = '@' + self._state
            if do_columns:
                self.columns._state = self._state
            if do_constraints:
                self.constraints._state = self._state
            self.last_epoch = epoch
        return ret, args


class View(Relation):
    pass

    #def __init__(self, ...):
    #    depends_on[]

class Index(Relation):
    
    _wait_depends = False
    _elem_attrs = [] # We don't want to recurse into our 'columns'
    
    def __init__(self, name, colnames, state, is_unique=False, reloid=None, indisprimary=False):
        Relation.__init__(self, name=name)
        self.is_unique = False
        self.set_state(state)
        self.columns.set_state('done') # never mind
        for i,cn in enumerate(colnames):
            self.columns.append(Column(name=cn, ctype='', num=(i+1)))
        self.set_state(None)
        if is_unique:
            self.indirect = 'u'
        elif indisprimary:
            self.indirect = 'p'
        else:
            self.indirect = False

    def _to_create_sql(self, table_name, args):
        ret = 'CREATE INDEX "%s" ON "%s" ' % (self._name, table_name)
        # the simple case: non-decorated columns (so far)
        colnames = [ (c.num, c._name) for c in self.columns]
        colnames.sort(key=lambda cc: cc[0])
        ret += '(' + (', '.join([cc[1] for cc in colnames])) + ')'
        return ret

class TableConstraint(_element):

    _wait_depends = False
    def __repr__(self):
        return self._to_create_sql([])

    def _to_create_sql(self, args):
        raise NotImplementedError


class PlainUniqueConstraint(TableConstraint):
    """ unique constraint on multiple, straight, columns
    """
    def __init__(self, name, columns):
        TableConstraint.__init__(self, name)
        self.columns = columns

    def _to_create_sql(self, args):
        return '"%s" UNIQUE(%s)' % (self._name, ', '.join(self.columns))

class CheckConstraint(TableConstraint):
    def __init__(self, name, definition):
        _element.__init__(self, name)
        self.definition = definition

    def __repr__(self):
        ret = "%s %s" % (self._name, self.definition)
        return ret

class OtherTableConstraint(TableConstraint):
    def __init__(self, name, **kwargs): # TODO
        _element.__init__(self, name)
        self.__dict__.update(kwargs) #dirty
        
    def _to_create_sql(self, args):
        ret = '"%s" %s' % (self._name, self.definition)
        return ret

class Command(_element):
    """Represents various SQL commands that must be carried out in order

        In some cases we have to execute some queries before we can update
        the schema. See subclasses
    """

    def get_dry(self):
        """ return the string of the command to be executed
        """
        raise NotImplementedError

    def execute(self, cr):
        """ carry out the command, given the cursor
        """
        raise NotImplementedError

class SQLCommand(Command):
    """ Arbitrary SQL command
    """
    def __init__(self, sql, args=None, name=None, prepare_fn=None, debug=False):
        """
            @param prepare_fn If given, append results of calling this function
                to the arguments
        """
        assert sql, "Must have some SQL"
        if not name:
            #just invent a unique name
            name = 'sql-' + hex(id(self))[-8:]
        Command.__init__(self, name=name)
        self.sql = sql
        if args:
            self.args = tuple(args)
        else:
            self.args = ()
        self._prepare_fn = prepare_fn
        self._debug=debug

    def get_dry(self):
        return self.sql

    def __str__(self):
        return self.sql

    def __repr__(self):
        return "%s(%s)" % (self.__class__.__name__, self.sql)

    def execute(self, cr):
        if self._prepare_fn:
            self.args = self.args + self._prepare_fn(cr)
        cr.execute(self.sql, self.args, debug=self._debug)

#class SQLExistsCommand(Command):
#    """Executes SELECT command to see if records exist
#    """

#eof
