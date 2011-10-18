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


#.apidoc title: Helper classes for expressions

""" These are classes that are needed for fields and/or ORM when feeding
    expressions to expression.py. The reason they are in a separate file
    is the order of importing. Having them in 'expression.py' would
    introduce a circular dependency.
"""

from tools.translate import _ # must be loaded late

PG_MODES = ('pg92', 'pg91', 'pg90', 'pg84', 'pgsql')
PG84_MODES = ('pg92', 'pg91', 'pg90', 'pg84')
PG90_MODES = ('pg92', 'pg91', 'pg90')

class DomainError(ValueError):
    """ An exception regarding an ORM domain expression
    """
    def get_msg(self, cr, uid, context=None):
        """ Get the message, possibly translating it
        """
        raise NotImplementedError(repr(self))

class DomainMsgError(DomainError):
    """Domain Error with specific message
    
        Message should be translated already
    """
    
    def __init__(self, msg):
        self.msg = msg

    def get_msg(self, cr, uid, context=None):
        return self.msg

class DomainExpressionError(DomainError):
    """ A parsing error in the domain expression (baseclass)
    
        You can ommit the right part in several cases
        
        These are predefined errors that have stock messages
    """
    def __init__(self, model, lefts, operator, right=None):
        """
            @param string name of the ORM model involved
        """
        self._model = model
        self._lefts = lefts
        self._operator = operator
        self._right = right

    def get_msg(self, cr, uid, context=None):
        return  _("The following domain expression is invalid for %s model: (%s, %r, %r)") % \
            (self._model, '.'.join(self._lefts), self._operator, self._right)

class DomainLeftError(DomainExpressionError):
    """ Means that the left part is not valid
    """
    def get_msg(self, cr, uid, context=None):
        return  _("Field %r is not valid in domains of %s") % \
            ('.'.join(self._lefts), self._model)

class DomainInvalidOperator(DomainExpressionError):
    """Meaning that this operator is not valid for that field
    """
    
    def get_msg(self, cr, uid, context=None):
        return  _("Field %s.\"%s\" cannot use the %r operator") % \
            (self._model, '.'.join(self._lefts), self._operator)

class DomainSubLeftError(DomainInvalidOperator):
    """ Meaning that the field type doesn't have that sub-operator
    """
    def __init__(self, model, lefts, sub_left):
        DomainInvalidOperator.__init__(self, model, lefts, operator='')
        self._sub_left = sub_left

    def get_msg(self, cr, uid, context=None):
        return  _("Field %s.\"%s\" cannot evaluate %r subfield") % \
            (self._model, '.'.join(self._lefts), self._sub_left)

class DomainRightError(DomainExpressionError):
    """ Meaning that that right expression is not recognizable
    """
    def get_msg(self, cr, uid, context=None):
        return  _("The following value is invalid for operator %r of %s: %r") % \
            (self._operator, self._lefts[-1], self._right)


class sub_expr(object):
    """ Sub-expression component
        Any of these, in the domain expression, should be legal.
    """
    pass

class placeholder(object):
    """ A dummy string, that will substitute the ids array in 
        recursive queries.
        Since this is not a string, nor int, it won't be substituted
        in expression parsing.
    """
    def __init__(self, name, expr = None):
        self.name = name
        self.expr = expr

    def __str__(self):
        return "<placeholder %s>" % self.name

    def __eq__(self, stgr):
        if isinstance(stgr, basestring) and stgr == self.name:
            return True
        return False

#eof