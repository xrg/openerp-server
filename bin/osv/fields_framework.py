# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP-f3, Open Source Management Solution
#    Copyright (C) 2008-2011 P. Christeas <xrg@hellug.gr>
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

#.apidoc title: Framework fields

""" Framework fields are implicit fields that are used by the ORM framework
    to convey special data. User can `not` directly manipulate the contents
    of these fields.
"""
from fields import _column, register_field_classes
from fields import integer
#import tools
#from tools.translate import _
from tools import expr_utils as eu

class id_field(integer):
    """ special properties for the 'id' field
    """
    
    def expr_eval(self, cr, uid, obj, lefts, operator, right, pexpr, context):

        if operator == 'child_of' or operator == '|child_of':
            dom = pexpr._rec_get(cr, uid, obj, right, null_too=(operator == '|child_of'), context=context)
            if len(dom) == 0:
                return True
            elif len(dom) == 1:
                return dom[0]
            else:
                return eu.nested_expr(dom)
        else:
            # Copy-paste logic from super, save on the function call
            assert len(lefts) == 1, lefts
            if right is False:
                if operator not in ('=', '!=', '<>'):
                    raise eu.DomainInvalidOperator(obj, lefts, operator, right)
                return (lefts[0], operator, None)
            return None # as-is


class vptr_field(_column):
    """Pseydo-field for the implicit _vptr column
    
        This will expose some helper functions that allow smart operations on
        the _vptr.
    """
    pass

    def _auto_init_sql(self, name, obj, schema_table, context=None):
        return None

class vptr_name(_column):
    """ Exposes the human readable name of the associated class

        This is a pseydo-function field with implied functionality.
        It will search `ir.model` and yield the corresponding model names,
        for the records of "our" model requested.
    """
    _classic_read = False
    _classic_write = False
    _prefetch = False
    _type = 'char'
    _properties = True

    def __init__(self, string='Class'):
        _column.__init__(self, string=string, readonly=True)

    def get(self, cr, obj, ids, name, user=None, context=None, values=None):
        from orm import browse_record_list, only_ids
        if not obj._vtable:
            return dict.fromkeys(only_ids(ids), False)

        res_ids = {}
        vptrs_resolve = {}
        if isinstance(ids, browse_record_list):
            for bro in ids:
                res_ids[bro.id] = bro._vptr
                vptrs_resolve[bro._vptr] = False
        else:
            # clasic read:
            for ores in obj.read(cr, user, ids, fields=['_vptr'], context=context):
                res_ids[ores['id']] = ores['_vptr']
                vptrs_resolve[ores['_vptr']] = False

        for mres in obj.pool.get('ir.model').search_read(cr, user, \
                [('model', 'in', vptrs_resolve.keys())], \
                fields=['model', 'name'], context=context):
            # context is important, we want mres to be translated
            vptrs_resolve[mres['model']] = mres['name']

        res = {}
        for rid, rmod in res_ids.items():
            res[rid] = vptrs_resolve.get(rmod, False)

        return res

        #if name == '_vptr.name':
        #    # retrieve the model name, translated
        #    pass

    def _auto_init_sql(self, name, obj, schema_table, context=None):
        return None

register_field_classes(id_field, vptr_field, vptr_name)

#eof
