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

def load_from_db(cr, relnames, _debug=False):
    """ Load the relations from the database into element structures
        
        @return dict (tables, indices, views, ...), each of them being
            a collection of elements
    """
    ret = collection('sql',collection)
    ret.append(collection('tables', Table))

    if True:
        # Load tables from db
        cr.execute("""SELECT c.relname, c.oid, c.relkind
            FROM pg_class c
            WHERE c.relname = ANY(%s) AND relkind IN ('r','v')
                  AND c.relnamespace IN (SELECT oid from pg_namespace 
                        WHERE nspname = ANY(current_schemas(false)))""",
                (relnames,), debug=_debug)

        tbl_reloids = {} # map oid: relname
        for r in cr.dictfetchall():
            if r['relkind'] == 'r':
                ret['tables'].append(Table(r['relname'], r['oid']))
                tbl_reloids[r['oid']] = r['relname']
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
                      (tbl_reloids.keys(),), debug=_debug)
            for c in cr.dictfetchall():
                tbl = ret['tables'][tbl_reloids[c['reloid']]]
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
                      (tbl_reloids.keys(),), debug=_debug)
            for idx in cr.dictfetchall():
                tbl = ret['tables'][tbl_reloids[idx['reloid']]]
                idx = idx.copy() # we want it dict
                idx_cols = map(tbl.find_colname, idx.pop('indkey').split(' '))
                if idx['indisprimary'] and (len(idx_cols) == 1) and idx_cols[0]:
                    tbl.columns[idx_cols[0]].primary_key = True
                else:
                    tbl.indices.append(Index(colnames=idx_cols, **idx.copy()))
        
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
                    (tbl_reloids.keys(), ), debug=_debug)
            for con in cr.dictfetchall():
                tbl = ret['tables'][tbl_reloids[con['reloid']]]
                con = con.copy()
                con_cols = map(tbl.find_colname, con.pop('conkey'))
                if len(con_cols) == 1: # most usual, column constraint
                    if con['contype'] == 'p':
                        # primary key constraint, must already be set by indexes
                        assert tbl.columns[con_cols[0]].primary_key
                        continue
                    elif True and con['contype'] == 'u':
                        # unique constraint
                        # Not supported yet on column level
                        tbl.columns[con_cols[0]].constraints.append(UniqueColumnConstraint(con['name']))
                        continue
                    elif con['contype'] == 'f':
                        # foreign key constraint
                        assert len(con['fc_colnames']) == 1, con['fc_colnames']
                        tbl.columns[con_cols[0]].constraints.append( \
                                FkColumnConstraint(con['name'], fcname=con['fcname'],
                                                fc_colname=con['fc_colnames'][0]))
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
                
    return ret


def pretty_print(elem, indent=0):
    """ Recursively print the collection
        @return a multi-line string
    """
    ret = (' ' * indent) + repr(elem)
    if isinstance(elem, collection):
        if indent > 80:
            return ret
        ret += '{'
        for e2 in elem:
            ret += '\n' + pretty_print(e2, indent+4)
        
        ret += '\n' +(' ' * indent) + '}'
    elif isinstance(elem, Relation):
        if indent > 80:
            return ret
        ret += '{'
        for e2 in elem.columns:
            ret += '\n' + pretty_print(e2, indent+4)

        if isinstance(elem, Table):
            ret += '\n'
            for e2 in elem.indices:
                ret += '\n' + pretty_print(e2, indent+4)
            
            if len(elem.constraints):
                for e2 in elem.constraints:
                    ret += '\n' + pretty_print(e2, indent+4)

        ret += '\n' +(' ' * indent) + '}'
    
    return ret

class _element(object):
    """ mostly for tracing
    """
    
    def __init__(self, name):
        self._name = name

    def __repr__(self):
        return "%s(%s)" % (self.__class__.__name__, self._name)

class collection(_element):
    """ hybrid dict/unordered list
    
        No setitem, use the append()
    """
    
    def __init__(self, name, klass):
        """
            @param klass all elements of this collection should be instances
                    of that class
        """
        _element.__init__(self, name)
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
        self._d[elem._name] = elem
    

class Column(_element):
    def __init__(self, name, ctype, num=None, size=None, has_def=None,
                not_null=None, default=None, constraint=None):
        _element.__init__(self, name)
        self.num = num # used as an index for other references
        self.ctype = ctype
        self.size = size
        self.has_def = has_def
        self.default = default
        self.primary_key = False
        self.constraints = collection('costraints', ColumnConstraint)
        self.not_null = not_null
        if constraint:
            self.constraints.append(constraint)

    def __repr__(self):
        ret = "%s %s" % (self._name, self.ctype)
        if self.ctype in ('char', 'varchar'):
            ret += '(%d)' % self.size
        if self.has_def:
            if self.default is None:
                ret += " default null"
            else:
                ret += " default %s" % self.default

        # These are constraints, go after the 'default'
        if self.not_null:
            ret += ' NOT NULL'
        if self.primary_key:
            ret += ' PRIMARY KEY'
        for c in self.constraints:
            # omit their names
            ret += ' ' + repr(c)
        return ret
        
class Relation(_element):
    """
        @attr name
        @attr kind
    """
    def __init__(self, name, oid=None):
        self._oid = oid
        self._name = name
        #state = 'create' | 'ok' | 'update' | 'drop'
        #indices (table)
        self.columns = collection('columns', Column)
        #constraints (table)
        #comment

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

    def add_column():
        pass

POSTGRES_CONFDELTYPES = {
    'r': 'RESTRICT',
    'a': 'NO ACTION',
    'c': 'CASCADE',
    'n': 'SET NULL',
    'd': 'SET DEFAULT',
}

class ColumnConstraint(_element):
    """Single-column constraint
    """
    pass

class UniqueColumnConstraint(ColumnConstraint):
    def __repr__(self):
        return 'unique'

class CheckColumnConstraint(ColumnConstraint):
    def __init__(self, name, definition):
        _element.__init__(self, name)
        self.definition = definition

    def __repr__(self):
        # is it?
        return self.definition

class FkColumnConstraint(ColumnConstraint):
    """A single-column foreign-key constraint
        Stores the necessary info for the REFERENCES clause
    """
    def __init__(self, name, fcname, fc_colname, on_update=None, on_delete=None):
        _element.__init__(self, name)
        self.fcname = fcname
        self.fc_colname = fc_colname
        self.on_update = POSTGRES_CONFDELTYPES.get(on_update)
        self.on_delete = POSTGRES_CONFDELTYPES.get(on_delete)

    def __repr__(self):
        ret = 'REFERENCES %s(%s)' % (self.fcname, self.fc_colname)
        if self.on_update:
            ret += ' ON UPDATE %s' % self.on_update
        if self.on_delete:
            ret += ' ON DELETE %s' % self.on_delete
        return ret

class Table(Relation):
    def __init__(self, name, oid=None):
        Relation.__init__(self, name=name, oid=oid)
        self.indices = collection('indices', Index)
        self.constraints = collection('constraints', TableConstraint)

class View(Relation):
    pass

    #def __init__(self, ...):
    #    depends_on[]

class Index(Relation):
    
    def __init__(self, name, colnames, is_unique=False, reloid=None, indisprimary=False):
        Relation.__init__(self, name=name)
        self.is_unique = False
        for i,cn in enumerate(colnames):
            self.columns.append(Column(name=cn, ctype='', num=(i+1)))
    # r = ordinary table, i = index, S = sequence, v = view, c = composite type, t = TOAST table, f = foreign table

class TableConstraint(_element):
    pass

class PlainUniqueConstraint(TableConstraint):
    """ unique constraint on multiple, straight, columns
    """
    def __init__(self, name, columns):
        TableConstraint.__init__(self, name)
        self.columns = columns

    def __repr__(self):
        return '%s unique(%s)' % (self._name, ', '.join(self.columns))

class CheckConstraint(ColumnConstraint):
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
        
    def __repr__(self):
        ret = "%s %s" % (self._name, self.definition)
        return ret

#eof
