# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
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

from tools import reverse_enumerate
# import logging
from tools import expr_utils as eu

#.apidoc title: Domain Expressions

ExpressionError = eu.DomainMsgError

def _m2o_cmp(a, b):
    if a is False:
        if b:
            return -1
        return 0
    else:
        if not b:
            return 1
        elif isinstance(b, (int, long)):
            return cmp(a[0], b)
        elif isinstance(b, basestring):
            return cmp(a[1], b)
        else:
            # Arbitrary: unknown b is greater than all record values
            return -1

class expression(object):
    """ Parse a domain expression into objects and SQL syntax
    use a real polish notation
    leafs are still in a ('foo', '=', 'bar') format
    For more info: http://christophe-simonis-at-tiny.blogspot.com/2008/08/new-new-domain-notation.html
    """
    OPS = ('=', '!=', '<>', '<=', '<', '>', '>=', '=?', 
            '=like', '=ilike', 'like', 'not like', 'ilike', 'not ilike', 
            'in', 'not in',
            'child_of', '|child_of' )
    INTERNAL_OPS = OPS + ('inselect', 'not inselect')

    FALLBACK_OPS = {'=': lambda a, b: bool(a == b),
            '!=': lambda a, b: bool(a != b),
            '<>': lambda a, b: bool(a != b),
            '<=': lambda a, b: bool(a <= b),
            '<': lambda a, b: bool(a < b),
            '>': lambda a, b: bool(a > b),
            '>=': lambda a, b: bool(a >= b),
            '=?': lambda a, b: b is None or b is False or bool(a == b),
            #'=like': lambda a, b: , need regexp?
            #'=ilike': lambda a, b: ,
            'like': lambda a, b: bool(b in a),
            'not like': lambda a, b: bool(b not in a),
            'ilike': lambda a, b: (not b) or (a and bool(b.lower() in a.lower())),
            'not ilike': lambda a, b: b and ((not a) or bool(b.lower() not in a.lower())),
            'in': lambda a, b: bool(a in b),
            'not in': lambda a, b: bool(a not in b),
            }

    FALLBACK_OPS_M2O = {'=': lambda a, b: _m2o_cmp(a,b) == 0,
            '!=': _m2o_cmp ,
            '<>': _m2o_cmp,
            '<=': lambda a, b: _m2o_cmp(a,b) <= 0,
            '<': lambda a, b: _m2o_cmp(a, b) < 0,
            '>': lambda a, b: _m2o_cmp(a, b) > 0,
            '>=': lambda a, b: _m2o_cmp(a, b) >= 0,
            '=?': lambda a, b: b is None or b is False or _m2o_cmp(a, b) == 0,
            #'=like': lambda a, b: , need regexp?
            #'=ilike': lambda a, b: ,
            'like': lambda a, b: (not b) or (a and bool(b in a[1])),
            'not like': lambda a, b: b and ((not a) or (b not in a[1])),
            'ilike': lambda a, b: (not b) or (a and bool(b.lower() in a[1].lower())),
            'not ilike': lambda a, b: b and ((not a) or bool(b.lower() not in a[1].lower())),
            'in': lambda a, b: a and bool(a[1] in b),
            'not in': lambda a, b: (not a) or bool(a[1] not in b),
            }
    def _is_operator(self, element):
        return isinstance(element, (str, unicode)) and element in ('&', '|', '!')

    def _is_leaf_old(self, element, internal=False):
        return (isinstance(element, tuple) or isinstance(element, list)) \
           and len(element) == 3 \
           and (((not internal) and element[1] in self.OPS) \
                or (internal and element[1] in self.INTERNAL_OPS))

    def _is_leaf(self, element, internal=False):
        return isinstance(element, (list, tuple)) \
                and len(element) == 3 \
                and isinstance(element[1], basestring) \
                and (isinstance(element[0], basestring) or \
                        (isinstance(element[0], int) and isinstance(element[2], int)))

    def __init__(self, exp, mode=None, debug=False):
        """  Initialize an expression to be evaluated on the object storage
        
            Expression may behave differently according to cr.pgmode:
            with 'old', 'sql' the db will execute the expression.
            At pgsql, pg84, pg90, sub-queries are allowed. At pg84, pg90+,
            recursive ones are used for 'child_of' expressions
        """
        # check if the expression is valid
        if not reduce(lambda acc, val: acc and (self._is_operator(val) or self._is_leaf(val)), exp, True):
            raise ExpressionError('Bad domain expression: %r' % (exp,))
        self.__exp = exp
        self.__field_tables = {}  # used to store the table to use for the sql generation. key = index of the leaf
        self.__all_tables = set()
        self.__joins = []
        self.__main_table = None # 'root' table. set by parse()
        self.__DUMMY_LEAF = (1, '=', 1) # a dummy leaf that must not be parsed or sql generated
        assert not mode, mode # obsolete
        self.__mode = mode
        self._debug = debug

    @property
    def exp(self):
        return self.__exp[:]

    def _rec_get(self, cr, uid, model, ids, parent=None, left='id', prefix='', \
                null_too=False, context=None):
            """ Compute the sub-expression for a recursive field's operator, 
                typically 'child_of'
                
                @param model the model to operate upon
                @param ids the ids of that model, to start from
                @param parent the name of the "parent" field
                @param left
                @param prefix if we must disambiguate the table name, 
                        including the dot
                @param null_too if specified, the expression would also stand 
                        for left = NULL
            """
            if model._parent_store and (not model.pool._init): #and False:
                # TODO: Improve where joins are implemented for many with '.', replace by:
                # doms += ['&',(prefix+'.parent_left','<',o.parent_right),(prefix+'.parent_left','>=',o.parent_left)]
                doms = []
                for o in model.browse(cr, uid, ids, context=context):
                    if doms:
                        doms.insert(0, '|')
                    doms += ['&', ('parent_left', '<', o.parent_right), ('parent_left', '>=', o.parent_left)]
                if prefix:
                    if null_too:
                        return ['|', (left, '=', False), (left, 'in', model.search(cr, uid, doms, context=context))]
                    return [(left, 'in', model.search(cr, uid, doms, context=context))]
                if null_too:
                    doms = ['|', (left, '=', False)] + doms
                return doms
            elif self.__mode in eu.PG84_MODES:
                # print "Recursive expand for 8.4, for %s" % model._table
                phname = prefix + model._table
                phname = phname.replace('.', '_')
                phexpr = '%s_rsrch.id' % phname
                
                ttables = ['"%s"' % model._table]
                dqry = model._where_calc(cr, uid, 
                        [(parent or model._parent_name, '=', eu.placeholder(phname, phexpr) )], context)
                qu1, qu2, qtables = dqry.where_clause, dqry.where_clause_params, dqry.tables
                if dqry.joins:
                    raise NotImplementedError  # FIXME

                d1, d2, dtables = model.pool.get('ir.rule').domain_get(cr, uid, model._name)
                if d1:
                    if isinstance(d1,list):
                        qu1 += d1
                    else:
                        qu1.append(d1)
                    qu2 += d2
                    
                    for dt in dtables:
                        if dt not in ttables:
                            ttables.append(dt)
                
                ttables2 = ', '.join(ttables)
                qu2 = [ ids, ] + qu2
                qry = ''' 
        WITH RECURSIVE %s_rsrch(id) AS (
                SELECT id FROM "%s" WHERE id = ANY(%%s)
                UNION ALL SELECT "%s".id FROM %s, %s_rsrch WHERE %s )
        SELECT id FROM %s_rsrch
                ''' %( phname, 
                        model._table,
                        model._table, ttables2, phname, ' AND '.join(qu1),
                        phname)
                
                # print "INSELECT %s" % qry
                # print "args:", qu2
                if null_too:
                    return ['|', (left, '=', False), (left, 'inselect', (qry, qu2))]
                return [(left, 'inselect', (qry, qu2))]
            # elif self.__mode == 'pgsql':
            #  any way  to do that in pg8.3?
            else:
                def rg(ids, model, parent):
                    if not ids:
                        return []
                    ids2 = model.search(cr, uid, [(parent, 'in', ids)], context=context)
                    return ids + rg(ids2, model, parent)
                if null_too:
                    res = ['|', (left, '=', False)]
                else:
                    res = []
                res += [(left, 'in', rg(ids, model, parent or model._parent_name))]
                return res

    def parse(self, cr, uid, table, context):
        """ transform the leafs of the expression """
        if not self.__exp:
            return self


        self.__main_table = table
        self.__all_tables.add(table)

        i = -1
        while i + 1<len(self.__exp):
            i += 1
            e = self.__exp[i]
            if self._is_operator(e) or e == self.__DUMMY_LEAF:
                continue
            left, operator, right = e
            operator = operator.lower()
            working_table = table
            main_table = table
            fargs = left.split('.', 1)
            if fargs[0] in table._inherit_fields:
                while True:
                    field = main_table._columns.get(fargs[0], False)
                    if field:
                        working_table = main_table
                        self.__field_tables[i] = working_table
                        break
                    working_table = main_table.pool.get(main_table._inherit_fields[fargs[0]][0])
                    if working_table not in self.__all_tables:
                        self.__joins.append('%s.%s=%s.%s' % (working_table._table, 'id', main_table._table, main_table._inherits[working_table._name]))
                        self.__all_tables.add(working_table)
                    main_table = working_table

            field = working_table._columns.get(fargs[0], False)
            if not field:
                if left == 'id' and (operator == 'child_of' or operator == '|child_of'):
                    dom = self._rec_get(cr, uid, working_table, right, null_too=(operator == '|child_of'), context=context)
                    self.__exp = self.__exp[:i] + dom + self.__exp[i+1:]
                continue

            nex = field.expr_eval(cr, uid, working_table, fargs, operator, right,
                                self, context=context)
            if nex is None:
                continue
            elif isinstance(nex, eu.sub_expr):
                continue
            elif nex is True:
                self.__exp[i] = self.__DUMMY_LEAF #TODO
            elif nex is False:
                self.__exp[i] = ('id', '=', 0) # TODO
            elif isinstance(nex, tuple) and len(nex) == 3:
                self.__exp[i] = nex
            elif isinstance(nex, list):
                if len(nex) == 0:
                    self.__exp[i] = self.__DUMMY_LEAF
                elif len(nex) == 1 and isinstance(nex[0], tuple) and len(nex[0]) == 3:
                    self.__exp[i] = nex[0]
                else:
                    raise NotImplementedError("Sorry Dave, can't insert %r" % nex)
            else:
                raise NotImplementedError("Sorry Dave, can't handle %r" % nex)

        return self

    def __leaf_to_sql(self, leaf, table):
        if leaf == self.__DUMMY_LEAF:
            return ('(1=1)', []) # true
        left, operator, right = leaf

        if operator == 'inselect':
            query = '(%s.%s in (%s))' % (table._table, left, right[0])
            params = right[1]
        elif operator == 'not inselect':
            query = '(%s.%s not in (%s))' % (table._table, left, right[0])
            params = right[1]
        elif operator in ['in', 'not in']:
            params = right and right[:] or []
            len_before = len(params)
            for i in range(len_before)[::-1]:
                if params[i] == False:
                    del params[i]

            len_after = len(params)
            check_nulls = len_after != len_before
            query = '(1=0)' # false

            if len_after:
                if left == 'id':
                    instr = ','.join(['%s'] * len_after)
                else:
                    instr = ','.join([table._columns[left]._symbol_set[0]] * len_after)
                query = '(%s.%s %s (%s))' % (table._table, left, operator, instr)
            else:
                # the case for [field, 'in', []] or [left, 'not in', []]
                if operator == 'in':
                    query = '(%s.%s IS NULL)' % (table._table, left)
                else:
                    query = '(%s.%s IS NOT NULL)' % (table._table, left)
            if check_nulls:
                query = '(%s OR %s.%s IS NULL)' % (query, table._table, left)
        else:
            params = []

            if right == False and (leaf[0] in table._columns)  and table._columns[leaf[0]]._type=="boolean"  and (operator == '='):
                query = '(%s.%s IS NULL or %s.%s = false )' % (table._table, left,table._table, left)
            elif (((right == False) and (type(right)==bool)) or (right is None)) and (operator == '='):
                query = '%s.%s IS NULL ' % (table._table, left)
            elif right == False and (leaf[0] in table._columns)  and table._columns[leaf[0]]._type=="boolean"  and (operator in ['<>', '!=']):
                query = '(%s.%s IS NOT NULL and %s.%s != false)' % (table._table, left,table._table, left)
            elif (((right == False) and (type(right)==bool)) or right is None) and (operator in ['<>', '!=']):
                query = '%s.%s IS NOT NULL' % (table._table, left)
            elif (operator == '=?'):
                op = '='
                import orm
                if (right is False or right is None or isinstance(right, orm.browse_null)):
                    return ( 'TRUE',[])
                if left in table._columns:
                        format = table._columns[left]._symbol_set[0]
                        query = '(%s.%s %s %s)' % (table._table, left, op, format)
                        params = table._columns[left]._symbol_set[1](right)
                else:
                        query = "(%s.%s %s %%s)" % (table._table, left, op)
                        params = right

            elif (operator == 'child_of' or operator == '|child_of'):
                raise ExpressionError("Cannot compute %s %s %s in sql" %(left, operator, right))
            else:
                if isinstance(right, eu.placeholder):
                    assert(right.expr)
                    query = '( %s.%s %s %s)' % (table._table, left, operator, right.expr)
                elif left == 'id':
                    query = '%s.id %s %%s' % (table._table, operator)
                    params = right
                else:
                    like = operator in ('like', 'ilike', 'not like', 'not ilike')

                    op = {'=like':'like','=ilike':'ilike'}.get(operator,operator)
                    if left in table._columns:
                        format = like and '%s' or table._columns[left]._symbol_set[0]
                        query = '(%s.%s %s %s)' % (table._table, left, op, format)
                    else:
                        query = "(%s.%s %s '%s')" % (table._table, left, op, right)

                    add_null = False
                    if like:
                        if isinstance(right, str):
                            str_utf8 = right
                        elif isinstance(right, unicode):
                            str_utf8 = right.encode('utf-8')
                        else:
                            str_utf8 = str(right)
                        params = '%%%s%%' % str_utf8
                        add_null = not str_utf8
                    elif left in table._columns:
                        params = table._columns[left]._symbol_set[1](right)

                    if add_null:
                        query = '(%s OR %s IS NULL)' % (query, left)

        if isinstance(params, basestring):
            params = [params]
        return (query, params)


    def to_sql(self):
        stack = []
        params = []
        try:
            for i, e in reverse_enumerate(self.__exp):
                if self._is_leaf(e, internal=True):
                    table = self.__field_tables.get(i, self.__main_table)
                    q, p = self.__leaf_to_sql(e, table)
                    if isinstance(p, (list, tuple)):
                        params = list(p) + params
                    else:
                        params.insert(0, p)
                    stack.append(q)
                else:
                    if e == '!':
                        stack.append('(NOT (%s))' % (stack.pop(),))
                    else:
                        ops = {'&': ' AND ', '|': ' OR '}
                        q1 = stack.pop()
                        q2 = stack.pop()
                        stack.append('(%s %s %s)' % (q1, ops[e], q2,))
    
            query = ' AND '.join(reversed(stack))
            joins = ' AND '.join(self.__joins)
            if joins:
                query = '(%s) AND (%s)' % (joins, query)
        except IndexError, er:
            raise IndexError( "%s at %s. Expression: %s" %(er, self.__main_table._name, self.__exp))
        return (query, params)

    def get_tables(self):
        return ['"%s"' % t._table for t in self.__all_tables]

def or_join(list1, list2):
        """ Produce an expression that will evaluate to list1 OR list2
        
        This is non, trivial, since the reverse Polish notation will depend
        on the length of list1, list2
        
        The order of elements is *strictly* preserved, since there will
        be parameters to these expressions, parallel to this function.
        """
        
        def op_explicit(dom):
            """Helper, add explicit and operators to elements of dom """
            stack = []
            assert isinstance(dom, list), type(dom)
            in_stack = 0
            for d in reversed(dom):
                if d == '!':
                    stack.append(d)
                    # no in_stack increment
                elif (d == '&') or (d == '|'):
                    assert (in_stack >= 2), (in_stack, stack)
                    in_stack -= 1
                    stack.append(d)
                else:
                    assert isinstance(d, (list, tuple)), d
                    stack.append(d)
                    in_stack += 1
        
            while in_stack >= 2:
                stack.append('&')
                in_stack -= 1

            return reversed(stack)
        
        if not len(list1):
            return list2
        if not len(list2):
            return list1
        
        res = ['|']
        for r in op_explicit(list1):
            res.append(r)
        for r in op_explicit(list2):
            res.append(r)
        return res

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

