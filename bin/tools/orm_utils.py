# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2011,2012 P. Christeas <xrg@hellug.gr>
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

#.apidoc title: Utility functions for ORM classes

"""These functions closely accompany ORM classes

    However, since we don't want to create unwanted imports of orm.py, we put
    these extra functions here
"""

browse_record_list = None # MUST be set by orm.py!
browse_null = None
browse_record = None
except_orm = None

def only_ids(ids):
    """ Return the list of ids from either a browse_record_list or plain list
    """
    if isinstance(ids, browse_record_list):
        return [ id._id for id in ids]
    else:
        return ids

# fields(copy_data) helpers:

def copy_false(*args, **kw):
    """Empty value, for scalar fields """
    return False

def copy_empty(*args, **kw):
    """Empty set, for x2many fields """
    return []

def copy_value(value):
    """Use this default value. Will yield a function"""
    return lambda *a, **kw: value

def copy_default(cr, uid, obj, id, f, data, context):
    """Use the default value at copying
    
        So far, this method uses *only* the value of _defaults. It does
        not consult ir.values or so.
    """
    val = obj._defaults.get(f, NotImplemented)
    if val is NotImplemented:
        raise KeyError("At %s.%s copy_default is specified, but no value in _defaults!" %\
                        (obj._name,f))
    if callable(val):
        return val(obj, cr, uid, context)
    else:
        return val

# Closure functions

def cl_user_id(self, cr, uid, context):
    """ Returns `uid` for column _defaults

        A trivial operation, but makes the _defaults section more readable,
        w/o lambdas.
    """
    return uid

def cl_company_default_get(model):
    """Return closure function for _company_default_get

        Sometimes, we want to set the `company_id` as::

            _defaults = { 'company_id': lambda s,c,u,ct: ..._company_default_get(...,'foo.model',) }

        we can replace it now with::

            _defaults = { 'company_id': cl_company_default_get('foo.model'), }

        @param model The model being passed to _company_default_get()
    """
    return lambda self, cr, uid, context: \
            self.pool.get('res.company')._company_default_get(cr, uid, model, context=context)

def cl_sequence_next(seq_name, default=None):
    """Retrieve the next number of a named ir.sequence
    """
    return lambda self,cr,uid,ctx=None: \
            self.pool.get('ir.sequence').get(cr, uid, seq_name, ctx) or default

def cl_company_id(self, cr, uid, ctx=None):
    """ Get the company_id of the current user
    """
    return self.pool.get('res.users').browse(cr, uid, uid, ctx).company_id.id

class ORM_stat_fields(object):
    """Statistic-collecting object of ORM fields access

        To be used by the browse object, this class shall behave like the previous dict(),
        but also implictly maintain the statistics.

        The selection threshold, so far, is a simple heuristic: we take all fields whose
        access count is greater than 1/THRESHOLD_DIV * most_used one .
    """
    THRESHOLD_DIV = 3
    COUNT_THRES = 10

    def __init__(self):
        self.flds = []

    def touch(self, name):
        """ Increment the counter of "name" by +1
        """
        c = 0
        for i, f in enumerate(self.flds):
            if f[0] == name:
                c = self.flds.pop(i)[1]
                break
        else:
            i = len(self.flds)
        c += 1
        while i and self.flds[i-1][1] < c:
            i -= 1
        self.flds.insert(i, (name, c))

    def most_common(self, n=None):
        """Return most common *names* of fields

            @param n if given, only the `n` most common ones, else use the built-in
            heuristic.

            Note: it will also return an empty set if the most common field has not
            been touched at least COUNT_THRES times. This is used to force full fetch
            until considerable statistics are available.
        """
        if (not self.flds) or (self.flds[0][1] < self.COUNT_THRES):
            return []
        elif n is None:
            ret = []
            thres = self.flds[0][1] / self.THRESHOLD_DIV
            for name, c in self.flds:
                if c >= thres:
                    ret.append(name)
                else:
                    break
            return ret
        else:
            return [f[0] for f in self.flds[:n]]

    def stats(self):
        """Return string of current values
        """
        return ', '.join([ '%s: %s' % x for x in self.flds ] )
    
    def extend(self, other):
        """ Needed for osv.createInstance(), take values of `other`
        """
        if not other.flds:
            return
        assert not self.flds, '%r %r' %(self.flds, other.flds)
        self.flds = other.flds
        other.flds = None # don't let it be used again

#eof
