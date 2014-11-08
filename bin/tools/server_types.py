# -*- encoding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2011 P. Christeas <xrg@hellug.gr>
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Lesser General Public License as published
#    by the Free Software Foundation, either version 3 of the License, or
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


#.apidoc title: Server types

""" A collection of pseydo-types that only live within the server

    These are explicitly NOT communicated to any RPC I/O, so that we can
    trust their values to originate only from the server side
"""

class _server_modifier:
    @classmethod
    def equals(cls, left, right):
        """Assert the equality of left == right, where left MUST be server-side
        """
        if not isinstance(left, cls):
            raise TypeError("Left side is not %s" % cls.__name__)
        return left == right

    @classmethod
    def check(cls, left):
        if not isinstance(left, cls):
            raise TypeError("Left side is not %s" % cls.__name__)
        return True

    @classmethod
    def in_context(cls, context, name, value=None, raise_error=False):
        """See if dict `context` contains a server type `name` with that value

            @param context a dictionary or None/False
            @param name typically a string, key to search in context
            @param value if set, value to check against
            @param raise_error will raise TypeError if context contains `name`
                but is not the right server type

            If `context` is empty, None will be returned
            If `value` is not set this function will return `context[name]`

        """

        if not context:
            return None
        if name not in context:
            return None

        if not isinstance(context[name], cls):
            if raise_error:
                raise TypeError("context[%s] is not a %s" % (name, cls.__name__))
            else:
                return None

        if value is None:
            return context[name]
        else:
            return context[name] == value

class server_bool(_server_modifier):
    def __init__(self, val):
        self.__val = bool(val)

    def __getattr__(self, name):
        return getattr(self.__val, name)

    def __eq__(self, other):
        if isinstance(other, server_bool):
            other = other.__val
        return self.__val == other

    def __ne__(self, other):
        if isinstance(other, server_bool):
            other = other.__val
        return self.__val != other

class server_int(_server_modifier, int):
    pass

class server_str(_server_modifier, str):
    pass

class server_unicode(_server_modifier, unicode):
    pass

class server_dict(_server_modifier, dict):
    pass

class server_list(_server_modifier, list):
    pass

#eof
