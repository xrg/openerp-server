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

#.apidoc title: Inherit field

""" Special field that appears in addons, but only causes OSV to modify
    existing fields.
"""

from fields import _column, register_any_classes
from fields_function import function
from fields_relational import one2many, many2many

class inherit(object):
    """ A special placeholder which tells the osv engine to override a field

    Note that this class is NOT a _column !

    With this class, we are able to override a field and change some of its
    properties through an _inherit model of ORM. the gain is that we don't
    need to redefine the whole field, but only the properties which we want
    to change.
    
    Example::
    
        In the base model, we could have
            'foo': fields.char('Foo', size=64, help='Some foo'),
        and in our extension addon, improve to
            'foo': fields.inherit(size=128),
    """

    def __init__(self, **kwargs):
        self.__kwargs = kwargs.copy()

    def _adapt(self, field):
        """ Manipulate field and alter it according to our kwargs
        """

        assert isinstance(field, _column), type(field)

        for kw, val in self.__kwargs.items():
            if kw == 'domain':
                field._domain = val
            elif kw == 'context':
                field._context = val
            elif kw == 'selection_extend':
                old_keys = set([v[0] for v in field.selection])
                for kv in val:
                    if kv[0] not in old_keys:
                        field.selection.append(kv)
            elif isinstance(field, function) \
                    and kw in ('old', 'method', 'fnct', 'fnct_inv', 'arg',
                                'multi', 'fnct_inv_arg', 'type', 'fnct_search'):
                # All these differ from params to attributes by an underscore
                setattr(field, '_'+kw, val)
            elif kw == 'limit' and isinstance(field, (one2many, many2many)):
                # We do NOT adapt the other attributes _obj, _id1, _id2 etc.
                # because you must think twice before hacking them.
                setattr(field, '_limit', val)
            else:
                setattr(field, kw, val)

register_any_classes(inherit)

# eof
