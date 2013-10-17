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

from tools.translate import _
import logging

PG_MODES = ('pg93', 'pg92', 'pg91', 'pg90', 'pg84', 'pgsql')
PG84_MODES = ('pg93', 'pg92', 'pg91', 'pg90', 'pg84')
PG90_MODES = ('pg93', 'pg92', 'pg91', 'pg90')
# PG93+ modes?

class DomainError(ValueError):
    """ An exception regarding an ORM domain expression
    """
    def get_msg(self, cr, uid, context=None):
        """ Get the message, possibly translating it
        """
        raise NotImplementedError(repr(self))

    def get_title(self, cr, uid, context=None):
        return _("Domain Error")

class DomainMsgError(DomainError):
    """Domain Error with specific message

        Message should be translated already
    """

    def __init__(self, msg):
        self.msg = msg

    def get_msg(self, cr, uid, context=None):
        return self.msg

    def __str__(self):
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
        if isinstance(model, basestring):
            self._model = model
        else:
            self._model = model._name
        self._lefts = lefts
        self._operator = operator
        self._right = right

    def __str__(self):
        return "%s: model %s (%s, '%s', %r)" % (self.__class__.__name__, self._model, \
                '.'.join(self._lefts or []), self._operator, self._right)

    def get_title(self, cr, uid, context=None):
        return _("Domain expression Error")

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
    def to_sql(self, parent, model, field):
        """ Return the sql expression
            @param parent the calling expression() object
            @param model the current ORM model for this node
            @param field the field of that node
            @return expr, params[]
        """
        raise NotImplementedError

class dirty_expr(list):
    """ Placeholder for expressions that need to be parsed again
    """
    pass

class nested_expr(sub_expr):
    """ A nested, Polish, expression

        This is not allowed to contain but pre-parsed elements.
    """
    def __init__(self, dom):
        self._dom = dom

    def to_sql(self, parent, model, field):

        if self._dom == []:
            return 'TRUE', []

        run_expr = [] #: string fragments to glue together into where_clause
        run_params = []
        stack = [] #: operand stack for Polish -> algebra notation
        for exp in self._dom:
            if run_expr and not stack:
                run_expr.append(' AND ')
            if exp == '&':
                run_expr.append('(')
                stack += [')', ' AND ']
                continue
            elif exp == '|':
                run_expr.append('(')
                stack += [')', ' OR ']
                continue
            elif exp == '!':
                run_expr.append('NOT (')
                stack += [')',]
                continue
            elif exp is True or exp == (1, '=', 1):
                run_expr.append('TRUE')
            elif exp is False:
                run_expr.append('FALSE')
            elif isinstance(exp, sub_expr): # more than one components
                e, p = exp.to_sql(parent, model, field)
                assert e, exp
                run_expr.append(e)
                run_params.extend(p)
            else:
                e, p = parent._leaf_to_sql(exp, model, field)
                run_expr.append(e)
                run_params.extend(p)

            while stack:
                p = stack.pop()
                run_expr.append(p)
                # continue closing parentheses
                if p != ')':
                    break

        if stack:
            raise DomainMsgError(_("Invalid domain expression, too many operators: %r") %\
                    self._dom)

        return ''.join(run_expr), run_params

class function_expr(sub_expr):
    """ SQL-function expression

        This will call arbitrary SQL functions against operator + right
    """
    def __init__(self, func, left=False, operator=None, right=None, params=None):
        """
            @param left the "left" column in the model table. It will be
                decorated with the table name
            @param operator outside the function
            @param right Expression value right of the operator. It will
                NOT be passed through "symbol_set"

            The result will be like:

                "func(%s) <operator> <right>" % (_table.left)
        """
        self.func = func
        self.left = left
        self.operator = operator
        self.right = right
        self.params = params or []

    def to_sql(self, parent, model, field):
        expr = self.func
        params = self.params
        if self.left:
            expr = expr % ('"%s".%s' % (model._table, self.left))
        if self.operator:
            expr = "%s %s %%s" %(expr, self.operator)
            params = params + [self.right,]
        return expr, params

class placeholder(object):
    """ A dummy string, that will substitute the ids array in
        recursive queries.
        Since this is not a string, nor int, it won't be substituted
        in expression parsing.
    """
    __slots__ = ('name', 'expr')

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