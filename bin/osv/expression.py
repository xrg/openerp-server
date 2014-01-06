#!/usr/bin/env python
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

from tools import flatten, reverse_enumerate
import fields


class expression(object):
    """
    parse a domain expression
    use a real polish notation
    leafs are still in a ('foo', '=', 'bar') format
    For more info: http://christophe-simonis-at-tiny.blogspot.com/2008/08/new-new-domain-notation.html
    """

    def _is_operator(self, element):
        return isinstance(element, (str, unicode)) and element in ['&', '|', '!']

    def _is_leaf(self, element, internal=False):
        OPS = ('=', '!=', '<>', '<=', '<', '>', '>=', '=?', '=like', '=ilike', 'like', 'not like', 'ilike', 'not ilike', 'in', 'not in', 'child_of')
        INTERNAL_OPS = OPS + ('inselect', 'not inselect')
        return (isinstance(element, tuple) or isinstance(element, list)) \
           and len(element) == 3 \
           and (((not internal) and element[1] in OPS) \
                or (internal and element[1] in INTERNAL_OPS))

    def __execute_recursive_in(self, cr, s, f, w, ids, op, type):
        # todo: merge into parent query as sub-query
        res = []
        if ids:
            if op in ['<','>','>=','<=']:
                cr.execute('SELECT "%s"'    \
                               '  FROM "%s"'    \
                               ' WHERE "%s" %s %%s' % (s, f, w, op), (ids[0],))
                res.extend([r[0] for r in cr.fetchall()])
            else:
                for i in range(0, len(ids), cr.IN_MAX):
                    subids = ids[i:i+cr.IN_MAX]
                    cr.execute('SELECT "%s"'    \
                               '  FROM "%s"'    \
                               '  WHERE "%s" IN %%s' % (s, f, w),(tuple(subids),))
                    res.extend([r[0] for r in cr.fetchall()])
        else:
            cr.execute('SELECT distinct("%s")'    \
                           '  FROM "%s" where "%s" is not null'  % (s, f, s)),
            res.extend([r[0] for r in cr.fetchall()])
        return res

    def __init__(self, exp):
        # check if the expression is valid
        if not reduce(lambda acc, val: acc and (self._is_operator(val) or self._is_leaf(val)), exp, True):
            raise ValueError('Bad domain expression: %r' % (exp,))
        self.__exp = exp
        self.__field_tables = {}  # used to store the table to use for the sql generation. key = index of the leaf
        self.__all_tables = set()
        self.__joins = []
        self.__main_table = None # 'root' table. set by parse()
        self.__DUMMY_LEAF = (1, '=', 1) # a dummy leaf that must not be parsed or sql generated

    @property
    def exp(self):
        return self.__exp[:]

    def parse(self, cr, uid, table, context):
        """ transform the leafs of the expression """
        if not self.__exp:
            return self

        def _rec_get(ids, table, parent=None, left='id', prefix=''):
            if table._parent_store and (not table.pool._init):
# TODO: Improve where joins are implemented for many with '.', replace by:
# doms += ['&',(prefix+'.parent_left','<',o.parent_right),(prefix+'.parent_left','>=',o.parent_left)]
                doms = []
                for o in table.browse(cr, uid, ids, context=context):
                    if doms:
                        doms.insert(0, '|')
                    doms += ['&', ('parent_left', '<', o.parent_right), ('parent_left', '>=', o.parent_left)]
                if prefix:
                    return [(left, 'in', table.search(cr, uid, doms, context=context))]
                return doms
            else:
                def rg(ids, table, parent):
                    if not ids:
                        return []
                    ids2 = table.search(cr, uid, [(parent, 'in', ids)], context=context)
                    return ids + rg(ids2, table, parent)
                return [(left, 'in', rg(ids, table, parent or table._parent_name))]

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
                if left == 'id' and operator == 'child_of':
                    dom = _rec_get(right, working_table)
                    self.__exp = self.__exp[:i] + dom + self.__exp[i+1:]
                continue

            field_obj = table.pool.get(field._obj)
            if len(fargs) > 1:
                if field._type == 'many2one':
                    right = field_obj.search(cr, uid, [(fargs[1], operator, right)], context=context)
                    if right == []:
                        self.__exp[i] = ( 'id', '=', 0 )
                    else:
                        self.__exp[i] = (fargs[0], 'in', right)
                # Making search easier when there is a left operand as field.o2m or field.m2m
                if field._type in ['many2many','one2many']:
                    right = field_obj.search(cr, uid, [(fargs[1], operator, right)], context=context)
                    right1 = table.search(cr, uid, [(fargs[0],'in', right)], context=context)
                    if right1 == []:
                        self.__exp[i] = ( 'id', '=', 0 )
                    else:
                        self.__exp[i] = ('id', 'in', right1)

                if not isinstance(field,fields.property):
                    continue

            if field._properties and not field.store:
                # this is a function field that is not stored
                if not field._fnct_search:
                    # the function field doesn't provide a search function and doesn't store
                    # values in the database, so we must ignore it : we generate a dummy leaf
                    self.__exp[i] = self.__DUMMY_LEAF
                else:
                    subexp = field.search(cr, uid, table, left, [self.__exp[i]], context=context)
                    if not subexp:
                        self.__exp[i] = self.__DUMMY_LEAF
                    else:
                        # we assume that the expression is valid
                        # we create a dummy leaf for forcing the parsing of the resulting expression
                        self.__exp[i] = '&'
                        self.__exp.insert(i + 1, self.__DUMMY_LEAF)
                        for j, se in enumerate(subexp):
                            self.__exp.insert(i + 2 + j, se)
            # else, the value of the field is store in the database, so we search on it

            elif field._type == 'one2many':
                # Applying recursivity on field(one2many)
                if operator == 'child_of':
                    if isinstance(right, basestring):
                        ids2 = [x[0] for x in field_obj.name_search(cr, uid, right, [], 'like', context=context, limit=None)]
                    else:
                        ids2 = list(right)
                    if field._obj != working_table._name:
                        dom = _rec_get(ids2, field_obj, left=left, prefix=field._obj)
                    else:
                        dom = _rec_get(ids2, working_table, parent=left)
                    self.__exp = self.__exp[:i] + dom + self.__exp[i+1:]

                else:
                    call_null = True

                    if right is not False:
                        if isinstance(right, basestring):
                            ids2 = [x[0] for x in field_obj.name_search(cr, uid, right, [], operator, context=context, limit=None)]
                            if ids2:
                                operator = 'in'
                        else:
                            if not isinstance(right,list):
                                ids2 = [right]
                            else:
                                ids2 = right
                        if not ids2:
                            if operator in ['like','ilike','in','=']:
                                #no result found with given search criteria
                                call_null = False
                                self.__exp[i] = ('id','=',0)
                            else:
                                call_null = True
                                operator = 'in' # operator changed because ids are directly related to main object
                        else:
                            call_null = False
                            o2m_op = 'in'
                            if operator in  ['not like','not ilike','not in','<>','!=']:
                                o2m_op = 'not in'
                            self.__exp[i] = ('id', o2m_op, self.__execute_recursive_in(cr, field._fields_id, field_obj._table, 'id', ids2, operator, field._type))

                    if call_null:
                        o2m_op = 'not in'
                        if operator in  ['not like','not ilike','not in','<>','!=']:
                            o2m_op = 'in'
                        self.__exp[i] = ('id', o2m_op, self.__execute_recursive_in(cr, field._fields_id, field_obj._table, 'id', [], operator, field._type) or [0])

            elif field._type == 'many2many':
                #FIXME
                if operator == 'child_of':
                    if isinstance(right, basestring):
                        ids2 = [x[0] for x in field_obj.name_search(cr, uid, right, [], 'like', context=context, limit=None)]
                    else:
                        ids2 = list(right)

                    def _rec_convert(ids):
                        if field_obj == table:
                            return ids
                        return self.__execute_recursive_in(cr, field._id1, field._rel, field._id2, ids, operator, field._type)

                    dom = _rec_get(ids2, field_obj)
                    ids2 = field_obj.search(cr, uid, dom, context=context)
                    self.__exp[i] = ('id', 'in', _rec_convert(ids2))
                else:
                    call_null_m2m = True
                    if right is not False:
                        if isinstance(right, basestring):
                            res_ids = [x[0] for x in field_obj.name_search(cr, uid, right, [], operator, context=context)]
                            if res_ids:
                                operator = 'in'
                        else:
                            if not isinstance(right, list):
                                res_ids = [right]
                            else:
                                res_ids = right
                        if not res_ids:
                            if operator in ['like','ilike','in','=']:
                                #no result found with given search criteria
                                call_null_m2m = False
                                self.__exp[i] = ('id','=',0)
                            else:
                                call_null_m2m = True
                                operator = 'in' # operator changed because ids are directly related to main object
                        else:
                            call_null_m2m = False
                            m2m_op = 'in'
                            if operator in  ['not like','not ilike','not in','<>','!=']:
                                m2m_op = 'not in'

                            self.__exp[i] = ('id', m2m_op, self.__execute_recursive_in(cr, field._id1, field._rel, field._id2, res_ids, operator, field._type) or [0])
                    if call_null_m2m:
                        m2m_op = 'not in'
                        if operator in  ['not like','not ilike','not in','<>','!=']:
                            m2m_op = 'in'
                        self.__exp[i] = ('id', m2m_op, self.__execute_recursive_in(cr, field._id1, field._rel, field._id2, [], operator,  field._type) or [0])

            elif field._type == 'many2one':
                if operator == 'child_of':
                    if isinstance(right, basestring):
                        ids2 = [x[0] for x in field_obj.name_search(cr, uid, right, [], 'like', limit=None)]
                    elif isinstance(right, (int, long)):
                        ids2 = list([right])
                    else:
                        ids2 = list(right)

                    self.__operator = 'in'
                    if field._obj != working_table._name:
                        dom = _rec_get(ids2, field_obj, left=left, prefix=field._obj)
                    else:
                        dom = _rec_get(ids2, working_table, parent=left)
                    self.__exp = self.__exp[:i] + dom + self.__exp[i+1:]
                else:
                    def _get_expression(field_obj,cr, uid, left, right, operator, context=None):
                        if context is None:
                            context = {}                        
                        c = context.copy()
                        c['active_test'] = False
                        #Special treatment to ill-formed domains
                        operator = ( operator in ['<','>','<=','>='] ) and 'in' or operator
                        
                        dict_op = {'not in':'!=','in':'=','=':'in','!=':'not in','<>':'not in'}
                        if isinstance(right,tuple):
                            right = list(right)
                        if (not isinstance(right,list)) and operator in ['not in','in']:
                            operator = dict_op[operator]
                        elif isinstance(right,list) and operator in ['<>','!=','=']: #for domain (FIELD,'=',['value1','value2'])
                            operator = dict_op[operator]
                        res_ids = field_obj.name_search(cr, uid, right, [], operator, limit=None, context=c)
                        if not res_ids:
                           return ('id','=',0)
                        else:
                            right = map(lambda x: x[0], res_ids)
                            return (left, 'in', right)

                    m2o_str = False
                    if right:
                        if isinstance(right, basestring): # and not isinstance(field, fields.related):
                            m2o_str = True
                        elif isinstance(right,(list,tuple)):
                            m2o_str = True
                            for ele in right:
                                if not isinstance(ele, basestring): 
                                    m2o_str = False
                                    break
                    elif right == []:
                        m2o_str = False
                        if operator in ('not in', '!=', '<>'):
                            # (many2one not in []) should return all records
                            self.__exp[i] = self.__DUMMY_LEAF
                        else:
                            self.__exp[i] = ('id','=',0)
                    else:
                        new_op = '='
                        if operator in  ['not like','not ilike','not in','<>','!=']:
                            new_op = '!='
                        #Is it ok to put 'left' and not 'id' ?
                        self.__exp[i] = (left,new_op,False)
                        
                    if m2o_str:
                        self.__exp[i] = _get_expression(field_obj,cr, uid, left, right, operator, context=context)
            else:
                # other field type
                # add the time part to datetime field when it's not there:
                if field._type == 'datetime' and self.__exp[i][2] and len(self.__exp[i][2]) == 10:

                    self.__exp[i] = list(self.__exp[i])

                    if operator in ('>', '>='):
                        self.__exp[i][2] += ' 00:00:00'
                    elif operator in ('<', '<='):
                        self.__exp[i][2] += ' 23:59:59'

                    self.__exp[i] = tuple(self.__exp[i])

                if field.translate:
                    if operator in ('like', 'ilike', 'not like', 'not ilike'):
                        right = '%%%s%%' % right

                    operator = operator == '=like' and 'like' or operator

                    new_op = 'inselect'
                    if operator in ['not like', 'not ilike', 'not in', '<>', '!=']:
                        new_op = 'not inselect'
                        operator = {'not like': 'like', 'not ilike': 'ilike', 'not in': 'in', '<>': '=', '!=': '='}[operator]

                    query1 = '( SELECT res_id'          \
                             '    FROM ir_translation'  \
                             '   WHERE name = %s'       \
                             '     AND lang = %s'       \
                             '     AND type = %s'
                    instr = ' %s'
                    #Covering in,not in operators with operands (%s,%s) ,etc.
                    if operator == 'in':
                        instr = ','.join(['%s'] * len(right))
                        query1 += '     AND value ' + operator +  ' ' +" (" + instr + ")"   \
                             ') UNION ('                \
                             '  SELECT id'              \
                             '    FROM "' + working_table._table + '"'       \
                             '   WHERE "' + left + '" ' + operator + ' ' +" (" + instr + "))"
                    else:
                        query1 += '     AND value ' + operator + instr +   \
                             ') UNION ('                \
                             '  SELECT id'              \
                             '    FROM "' + working_table._table + '"'       \
                             '   WHERE "' + left + '" ' + operator + instr + ")"

                    query2 = [working_table._name + ',' + left,
                              context.get('lang', False) or 'en_US',
                              'model',
                              right,
                              right,
                             ]

                    self.__exp[i] = ('id', new_op, (query1, query2))

        return self

    def __leaf_to_sql(self, leaf, table):
        if leaf == self.__DUMMY_LEAF:
            return ('(1=1)', [])
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
            query = '(1=0)'

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
                if (right is False or right is None):
                    return ( 'TRUE',[])
                if left in table._columns:
                        format = table._columns[left]._symbol_set[0]
                        query = '(%s.%s %s %s)' % (table._table, left, op, format)
                        params = table._columns[left]._symbol_set[1](right)
                else:
                        query = "(%s.%s %s '%%s')" % (table._table, left, op)
                        params = right

            else:
                if left == 'id':
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
        for i, e in reverse_enumerate(self.__exp):
            if self._is_leaf(e, internal=True):
                table = self.__field_tables.get(i, self.__main_table)
                q, p = self.__leaf_to_sql(e, table)
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
        return (query, flatten(params))

    def get_tables(self):
        return ['"%s"' % t._table for t in self.__all_tables]

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

