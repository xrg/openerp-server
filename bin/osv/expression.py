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
        OPS = ('=', '!=', '<>', '<=', '<', '>', '>=', '=?', 
                '=like', '=ilike', 'like', 'not like', 'ilike', 'not ilike', 
                'in', 'not in',
                'child_of', '|child_of' )
        INTERNAL_OPS = OPS + ('inselect', 'not inselect')
        return (isinstance(element, tuple) or isinstance(element, list)) \
           and len(element) == 3 \
           and (((not internal) and element[1] in OPS) \
                or (internal and element[1] in INTERNAL_OPS))

    def __execute_recursive_in(self, cr, s, f, w, ids, op, type):
        # todo: merge into parent query as sub-query
        res = []
        qry = None
        params = []
        if ids:
            if op in ['<','>','>=','<=']:
                qry = 'SELECT "%s" FROM "%s"'    \
                          ' WHERE "%s" %s %%s' % (s, f, w, op)
                params = [ids[0], ]
            elif self.__mode in ('pg90', 'pg84', 'pgsql'):
                if isinstance(ids, placeholder):
                    dwc = '= %s' % ids.expr
                    params = []
                elif isinstance(ids, list) and isinstance(ids[0], placeholder):
                    dwc = '= %s' % ids[0].expr
                    params = []
                elif isinstance(ids, (int, long)):
                    dwc = '= %s'
                    params = [ids,]
                else:
                    dwc = '= ANY(%s)'
                    params = [ids,]
                qry = 'SELECT "%s"'    \
                           '  FROM "%s"'    \
                           ' WHERE "%s" %s' % (s, f, w, dwc)
            else:
                for i in range(0, len(ids), cr.IN_MAX):
                    subids = ids[i:i+cr.IN_MAX]
                    cr.execute('SELECT "%s"'    \
                               '  FROM "%s"'    \
                               ' WHERE "%s" IN (%s) ' % (s, f, w, 
                                            ','.join(['%s']*len(subids)) ),
                                        subids)
                    res.extend([r[0] for r in cr.fetchall()])
                # we return early, with the bare results
                return None, res

        else:
            qry = 'SELECT distinct("%s")' \
                           '  FROM "%s" where "%s" is not null'  % (s, f, s)
            params = []
           
        if self.__mode in ('pgsql', 'pg84', 'pg90'):
            return qry, params
        else:
            cr.execute(qry, params)
            res.extend([r[0] for r in cr.fetchall()])
            return None, res
        # unreachable code
        return None, None

    def __init__(self, exp, mode='old'):
        """  Initialize an expression to be evaluated on the object storage
             Mode can be 'old', 'sql', 'pgsql', 'pg84' or 'pg90', according 
             to if a db will execute the expression.
             At pgsql, pg84, pg90, sub-queries are allowed. At pg84, pg90,
             recursive ones are used for 'child_of' expressions
        """
        # check if the expression is valid
        if not reduce(lambda acc, val: acc and (self._is_operator(val) or self._is_leaf(val)), exp, True):
            raise ValueError('Bad domain expression: %r' % (exp,))
        self.__exp = exp
        self.__field_tables = {}  # used to store the table to use for the sql generation. key = index of the leaf
        self.__all_tables = set()
        self.__joins = []
        self.__main_table = None # 'root' table. set by parse()
        self.__DUMMY_LEAF = (1, '=', 1) # a dummy leaf that must not be parsed or sql generated
        self.__mode = mode

    @property
    def exp(self):
        return self.__exp[:]

    def parse(self, cr, uid, table, context):
        """ transform the leafs of the expression """
        if not self.__exp:
            return self

        def _rec_get(ids, table, parent=None, left='id', prefix='', null_too=False):
            """ Compute the sub-expression for a recursive field's operator, 
                typically 'child_of'
                
                If null_too is specified, the expression would also stand for left = NULL
            """
            if table._parent_store and (not table.pool._init): #and False:
# TODO: Improve where joins are implemented for many with '.', replace by:
# doms += ['&',(prefix+'.parent_left','<',o.parent_right),(prefix+'.parent_left','>=',o.parent_left)]
                doms = []
                for o in table.browse(cr, uid, ids, context=context):
                    if doms:
                        doms.insert(0, '|')
                    doms += ['&', ('parent_left', '<', o.parent_right), ('parent_left', '>=', o.parent_left)]
                if prefix:
                    if null_too:
                        return ['|', (left, '=', False), (left, 'in', table.search(cr, uid, doms, context=context))]
                    return [(left, 'in', table.search(cr, uid, doms, context=context))]
                if null_too:
                    doms = ['|', (left, '=', False)] + doms
                return doms
            elif self.__mode in ('pg84', 'pg90'):
                # print "Recursive expand for 8.4, for %s" % table._table
                phname = prefix + table._table
                phname = phname.replace('.', '_')
                phexpr = '%s_rsrch.id' % phname
                
                ttables = ['"%s"' % table._table]
                qu1, qu2, qtables = table._where_calc(cr, uid, 
                        [(parent or table._parent_name, '=', placeholder(phname, phexpr) )], context)

                d1, d2, dtables = table.pool.get('ir.rule').domain_get(cr, uid, table._name)
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
                        table._table,
                        table._table, ttables2, phname, ' AND '.join(qu1),
                        phname)
                
                # print "INSELECT %s" % qry
                # print "args:", qu2
                if null_too:
                    return ['|', (left, '=', False), (left, 'inselect', (qry, qu2))]
                return [(left, 'inselect', (qry, qu2))]
            # elif self.__mode == 'pgsql':
            #  any way  to do that in pg8.3?
            else:
                def rg(ids, table, parent):
                    if not ids:
                        return []
                    ids2 = table.search(cr, uid, [(parent, 'in', ids)], context=context)
                    return ids + rg(ids2, table, parent)
                if null_too:
                    res = ['|', (left, '=', False)]
                else:
                    res = []
                res += [(left, 'in', rg(ids, table, parent or table._parent_name))]
                return res

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
                    dom = _rec_get(right, working_table, null_too=(operator == '|child_of'))
                    self.__exp = self.__exp[:i] + dom + self.__exp[i+1:]
                continue

            field_obj = table.pool.get(field._obj)
            if len(fargs) > 1: # *-*
                if field._type == 'many2one':
                    right = field_obj.search(cr, uid, [(fargs[1], operator, right)], context=context)
                    self.__exp[i] = (fargs[0], 'in', right)
                # Making search easier when there is a left operand as field.o2m or field.m2m
                if field._type in ['many2many','one2many']:
                    right = field_obj.search(cr, uid, [(fargs[1], operator, right)], context=context)
                    right1 = table.search(cr, uid, [(fargs[0],'in', right)], context=context)
                    self.__exp[i] = ('id', 'in', right1)
                continue

            if field._properties and ((not field.store) or field._fnct_search):
                # this is a function field
                if not field._fnct_search:
                    # the function field doesn't provide a search function and doesn't store
                    # values in the database, so we must ignore it : we generate a dummy leaf
                    self.__exp[i] = self.__DUMMY_LEAF
                else:
                    subexp = field.search(cr, uid, table, left, [self.__exp[i]], context=context)
                    # we assume that the expression is valid
                    # we create a dummy leaf for forcing the parsing of the resulting expression
                    self.__exp[i] = '&'
                    self.__exp.insert(i + 1, self.__DUMMY_LEAF)
                    for j, se in enumerate(subexp):
                        self.__exp.insert(i + 2 + j, se)
            # else, the value of the field is store in the database, so we search on it

            elif field._type == 'one2many':
                # Applying recursivity on field(one2many)
                if operator == 'child_of' or operator == '|child_of':
                    if isinstance(right, basestring):
                        ids2 = [x[0] for x in field_obj.name_search(cr, uid, right, [], 'like', context=context, limit=None)]
                    else:
                        ids2 = list(right)
                    null_too = (operator == '|child_of')
                    if field._obj != working_table._name:
                        dom = _rec_get(ids2, field_obj, left=left, prefix=field._obj, null_too=null_too)
                    else:
                        dom = _rec_get(ids2, working_table, parent=left, null_too=null_too)
                    self.__exp = self.__exp[:i] + dom + self.__exp[i+1:]

                else:
                    call_null = True

                    if right:
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
                            erqu, erpa = self.__execute_recursive_in(cr, field._fields_id,
                                                   field_obj._table, 'id', ids2, operator, field._type)
                            if not erqu:
                                self.__exp[i] = ('id', o2m_op, erpa )
                            else:
                                if o2m_op in ('in', '='):
                                    o2m_op = 'inselect'
                                elif o2m_op in ('not in', '!=', '<>'):
                                    o2m_op = 'not inselect'
                                else:
                                    raise NotImplementedError('operator: %s' % o2m_op)
                                self.__exp[i] = ('id', o2m_op, (erqu, erpa))

                    if call_null:
                        o2m_op = 'not in'
                        if operator in  ['not like','not ilike','not in','<>','!=']:
                            o2m_op = 'in'

                        erqu, erpa = self.__execute_recursive_in(cr, field._fields_id, 
                                        field_obj._table, 'id', [], operator, field._type)
                        if not erqu:
                            self.__exp[i] = ('id', o2m_op, erpa )
                        else:
                            if o2m_op in ('in', '='):
                                o2m_op = 'inselect'
                            elif o2m_op in ('not in', '!=', '<>'):
                                o2m_op = 'not inselect'
                            else:
                                raise NotImplementedError('operator: %s' % o2m_op)
                            self.__exp[i] = ('id', o2m_op, (erqu, erpa))

            elif field._type == 'many2many':
                #FIXME
                if operator == 'child_of' or operator == '|child_of':
                    if isinstance(right, basestring):
                        ids2 = [x[0] for x in field_obj.name_search(cr, uid, right, [], 'like', context=context, limit=None)]
                    else:
                        ids2 = list(right)

                    def _rec_convert(ids):
                        if field_obj == table:
                            return ids
                        erqu, erpa = self.__execute_recursive_in(cr, field._id1, field._rel, field._id2, ids, operator, field._type)
                        assert(not erqu) # TODO
                        return erpa

                    dom = _rec_get(ids2, field_obj, null_too=(operator == '|child_of'))
                    ids2 = field_obj.search(cr, uid, dom, context=context)
                    self.__exp[i] = ('id', 'in', _rec_convert(ids2))
                else:
                    call_null_m2m = True
                    if right:
                        if isinstance(right, basestring):
                            res_ids = [x[0] for x in field_obj.name_search(cr, uid, right, [], operator, context=context)]
                            if res_ids:
                                operator = 'in'
                        else:
                            if isinstance(right, tuple):
                                res_ids = list(map(int, right))
                            elif not isinstance(right, list):
                                res_ids = [ int(right) ]
                            else:
                                res_ids = map(int, right)
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

                            erqu, erpa = self.__execute_recursive_in(cr, field._id1, field._rel, field._id2, res_ids, operator, field._type)
                            if not erqu:
                                self.__exp[i] = ('id', m2m_op, erpa )
                            else:
                                if m2m_op in ('in', '='):
                                    m2m_op = 'inselect'
                                elif m2m_op in ('not in', '!=', '<>'):
                                    m2m_op = 'not inselect'
                                else:
                                    raise NotImplementedError('operator: %s' % m2m_op)
                                self.__exp[i] = ('id', m2m_op, (erqu, erpa))
                    if call_null_m2m:
                        m2m_op = 'not in'
                        if operator in  ['not like','not ilike','not in','<>','!=']:
                            m2m_op = 'in'
                        erqu, erpa = self.__execute_recursive_in(cr, field._id1, field._rel, field._id2, [], operator,  field._type)
                        if not erqu:
                            self.__exp[i] = ('id', m2m_op, erpa )
                        else:
                            if m2m_op in ('in', '='):
                                m2m_op = 'inselect'
                            elif m2m_op in ('not in', '!=', '<>'):
                                m2m_op = 'not inselect'
                            else:
                                raise NotImplementedError('operator: %s' % m2m_op)
                            self.__exp[i] = ('id', m2m_op, (erqu, erpa))

            elif field._type == 'many2one':
                if isinstance(right, list) and len(right) and isinstance(right[0], tuple):
                    # That's a nested expression
                    print "nested expr: %s for field %s" % ( right, field._obj)
                    
                    assert(operator == 'in') # others not implemented
                    # Note, we don't actually check access permissions for the
                    # intermediate object yet. That wouldn't still let us read
                    # the forbidden record, but just use its id
                    
                    qu1, qu2, qtables = field_obj._where_calc(cr, uid, right, context)
                    
                    d1, d2, dtables = table.pool.get('ir.rule').domain_get(cr, uid, field_obj._name)
                    if d1:
                        qu1.append(d1)
                        qu2 += d2
                        
                        for dt in dtables:
                            if dt not in qtables:
                                qtables.append(dt)
                
                    qry = "SELECT id FROM %s WHERE %s " %( ', '.join(qtables), ' AND '.join(qu1))

                    self.__exp[i] = (left,'inselect', (qry, qu2))
                    print "Nested query now is like:", self.__exp[i]
                elif operator == 'child_of' or operator == '|child_of' :
                    if isinstance(right, basestring):
                        ids2 = [x[0] for x in field_obj.name_search(cr, uid, right, [], 'like', limit=None)]
                    elif isinstance(right, (int, long)):
                        ids2 = list([right])
                    else:
                        ids2 = list(right)

                    self.__operator = 'in'
                    null_too = (operator == '|child_of')
                    if field._obj != working_table._name:
                        dom = _rec_get(ids2, field_obj, left=left, prefix=field._obj, null_too=null_too)
                    else:
                        dom = _rec_get(ids2, working_table, parent=left, null_too=null_too)
                    self.__exp = self.__exp[:i] + dom + self.__exp[i+1:]
                else:
                    def _get_expression(field_obj,cr, uid, left, right, operator, context=None):
                        if context is None:
                            context = {}
                        c = context.copy()
                        c['active_test'] = False
                        dict_op = {'not in':'!=','in':'='}
                        if isinstance(right,tuple):
                            right = list(right)
                        if (not isinstance(right,list)) and operator in ['not in','in']:
                            operator = dict_op[operator]
                            
                        res_ids = field_obj.name_search(cr, uid, right, [], operator, limit=None, context=c)
                        if not res_ids:
                             return ('id','=',0)
                        else:
                            right = map(lambda x: x[0], res_ids)
                            return (left, 'in', right)

                    m2o_str = False
                    if isinstance(right, basestring): # and not isinstance(field, fields.related):
                        m2o_str = True
                    elif isinstance(right, list) or isinstance(right, tuple):
                        m2o_str = True
                        for ele in right:
                            if not isinstance(ele, basestring): 
                                m2o_str = False
                                break

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

                    query1 = '( SELECT res_id'          \
                             '    FROM ir_translation'  \
                             '   WHERE name = %s'       \
                             '     AND lang = %s'       \
                             '     AND type = %s'
                    instr = ' %s'
                    #Covering in,not in operators with operands (%s,%s) ,etc.
                    if operator in ['in','not in']:
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

                    self.__exp[i] = ('id', 'inselect', (query1, query2))

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

            elif (operator == 'child_of' or operator == '|child_of'):
                raise Exception("Cannot compute %s %s %s in sql" %(left, operator, right))
            else:
                if isinstance(right, placeholder):
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

