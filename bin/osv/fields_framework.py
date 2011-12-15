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

register_field_classes(id_field, vptr_field)

#eof
