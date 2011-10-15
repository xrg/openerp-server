# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2011 P. Christeas <xrg@hellug.gr>
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

from tools import sql_model

#.apidoc title: ORM Index classes

# not to be confused with index.html :P

""" Like fields, Index objects define indices that must be created on
    the model's tables
"""

class _baseindex(object):

    def _auto_init_sql(self, name, obj, schema_table, context=None):
        """Initialize and update the schema with this index

            Must be implemented by subclasses
        """
        raise NotImplementedError

class plain(_baseindex):
    """ Plain, multi-column index
    """
    sql_model_klass = sql_model.Index
    def __init__(self, *colnames):
        """Just state the columns names like::

            _indices = {
                index.plain('foo', 'bar', 'spam'),
            }
        """
        self.colnames = colnames

    def _auto_init_sql(self, name, obj, schema_table, context=None):
        idxname = '%s_%s' % (obj._table, name)
        if idxname in schema_table.indices:
            schema_table.indices[idxname].mark()
        else:
            idx = schema_table.indices.append(self.sql_model_klass(name=idxname, colnames=self.colnames, state=sql_model.CREATE))

            needs_table = False
            for c in self.colnames:
                if c in schema_table.columns:
                    idx.set_depends(schema_table.columns[c])
                else:
                    needs_table = True
            if needs_table:
                idx.set_depends(schema_table, on_alter=True)
        return None

class ihash(plain):
    """Using a hash table
    """
    sql_model_klass = sql_model.HashIndex

class unique(plain):
    """Unique-constraint index
    """
    sql_model_klass = sql_model.UniqueIndex
#eof