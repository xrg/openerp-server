# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2010 Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2009,2011-2014 P. Christeas <xrg@hellug.gr>
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

#.apidoc title: Object Relational Mapping
#.apidoc module-mods: member-order: bysource

"""The 'M' component in OpenObject's MVC

  Object relational mapping to database (postgresql) module
     * Hierarchical structure
     * Constraints consistency, validations
     * Object meta Data depends on its status
     * Optimised processing by complex query (multiple actions at once)
     * Default fields value
     * Permissions optimisation
     * Persistant object: DB postgresql
     * Datas conversions
     * Multi-level caching system
     * 2 different inheritancies
     * Fields:
          - classicals (varchar, integer, boolean, ...)
          - relations (one2many, many2one, many2many)
          - functions
 
"""

import calendar
import copy
import datetime
import logging
import warnings
from operator import itemgetter
from collections import defaultdict
import itertools
import pickle
import re
import time
import types
import psycopg2
from psycopg2 import DatabaseError, IntegrityError, _psycopg

import netsvc
from lxml import etree
from tools.config import config
from tools.translate import _
from tools import sql_model
from tools import orm_utils

import fields
import fields_relational
from views import oo_view
from query import Query
import tools
from tools.safe_eval import safe_eval as eval
from tools.expr_utils import DomainError

# List of etree._Element subclasses that we choose to ignore when parsing XML.
from tools import SKIPPED_ELEMENT_TYPES

__hush_pyflakes = [ netsvc, IntegrityError, DatabaseError, DomainError]

regex_order = re.compile('^(([a-z0-9_]+|"[a-z0-9_]+")( *desc| *asc)?( *, *|))+$', re.I)

FIELDS_ONLY_DEFAULT = 'auto'
""" This controls the "fields_only" feature. The purpose of the feature is to
    optimize the set of fields fetched each time a browse() is used.
    Some tables are bloated with dozens of fields, which are rarely used by
    the browse objects (sometimes, when browsing, we merely need the 'name'
    column), so it would be a waste of resources to fetch them all the time.
 
    There are 4 modes:
        - False:    Was the default before this feature, will prefetch all the
          fields of the table
        - True:     The default in pg84 for some time, will only prefetch the
          field that was asked in the browse()
        - [f1, f2..]:  Will prefetch the fields of the list/tuple, is used for
          manually tuning the optimization
        - 'auto':   Will use the _column_stats of the table to select the
          most popular fields (AUTO_SELECT_COLS) to prefetch
"""

AUTO_SELECT_COLS = 4
""" Columns of table to prefetch by default
"""

# not used yet AUTO_SELECT_WRAP = 1000000 # prevent integer overflow, wrap at that num.

def intersect(la, lb):
    return filter(lambda x: x in lb, la)

class except_orm(Exception):
    def __init__(self, name, value):
        self.name = name
        self.value = value
        self.args = (name, value)

class pythonOrderBy(object):
    """ Placeholder class for _generate_order_by() requesting python sorting
    """

    def __init__(self, inlist):
        """
            @param inlist is a list of column + direction, which we parse

            Creates self.plist which will be (direction, [getters])
        """
        old_direction = 3 # neither True or False
        self.plist = []
        self.fields = []
        for x in inlist:
            if ' ' in x:
                nkey, ndir = x.split(' ', 1)
                ndir = (ndir.lower() != 'desc')
            else:
                nkey = x
                ndir = True

            if '.' in nkey:
                # For regular fields, _generate_order_by will give us
                # "table_name".field_name . But we operate on the read()
                # results, which typically only have the `field_name`
                nkey = nkey.rsplit('.',1)[-1]
            nkey = nkey.strip('"')

            if nkey.endswith(':'):
                nkey = nkey[:-1]
                ngetter = lambda k: ((k[nkey] and k[nkey][1]) or None) # the visual string of m2o
                self.fields.append(nkey)
            else:
                ngetter = itemgetter(nkey)
                self.fields.append(nkey)

            if ndir != old_direction:
                self.plist.append((ndir, []))
            self.plist[-1][1].append(ngetter)

    def needs_more(self):
        return bool(self.plist)

    def get_next_sort(self):
        """ returns direction + function to use in sort(key=...)
        """
        if not self.plist:
            raise IndexError("List exhausted already")

        direction, getters = self.plist.pop()
        def sort_key(k):
            return tuple([ g(k) for g in getters])
        return direction, sort_key


    def get_fields(self):
        """Return the field components of this order-by list
        """
        return self.fields

class BrowseRecordError(Exception):
    pass

_logger = logging.getLogger('orm')
""" Persistent logger to be used throughout ORM
"""

class browse_null(object):
    """ Readonly python database object browser
    """
    __slots__ = ('id', '_id')

    def __init__(self):
        self.id = self._id = False

    def __getitem__(self, name):
        return None

    def __getattr__(self, name):
        return None  # XXX: return self ?

    def __int__(self):
        return False

    def __str__(self):
        return ''

    def __nonzero__(self):
        return False

    def __unicode__(self):
        return u''

    def __conform__(self, *args):
        """ If we find ourselves in an SQL query, we are NULL
            This is a dirty hack and will most probable never work, 
            since "a = NULL" is never valid.
            Still, a failed SQL query may help more in debugging than
            one that never reached the db.
        """
        return _psycopg.AsIs('NULL')

#
# TODO: execute an object method on browse_record_list
#
class browse_record_list(list):
    """ Collection of browse objects
    
        Such an instance will be returned when doing a ``browse([ids..])``
        and will be iterable, yielding browse() objects
    """
    __slots__ = ('context',)

    def __init__(self, lst, context=None):
        if not context:
            context = {}
        super(browse_record_list, self).__init__(lst)
        self.context = context

    def __add__(self, y):
        return browse_record_list(list.__add__(self, y), context=self.context)

only_ids = orm_utils.only_ids # take it from there..
orm_utils.browse_record_list = browse_record_list # ..put that one back
orm_utils.browse_null = browse_null
orm_utils.except_orm = except_orm

class browse_record(object):
    """ An object that behaves like a row of an object's table.
        It has attributes after the columns of the corresponding object.
        
        If 'fields_only' is specified in the initializer, then only the
        asked for fields will be fetched from the db. This parameter can
        be False, which fetches all columns, True, which fetches one at a
        time, or a list/tuple, which indicates which columns to also 
        prefetch.
        
        Examples::
        
            uobj = pool.get('res.users')
            user_rec = uobj.browse(cr, uid, 104)
            name = user_rec.name
        
        If you explicitly want to fetch some columns, re-write as::
        
            user_rec = uobj.browse(cr,uid, 104, fields_only =('name', 'email', 'signature'))
            name = user_rec.name
            email = user_rec.email
            signature = user_rec.signature

        @note With the default "auto" mode, column prefetching should be efficient
            enough, so you can avoid using 'fields_only'.
    """
    __logger = logging.getLogger('orm.browse_record')
    
    __slots__ = ('_list_class', '_cr', '_uid', '_id', '_table', '_table_name', \
               '_context', '_fields_process', '_fields_only', '_data', '_cache')

    def __init__(self, cr, uid, id, table, cache, context=None, list_class=None,
                fields_process=None, fields_only=FIELDS_ONLY_DEFAULT):
        """
        @param cache a dictionary of model->field->data to be shared accross browse
            objects, thus reducing the SQL read()s . It can speed up things a lot,
            but also be disastrous if not discarded after write()/unlink() operations
        @param table the object (inherited from orm)
        @param context dictionary with an optional context
        """
        if fields_process is None:
            fields_process = {}
        if context is None:
            context = {}
        self._list_class = list_class or browse_record_list
        self._cr = cr
        self._uid = uid
        self._id = id
        self._table = table
        self._table_name = self._table._name
        self._context = context
        self._fields_process = fields_process
        self._fields_only = fields_only

        cache.setdefault(table._name, {})
        self._data = cache[table._name]

        if not (id and isinstance(id, (int, long,))):
            raise BrowseRecordError(_('Wrong ID for the %s browse record, got %r, expected an integer.') % (self._table_name, id,))
#        if not table.exists(cr, uid, id, context):
#            raise BrowseRecordError(_('Object %s does not exists') % (self,))

        if id not in self._data:
            self._data[id] = {'id': id}

        self._cache = cache

    def __getitem__(self, name):
        if name == 'id':
            return self._id

        if name not in self._data[self._id]:
            # build the list of fields we will fetch

            if self._table._debug:
                self.__logger.debug("self[%d].%s w. %s" % (self._id, name, self._fields_only))
            
            # Virtual members have precedence over any local ones
            if self._table._vtable and name in self._table._vtable:
                if self._table._debug:
                    self.__logger.debug("%s.%s is virtual, fetching for %s", 
                            self._table._name, name, self._id)
                if '_vptr' not in self._data[self._id]:
                    ids_v = filter(lambda id: '_vptr' not in self._data[id], self._data.keys())
                    vptrs = self._table.read(self._cr, self._uid, ids_v, ['_vptr'],
                            context=self._context, load="_classic_write")
                    for data in vptrs:
                        if len(str(data['id']).split('-')) > 1:
                            data['id'] = int(str(data['id']).split('-')[0])
                        if '_vptr' not in data:
                            continue
                        # assert len(data) == 2, data  # should only have id, _vptr
                        self._data[data['id']]['_vptr'] = data['_vptr']
                if '_vptr' not in self._data[self._id]:
                    self.__logger.warning("%s.%s is virtual, but no _vptr for #%s!", 
                            self._table._name, name, self._id)
                elif self._data[self._id]['_vptr'] \
                        and self._data[self._id]['_vptr'] != self._name:
                    vobj = self._table.pool.get(self._data[self._id]['_vptr'])
                    if self._debug:
                        self.__logger.debug("%s[%s].%s dispatching to %s..", 
                            self._table._name,self._id, name, vobj._name)
                    # .. and this is where it all happens. We call a browse object
                    # on the class that should handle this virtual member. We try
                    # to preserve as much (cache, fields etc) as possible from the
                    # current browse object. Perhaps it also calls our fetched
                    # fields again..
                    # The id has to be translated. _we currently only search for_
                    # _a single id_ . We use the search-browse feature, hoping for
                    # some future optimisation.
                    bro = vobj.browse(self._cr, self._uid, [(vobj._inherits[self._table._name],'=', self._id) ],
                                context=self._context, fields_process=self._fields_process,
                                fields_only=self._fields_only)
                    # bro must now be a browse_list instance..
                    
                    assert len(bro) == 1, "Virtual object %s[%s=%s] has %s instances " % \
                            (vobj._name, self._table._name, self._id, len(bro))
                    ret = getattr(bro[0], name, NotImplemented)
                    # Still, allow it to fail if there is no such attribute
                    # at the virtual child class. May happen if 2 classes inherits
                    # from self, but only one defines the attribute.
                    # Note that we treat None as a valid attribute value, so we
                    # need something more exotic (NotImplemented) as the placeholder.
                    if ret is not NotImplemented:
                        return ret

                # else fetch the old way..

            # fetch the definition of the field which was asked for
            if name in self._table._columns:
                col = self._table._columns[name]
            elif name in self._table._inherit_fields:
                col = self._table._inherit_fields[name][2]
            elif hasattr(self._table, str(name)):
                attr = getattr(self._table, name)

                if isinstance(attr, (types.MethodType, types.LambdaType, types.FunctionType)):
                    return lambda *args, **argv: attr(self._cr, self._uid, [self._id], *args, **argv)
                else:
                    return attr
            else:
                self.__logger.warning( "Field '%s' does not exist in object '%s'.", name, self._table_name )
                raise KeyError("Field '%s' does not exist in object '%s'" % ( name, self._table_name))

            # if the field is a classic one or a many2one, we'll fetch all classic and many2one fields
            if col._prefetch and (self._fields_only is not True):
                # gen the list of "local" (ie not inherited) fields which are classic or many2one
                fields_to_fetch = filter(lambda x: x[1]._prefetch, self._table._columns.items())
                # complete the field list with the inherited fields which are classic or many2one
                fields_to_fetch += [ (x, xc[2]) for x, xc in self._table._inherit_fields.items() if xc[2]._prefetch]
                # also, filter out the fields that we have already fetched
                fields_to_fetch = filter(lambda f: f[0] not in self._data[self._id], fields_to_fetch)
                if isinstance(self._fields_only, (tuple, list)):
                    fields_to_fetch = filter(lambda f: f[0] == name or f[0] in self._fields_only, fields_to_fetch)
                elif self._fields_only == 'auto':
                    if self._table._debug:
                        self.__logger.debug("Stats for %s are: %s " , self._table._name, self._table._column_stats.stats())
                    stat_fields = self._table._column_stats.most_common()
                    if stat_fields:
                        # Filter out ones that are seldom used:
                        fields_to_fetch = filter(lambda f: f[0] == name or f[0] in stat_fields, fields_to_fetch)
                        if self._table._debug:
                            self.__logger.debug("Auto selecting columns %s of %s for table %s",
                                    [x[0] for x in fields_to_fetch], stat_fields, self._table._name)
                    else:
                        # len(_column) first calls should return all fields!
                        pass
            # otherwise we fetch only that field
            else:
                fields_to_fetch = [(name, col)]
            ids = filter(lambda id: name not in self._data[id], self._data.keys())
            # read the results
            field_names = map(itemgetter(0), fields_to_fetch)

            if self._table._vtable:
                field_names.append('_vptr')
            if self._table._debug:
                self.__logger.debug("Reading ids: %r/ %r", ids, self._data.keys())
            field_values = self._table.read(self._cr, self._uid, ids, field_names, context=self._context, load="_classic_write")
            # if self._table._debug: # too much now, please enable if really needed
            #     self.__logger.debug("Got result %r", field_values)

            # TODO: improve this, very slow for reports
            if self._fields_process:
                lang_obj = None
                if self._context.get('lang', False):
                    lang = self._context['lang']
                    lang_obj_ids = self.pool.get('res.lang').search(self._cr, self._uid, [('code','=',lang)])
                    if not lang_obj_ids:
                        raise Exception(_('Language with code "%s" is not defined in your system !\nDefine it through the Administration menu.') % (lang,))
                    lang_obj = self.pool.get('res.lang').browse(self._cr, self._uid, lang_obj_ids[0])

                for field_name, field_column in fields_to_fetch:
                    if field_column._type in self._fields_process:
                        for result_line in field_values:
                            result_line[field_name] = self._fields_process[field_column._type](result_line[field_name])
                            if result_line[field_name]:
                                result_line[field_name].set_value(self._cr, self._uid, result_line[field_name], self, field_column, lang_obj)

            if not field_values:
                # Where did those ids come from? Perhaps old entries in ir_model_dat?
                self.__logger.warn("No field_values found for ids %s in %s", ids, self)
                raise KeyError('Field %s not found in %s'%(name, self))

            # store the raw data (eg. ids for many2one fields) in cache
            for result_line in field_values:
                res_id = result_line['id']
                del result_line['id']
                self._data[res_id].update(result_line)

            # Prefetch logic: When we fetch the IDs of some relational field,
            # preset these IDs in the browse_cache. Then, any read() of the
            # remote object will fetch *all* these IDs on the first query.
            for rn, rc in fields_to_fetch:
                if isinstance(rc, fields_relational._rel2many):
                    rcache = self._cache.setdefault(rc._obj, {})
                    for rid in itertools.chain.from_iterable(map(itemgetter(rn), field_values)):
                        if rid:
                            rcache.setdefault(rid, {})
                elif isinstance(rc, fields_relational._rel2one):
                    rcache = self._cache.setdefault(rc._obj, {})
                    for rid in map(itemgetter(rn), field_values):
                        if rid:
                            rcache.setdefault(rid, {})
            del rn, rc

        if not name in self._data[self._id]:
            # How did this happen? Could be a missing model due to custom fields used too soon, see above.
            self.__logger.error( "Ffields: %s, datas: %s"%(field_names, field_values))
            self.__logger.error( "Data: %s, Table: %s"%(self._data[self._id], self._table))
            raise KeyError(_('Unknown attribute %s in %s ') % (name, self))

        # update the columns stats
        if True: # not "for f in field_names:", it would falsely prefer the "popular" ones
            # We advance the counter of fetches for the column we have been
            # asked to browse. It is better to advance by 1, since many single
            # fetches of the name is the ones we need to optimize (as opposed
            # to using len(ids) which would prefer the list browses).
            self._table._column_stats.touch(name)

        # Process the return value and convert from raw data into
        # browse records, where applicable. 
        # The browse records shall have a very short life, only as return
        # values of this function, never stored in self._data{} cache
        
        ret = self._data[self._id][name]
        col = None
        if name in self._table._columns:
            col = self._table._columns[name]
        elif name in self._table._inherit_fields:
            col = self._table._inherit_fields[name][2]

        if col and not isinstance(ret, browse_record): # most likely
            # some (relational) fields need to convert their data
            # to browse records
            ret = col._val2browse(ret, name, self)
        return ret

    def __getattr__(self, name):
        if name == 'id':
            return self._id
        try:
            return self[name]
        except KeyError, e:
            if self._table._debug:
                _logger.debug("%r[%s]: KeyError ", self, name, exc_info=True)
            raise AttributeError(e)

    def __contains__(self, name):
        return (name in self._table._columns) or (name in self._table._inherit_fields) or hasattr(self._table, name)

    def __iter__(self):
        raise NotImplementedError("Iteration is not allowed on %s" % self)

    def __hasattr__(self, name):
        return name in self

    def __int__(self):
        return self._id

    def __str__(self):
        return "browse_record(%s, %d)" % (self._table_name, self._id)

    def __eq__(self, other):
        if not isinstance(other, browse_record):
            return False
        return (self._table_name, self._id) == (other._table_name, other._id)

    def __ne__(self, other):
        if not isinstance(other, browse_record):
            return True
        return (self._table_name, self._id) != (other._table_name, other._id)

    # we need to define __unicode__ even though we've already defined __str__
    # because we have overridden __getattr__
    def __unicode__(self):
        return unicode(str(self))

    def __hash__(self):
        return hash((self._table_name, self._id))

    __repr__ = __str__

    def _invalidate(self):
        """Purge internal cache for this record, trigger re-read()

            Should be used if this record has been write()n to, and self
            cannot be discarded safely.

            Best method is to not mix `browse_records` and `write()` calls,
            anyway.
        """
        if self._id not in self._data:
            raise RuntimeError("Record #%d is already removed from cache" % self._id)

        self._data[self._id] = {'id': self._id}

    def _invalidate_others(self, ids, model=None):
        """ Variant of `_invalidate()` that operates on foreign ids

            @param ids List of ids to invalidate
            @param model string name of model to operate on (if exists in cache)

            This can indirectly be called when we write to some other ids, like::

            for b in self.browse(cr, uid, [(find-domain)]):
                print b.name
                # find synonyms
                res = self.search(cr, uid, [('name', '=', b.name), ('id', '!=', b.id)])
                if res:
                    self.write(res, {'description': 'see also %d' % b.id })

                    # make sure we don't affect items cached in `self.browse()`
                    b._invalidate_others(res)
        """

        if model is None:
            data = self._data
        elif model in self._cache:
            data = self._cache[model]
        else:
            return None
        for i in ids:
            if i in data: # only those that are cached
                data[i] = {'id': i}

        return

orm_utils.browse_record = browse_record

_import_id_re = re.compile(r'([a-z0-9A-Z_])\.id$')

class orm_template(object):
    """ THE base of all ORM models
    """
    #TODO doc!
    _name = None
    _columns = {}
    _constraints = []
    _defaults = {}
    _rec_name = 'name'
    _parent_name = 'parent_id'
    _parent_store = False
    _parent_order = False
    _date_name = 'date'
    _order = 'id'
    _sequence = None
    _description = None
    _inherits = {}
    _implements = None
    _table = None
    _invalids = set() # FIXME: why persistent?
    _log_create = False
    _virtuals = None
    _vtable = False

    CONCURRENCY_CHECK_FIELD = '__last_update'
    def log(self, cr, uid, id, message, secondary=False, context=None):
        """Insert a line in res.log about this ORM record
        """
        try:
            if isinstance(id, (list, tuple)):
                assert len(id) == 1, id
                id = id[0]
            return self.pool.get('res.log').create(cr, uid,
                {
                    'name': message,
                    'res_model': self._name,
                    'secondary': secondary,
                    'res_id': id,
                },
                    context=context
            )
        except psycopg2.ProgrammingError:
            # our cursor is screwed, hopeless
            raise
        except Exception:
            _logger.warning("Could not create res.log line: %s", message, exc_info=True)

    def view_init(self, cr , uid , fields_list, context=None):
        """Override this method to do specific things when a view on the object is opened."""
        pass

    def read_group(self, cr, uid, domain, fields, groupby, offset=0, limit=None, context=None, orderby=False):
        raise NotImplementedError(_('The read_group method is not implemented on this object !'))

    def _field_create(self, cr, context=None):
        """ obsoleted in favour of _field_model2db
        """
        raise RuntimeError("Who called this?")

    def _auto_init_prefetch(self, schema, context=None):
        """ Populate schema.hints with names of objects to fetch from SQL

            This is a step before _field_model2db, _auto_init_sql, before any
            modifications to the DB. It only scans the model (recursively) to
            discover which tables we are interested to manipulate.
            Does also include referenced tables, through relational fields
        """

        # we do not need to register /our/ table here, we are not an sql model yet

        for name, c in self._columns.items():
            c._auto_init_prefetch(name, self, schema, context=context)

    def _field_model2db(self, cr,context=None):
        """ was _field_create(), stores the fields definitions in ir_model_fields table

            @param prefetch_schema a dict like { 'tables': [] ... } of db DDL elements
                that will need to be examined at the next step
            @return nothing
        """

        # TODO: perhaps put a struct column in ir.model.fields
        if context is None:
            context = {}
        cr.execute("SELECT id FROM ir_model WHERE model=%s", (self._name,), debug=self._debug)
        if not cr.rowcount:
            cr.execute("INSERT INTO ir_model (model, name, info, state) "
                        "VALUES (%s, %s, %s, %s) "
                        "RETURNING id",
                (self._name, self._description, self.__doc__, 'base'),
                debug=self._debug)
            model_id = cr.fetchone()[0]
        else:
            model_id = cr.fetchone()[0]
        if self._debug:
            _logger.debug("Field create for %s.%s", context.get('module','<module>'), self._name)

        if 'module' in context:
            name_id = 'model_'+self._name.replace('.','_')
            cr.execute('SELECT id FROM ir_model_data '
                "WHERE name=%s AND model = 'ir.model' AND res_id=%s "
                " AND module=%s AND source = 'orm' ",
                (name_id, model_id, context['module']))

            # We do allow multiple modules to have references to the same model
            # through ir.model.data . This, however, would never break those
            # who belong to an earlier module, which now doesn't contain that
            # model. Almost harmless, because the reference will point to the
            # right model (BUT may behave different at next db installation!).
            if not cr.rowcount:
                cr.execute("INSERT INTO ir_model_data (name,date_init,date_update,module,model,res_id, source) VALUES (%s, now(), now(), %s, %s, %s, 'orm')", \
                    (name_id, context['module'], 'ir.model', model_id), debug=self._debug)

        cr.execute("SELECT * FROM ir_model_fields WHERE model=%s", (self._name,) ,
                        debug=self._debug)
        cols = {}
        for rec in cr.dictfetchall():
            cols[rec['name']] = rec

        new_imd_colnames = []
        for (k, f) in self._columns.items():
            vals = {
                'model_id': model_id,
                'model': self._name,
                'name': k,
                'field_description': f.string.replace("'", " "),
                'ttype': f._type,
                'relation': f._obj or '',
                'view_load': bool(f.view_load),
                'select_level': tools.ustr(f.select or 0),
                'readonly': bool(f.readonly),
                'required': bool(f.required),
                'selectable' : bool(f.selectable),
                'translate': bool(f.translate),
                'relation_field': (f._type=='one2many' and isinstance(f,fields.one2many)) and f._fields_id or '',
            }
            # When its a custom field,it does not contain f.select
            if context.get('field_state', 'base') == 'manual':
                if context.get('field_name', '') == k:
                    vals['select_level'] = context.get('select', '0')
                #setting value to let the problem NOT occur next time
                elif k in cols:
                    vals['select_level'] = cols[k]['select_level']

            if k not in cols:
                cr.execute("""INSERT INTO ir_model_fields (
                        model_id, model, name, field_description, ttype,
                        relation,view_load,state,select_level,relation_field, translate,
                        readonly, required)
                    VALUES ( %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s ) RETURNING id""",
                    ( vals['model_id'], vals['model'], vals['name'], vals['field_description'], vals['ttype'],
                     vals['relation'], vals['view_load'], 'base',
                    vals['select_level'],vals['relation_field'], vals['translate'],
                    vals['readonly'], vals['required']),
                    debug=self._debug)
                id = cr.fetchone()[0]
                vals['id'] = id
                new_imd_colnames.append(('field_' + self._table + '_' + k, id))
            else:
                if self._debug:
                    _logger.debug("Field %s.%s found in db", self._name, k)
                for key, val in vals.items():
                    if cols[k][key] != vals[key]:
                        if self._debug:
                            _logger.debug("Column %s[%s] differs: %r != %r", k, key, cols[k][key], vals[key])
                        cr.execute("UPDATE ir_model_fields SET "
                            "model_id=%s, field_description=%s, ttype=%s, relation=%s, "
                            "view_load=%s, select_level=%s, readonly=%s ,required=%s,  "
                            "selectable=%s, relation_field=%s, translate=%s "
                            " WHERE model=%s AND name=%s", 
                            ( vals['model_id'], vals['field_description'], vals['ttype'],
                                vals['relation'], 
                                vals['view_load'], vals['select_level'], vals['readonly'],vals['required'],
                                vals['selectable'],vals['relation_field'], vals['translate'],
                                vals['model'], vals['name'] ),
                                debug=self._debug)
                        # Don't check any more attributes, we're up-to-date now.
                        break

        if new_imd_colnames and 'module' in context:
            cr.execute("SELECT name FROM ir_model_data WHERE name = ANY(%s) AND module = %s AND source = 'orm' ",
                            ([name1 for name1, id in new_imd_colnames], context['module']),
                            debug=self._debug)
            existing_names = [ name1 for name1, in cr.fetchall()]
            
            for name1, id in new_imd_colnames:
                if name1 in existing_names:
                    continue
                cr.execute("INSERT INTO ir_model_data (name,date_init, module,model, res_id, source)"
                        " VALUES(%s, now(), %s, %s, %s, 'orm')", \
                        (name1, context['module'], 'ir.model.fields', id),
                        debug=self._debug )

        # Discovered that keeping the transaction short improves
        # performance, hence we can commit now:
        cr.commit()

    def _auto_init(self, cr, context=None):
        """ Deprecated initialization function of model -> db schema
        """
        pass

    _auto_init.deferrable = True #: magic attribute for init algorithm

    def _auto_init_sql(self, schema, context=None):
        """ Manipulate schema to match the model (python -> sql)

            In this class, no need to do anything, since we are not
            a database-stored model
            
            Note: this function does NOT take 'cr' as an argument, because
            it is not allowed to perform any DB operations. All of the
            previous information should have been loaded at the time of
            _auto_init_prefetch(), and all manipulations will happen when
            schema.commit_to_db() is called.
            That's why _field_model2db() is a separate function, it's logic
            cannot be covered here.
        """
        pass

    def _load_manual_fields(self, cr):
        """Performs initialization of manual fields. Called through __init__()
        """
        pass

    def __init__(self, cr):
        if not self._name and not hasattr(self, '_inherit'):
            name = type(self).__name__.split('.')[0]
            msg = "The class %s has to have a _name attribute" % name

            _logger.error(msg)
            raise except_orm('ValueError', msg )

        if not self._description:
            self._description = self._name
        if not self._table:
            self._table = self._name.replace('.', '_')
        self._debug = config.get_misc_db(cr.dbname, 'logging_orm', self._name, False)
        self._workflow = None

        # Keep statistics of fields access
        self._column_stats = orm_utils.ORM_stat_fields()

        # code for virtual functions:
        if not self._virtuals:
            self._virtuals = []
        for key, ffn in self.__class__.__dict__.items():
            # try to discover the '_virtual' attribute in this class
            # functions (note: an attribute wouldn't work for other
            # data types)
            if not callable(ffn):
                continue
            if hasattr(ffn, '_virtual') and ffn._virtual:
                self._virtuals.append(key)

        if self._virtuals:
            self._vtable = set(self._virtuals)

        if self._vtable and self._inherits:
            for pinh in self._inherits:
                pclass = self.pool.get(pinh)
                if pclass._vtable is False:
                    pclass._vtable = set()
                pclass._vtable.update(self._vtable)
                
                # pclass._debug = True
                # _logger.debug("Object %s is virtual because of %s", pclass._name, self._name)

    def browse(self, cr, uid, select, context=None, list_class=None, 
                fields_process=None, fields_only=FIELDS_ONLY_DEFAULT, cache=None):
        """Fetch records as objects allowing to use dot notation to browse fields and relations

        :param cr: database cursor
        :param user: current user id
        :param select: id or list of ids.
                        Can also be expression, like ``[(...), ...]`` ,
                        or ``[True,]`` for all records
        :param context: context arguments, like lang, time zone
        :rtype: object or list of objects requested

        :param cache: The parent's cache. Please ONLY use it when the caller is
            itself a browse object, and within a single transaction. If unsure,
            just don't use!
        """
        self._list_class = list_class or browse_record_list
        if cache is None:
            cache = {}
        # need to accepts ints and longs because ids coming from a method
        # launched by button in the interface have a type long...
        if isinstance(select, (int, long)):
            return browse_record(cr, uid, select, self, cache, context=context, list_class=self._list_class, fields_process=fields_process, fields_only=fields_only)
        elif isinstance(select, (browse_record, browse_record_list, browse_null)):
            return select
        elif isinstance(select, list):
            # since the loop below will create data[id] for each of the ids, 
            # the first time one of them is accessed, the whole dataset is
            # fetched there, in one go.
            if self._debug:
                _logger.debug("%s.browse(%s)" % (self._name, select))
            
            # tuple-in-list means expression.
            # TODO: this quick hack must be re-written to end up in one
            # real query, as one would expect.
            if len(select) and \
                ( isinstance(select[0], tuple) or select[0] is True):
                if select[0] is True:
                    select = []
                select = self.search(cr, uid, select, context=context)
                if self._debug:
                    _logger.debug('%s.browse_search( %s...)' % (self._name, select[:5]))
            
            return self._list_class([browse_record(cr, uid, id, self, cache, context=context, list_class=self._list_class, fields_process=fields_process, fields_only=fields_only) for id in select], context=context)
        else:
            return browse_null()

    def __export_row(self, cr, uid, row, fields, context=None):
        if context is None:
            context = {}

        def check_type(field_type):
            if field_type == 'float':
                return 0.0
            elif field_type == 'integer':
                return 0
            elif field_type == 'boolean':
                return False
            return ''

        def selection_field(in_field):
            col_obj = self.pool.get(in_field.keys()[0])
            if f[i] in col_obj._columns.keys():
                return  col_obj._columns[f[i]]
            elif f[i] in col_obj._inherits.keys():
                selection_field(col_obj._inherits)
            else:
                return False

        lines = []
        data = map(lambda x: '', range(len(fields)))
        done = []
        for fpos in range(len(fields)):
            f = fields[fpos]
            if f:
                r = row
                i = 0
                while i < len(f):
                    if f[i] == '.id':
                        r = r['id']
                    elif f[i] == 'id':
                        model_data = self.pool.get('ir.model.data')
                        data_ids = model_data.search(cr, uid, [('model','=',r._table_name), ('res_id','=',r['id']), ('source', 'in', ('orm', 'xml'))])
                        if len(data_ids):
                            d = model_data.read(cr, uid, data_ids, ['name', 'module'])[0]
                            if d['module']:
                                r = '%s.%s' % (d['module'],d['name'])
                            else:
                                r = d['name']
                        else:
                            break
                    else:
                        r = r[f[i]]
                        # To display external name of selection field when its exported
                        cols = False
                        if f[i] in self._columns.keys():
                            cols = self._columns[f[i]]
                        elif f[i] in self._inherit_fields.keys():
                            cols = selection_field(self._inherits)
                        if cols and cols._type == 'selection':
                            sel_list = cols.selection
                            if r and type(sel_list) == type([]):
                                r = [x[1] for x in sel_list if r==x[0]]
                                r = r and r[0] or False
                    if not r:
                        if f[i] in self._columns:
                            r = check_type(self._columns[f[i]]._type)
                        elif f[i] in self._inherit_fields:
                            r = check_type(self._inherit_fields[f[i]][2]._type)
                        if isinstance(r, browse_null):
                            r = ''
                        data[fpos] = r
                        break
                    if isinstance(r, (browse_record_list, list)):
                        first = True
                        fields2 = map(lambda x: (x[:i+1]==f[:i+1] and x[i+1:]) \
                                or [], fields)
                        if fields2 in done:
                            if [x for x in fields2 if x]:
                                break
                        done.append(fields2)
                        for row2 in r:
                            lines2 = self.__export_row(cr, uid, row2, fields2,
                                    context)
                            if first:
                                for fpos2 in range(len(fields)):
                                    if lines2 and lines2[0][fpos2]:
                                        data[fpos2] = lines2[0][fpos2]
                                if not data[fpos]:
                                    dt = ''
                                    for rr in r:
                                        name_relation = self.pool.get(rr._table_name)._rec_name
                                        if isinstance(rr[name_relation], browse_record):
                                            rr = rr[name_relation]
                                        rr_name = self.pool.get(rr._table_name).name_get(cr, uid, [rr.id], context=context)
                                        rr_name = rr_name and rr_name[0] and rr_name[0][1] or ''
                                        dt += tools.ustr(rr_name or '') + ','
                                    data[fpos] = dt[:-1]
                                    break
                                lines += lines2[1:]
                                first = False
                            else:
                                lines += lines2
                        break
                    i += 1
                if i == len(f):
                    if isinstance(r, browse_record):
                        r = self.pool.get(r._table_name).name_get(cr, uid, [r.id], context=context)
                        r = r and r[0] and r[0][1] or ''
                    if isinstance(r, browse_null):
                        r = ''
                    data[fpos] = tools.ustr(r or '')
        return [data] + lines

    def export_data(self, cr, uid, ids, fields_to_export, context=None):
        """
        Export fields for selected objects

        :param cr: database cursor
        :param uid: current user id
        :param ids: list of ids
        :param fields_to_export: list of fields
        :param context: context arguments, like lang, time zone
        :rtype: dictionary with a *datas* matrix

        This method is used when exporting data via client menu

        """
        if context is None:
            context = {}
        cols = self._columns.copy()
        for f in self._inherit_fields:
            cols.update({f: self._inherit_fields[f][2]})
        def fsplit(x):
            if x=='.id': return [x]
            return x.replace(':id','/id').replace('.id','/.id').split('/')
        fields_to_export = map(fsplit, fields_to_export)
        warning = ''
        warning_fields = []
        datas = []
        for row in self.browse(cr, uid, ids, context):
            datas += self.__export_row(cr, uid, row, fields_to_export, context)
        return {'datas': datas}

    def import_data(self, cr, uid, fields, datas, mode='init', current_module='', noupdate=False, context=None, filename=None):
        """ Import given data in given module

        :param cr: database cursor
        :param uid: current user id
        :param fields: list of fields
        :param data: data to import
        :param mode: 'init' or 'update' for record creation
        :param current_module: module name
        :param noupdate: flag for record creation
        :param context: context arguments, like lang, time zone,
        :param filename: optional file to store partial import state for recovery
        :rtype: tuple

        This method is used when importing data via client menu.

        Example of fields to import for a sale.order::

            .id,                         (=database_id)
            partner_id,                  (=name_search)
            order_line/.id,              (=database_id)
            order_line/name,
            order_line/product_id/id,    (=xml id)
            order_line/price_unit,
            order_line/product_uom_qty,
            order_line/product_uom/id    (=xml_id)
        """
        if context is None:
            context = {}
        def _replace_field(x):
            x = _import_id_re.sub(r'\1/.id', x)
            return x.replace(':id','/id').split('/')
        fields = map(_replace_field, fields)
        logger = logging.getLogger('orm.import')
        ir_model_data_obj = self.pool.get('ir.model.data')

        # mode: id (XML id) or .id (database id) or False for name_get
        def _get_id(model_name, id, current_module=False, mode='id'):
            if mode=='.id':
                id = int(id)
                obj_model = self.pool.get(model_name)
                dom = [('id', '=', id)]
                if obj_model._columns.get('active'):
                    dom.append(('active', 'in', ['True','False']))
                ids = obj_model.search(cr, uid, dom, context=context)
                if not len(ids):
                    raise Exception(_("Database ID doesn't exist: %s : %s") %(model_name, id))
            elif mode=='id':
                if '.' in id:
                    module, xml_id = id.rsplit('.', 1)
                else:
                    module, xml_id = current_module, id
                record_id = ir_model_data_obj._get_id(cr, uid, module, xml_id)
                ir_model_data = ir_model_data_obj.read(cr, uid, [record_id], ['res_id'], context=context)
                if not ir_model_data:
                    raise ValueError('No references to %s.%s' % (module, xml_id))
                id = ir_model_data[0]['res_id']
            else:
                obj_model = self.pool.get(model_name)
                ids = obj_model.name_search(cr, uid, id, operator='=', context=context)
                if not ids:
                    raise ValueError('No record found for %s' % (id,))
                id = ids[0][0]
            return id

        # IN:
        #   datas: a list of records, each record is defined by a list of values
        #   prefix: a list of prefix fields ['line_ids']
        #   position: the line to process, skip is False if it's the first line of the current record
        # OUT:
        #   (res, position, warning, res_id) with
        #     res: the record for the next line to process (including it's one2many)
        #     position: the new position for the next line
        #     res_id: the ID of the record if it's a modification
        def process_liness(self, datas, prefix, current_module, model_name, fields_def, position=0, skip=0):
            line = datas[position]
            row = {}
            warning = []
            data_res_id = False
            xml_id = False
            nbrmax = position+1

            done = {}
            for i in range(len(fields)):
                res = False
                if i >= len(line):
                    raise Exception(_('Please check that all your lines have %d columns.'
                        'Stopped around line %d having %d columns.') % \
                            (len(fields), position+2, len(line)))
                if not line[i]:
                    continue

                field = fields[i]
                if field[:len(prefix)] <> prefix \
                        or (not field[len(prefix):] ):
                    if line[i] and skip:
                        return False
                    continue

                # ID of the record using a XML ID
                if field[len(prefix)]=='id':
                    try:
                        data_res_id = _get_id(model_name, line[i], current_module, 'id')
                    except ValueError:
                        pass
                    xml_id = line[i]
                    continue

                # ID of the record using a database ID
                elif field[len(prefix)]=='.id':
                    data_res_id = _get_id(model_name, line[i], current_module, '.id')
                    continue

                # recursive call for getting children and returning [(0,0,{})] or [(1,ID,{})]
                if fields_def[field[len(prefix)]]['type']=='one2many':
                    if field[len(prefix)] in done:
                        continue
                    done[field[len(prefix)]] = True
                    relation_obj = self.pool.get(fields_def[field[len(prefix)]]['relation'])
                    newfd = relation_obj.fields_get(cr, uid, context=context)
                    pos = position
                    res = []
                    first = 0
                    while pos < len(datas):
                        res2 = process_liness(self, datas, prefix + [field[len(prefix)]], current_module, relation_obj._name, newfd, pos, first)
                        if not res2:
                            break
                        (newrow, pos, w2, data_res_id2, xml_id2) = res2
                        nbrmax = max(nbrmax, pos)
                        warning += w2
                        first += 1
                        if (not newrow) or not reduce(lambda x, y: x or y, newrow.values(), 0):
                            break
                        res.append( (data_res_id2 and 1 or 0, data_res_id2 or 0, newrow) )

                elif fields_def[field[len(prefix)]]['type']=='many2one':
                    relation = fields_def[field[len(prefix)]]['relation']
                    if len(field) == len(prefix)+1:
                        mode = False
                    else:
                        mode = field[len(prefix)+1]
                    res = _get_id(relation, line[i], current_module, mode)

                elif fields_def[field[len(prefix)]]['type']=='many2many':
                    relation = fields_def[field[len(prefix)]]['relation']
                    if len(field) == len(prefix)+1:
                        mode = False
                    else:
                        mode = field[len(prefix)+1]

                    # TODO: improve this by using csv.csv_reader
                    res = []
                    for db_id in line[i].split(config.get('csv_internal_sep')):
                        res.append( _get_id(relation, db_id, current_module, mode) )
                    res = [(6,0,res)]

                elif fields_def[field[len(prefix)]]['type'] == 'integer':
                    res = int(line[i])
                elif fields_def[field[len(prefix)]]['type'] == 'boolean':
                    res = line[i].lower() not in ('0', 'false', 'off')
                elif fields_def[field[len(prefix)]]['type'] == 'float':
                    res = float(line[i])
                elif fields_def[field[len(prefix)]]['type'] == 'selection':
                    for key, val in fields_def[field[len(prefix)]]['selection']:
                        if line[i] in [tools.ustr(key), tools.ustr(val)]:
                            res = key
                            break
                    if line[i] and not res:
                        logger.warning( _("key '%s' not found in selection field '%s'"),
                                        line[i], field[len(prefix)])
                        warning += [_("Key/value '%s' not found in selection field '%s'") % (line[i], field[len(prefix)])]
                else:
                    res = line[i] or False

                row[field[len(prefix)]] = res

            result = (row, nbrmax, warning, data_res_id, xml_id)
            return result

        fields_def = self.fields_get(cr, uid, context=context)

        if config.get('import_partial', False) and filename:
            data = pickle.load(file(config.get('import_partial')))

        position = 0
        while position<len(datas):
            res = {}

            (res, position, warning, res_id, xml_id) = \
                    process_liness(self, datas, [], current_module, self._name, fields_def, position=position)
            if len(warning):
                cr.rollback()
                return (-1, res, 'Line ' + str(position) +' : ' + '!\n'.join(warning), '')

            try:
                ir_model_data_obj._update(cr, uid, self._name,
                     current_module, res, mode=mode, xml_id=xml_id,
                     noupdate=noupdate, res_id=res_id, context=context)
            except Exception, e:
                return (-1, res, 'Line ' + str(position) +' : ' + tools.ustr(e), '')

            if config.get('import_partial', False) and filename and (not (position%100)):
                data = pickle.load(file(config.get('import_partial')))
                data[filename] = position
                pickle.dump(data, file(config.get('import_partial'),'wb'))
                if context.get('defer_parent_store_computation'):
                    self._parent_store_compute(cr)
                cr.commit()

        if context.get('defer_parent_store_computation'):
            self._parent_store_compute(cr)
        return (position, 0, 0, 0)

    def read(self, cr, user, ids, fields=None, context=None, load='_classic_read'):
        """
        Read records with given ids with the given fields

        :param cr: database cursor
        :param user: current user id
        :param ids: id or list of the ids of the records to read
        :param fields: optional list of field names to return (default: all fields would be returned)
        :type fields: list (example ['field_name_1', ...])
        :param context: optional context dictionary - it may contains keys for specifying certain options
                        like ``context_lang``, ``context_tz`` to alter the results of the call.
                        A special ``bin_size`` boolean flag may also be passed in the context to request the
                        value of all fields.binary columns to be returned as the size of the binary instead of its
                        contents. This can also be selectively overriden by passing a field-specific flag
                        in the form ``bin_size_XXX: True/False`` where ``XXX`` is the name of the field.
                        Note: The ``bin_size_XXX`` form is new in OpenERP v6.0.
        :return: list of dictionaries((dictionary per record asked)) with requested field values
        :rtype: [{‘name_of_the_field’: value, ...}, ...]
        :raise AccessError: * if user has no read rights on the requested object
                            * if user tries to bypass access rules for read on the requested object

        """
        raise NotImplementedError(_('The read method is not implemented on this object !'))

    def search_read(self, cr, user, domain, offset=0, limit=None, order=None, fields=None, context=None, load='_classic_read'):
        """
        Read records of the given search criteria and return the specified fields

        :param cr: database cursor
        :param user: current user id
        :param domain: expression to search with
        :param fields: optional list of field names to return (default: all fields would be returned)
        :type fields: list (example ['field_name_1', ...])
        :param context: optional context dictionary. See read()
        :return: list of dictionaries((dictionary per record asked)) with requested field values
        :rtype: [{‘name_of_the_field’: value, ...}, ...]
        :raise AccessError: * if user has no read rights on the requested object
                            * if user tries to bypass access rules for read on the requested object

        """
        ids = self.search(cr, user, domain, offset, limit, order, context)
        if ids:
            return self.read(cr, user, ids, fields, context, load)
        else:
            return []

    def get_invalid_fields(self, cr, uid):
        return list(self._invalids)

    def _validate(self, cr, uid, ids, context=None):
        context = context or {}
        lng = context.get('lang', False)
        trans = self.pool.get('ir.translation')
        error_msgs = []
        if getattr(self, '_function_field_browse', False):
            ids = self.browse(cr, uid, ids, context=context)
        for constraint in self._constraints:
            fun, msg, fields = constraint
            if not fun(self, cr, uid, ids):
                # Check presence of __call__ directly instead of using
                # callable() because it will be deprecated as of Python 3.0
                if hasattr(msg, '__call__'):
                    tmp_msg = msg(self, cr, uid, ids, context=context)
                    if isinstance(tmp_msg, tuple):
                        tmp_msg, params = tmp_msg
                        translated_msg = tmp_msg % params
                    else:
                        translated_msg = tmp_msg
                elif not lng:
                    translated_msg = msg
                else:
                    translated_msg = trans._get_source(cr, uid, self._name, 'constraint', lng, source=msg) or msg
                error_msgs.append(
                        _("Error occurred while validating the field(s) %s: %s") % (','.join(fields), translated_msg)
                )
                self._invalids.update(fields)
        if error_msgs:
            cr.rollback()
            # TODO: perhaps return the invalid fields here...
            raise except_orm('ValidateError', '\n'.join(error_msgs))
        else:
            # FIXME: invalid fields should be stored per session, if ever one
            self._invalids.clear()

    def default_get(self, cr, uid, fields_list, context=None):
        """
        Returns default values for the fields in fields_list.

        :param fields_list: list of fields to get the default values for (example ['field1', 'field2',])
        :type fields_list: list
        :param context: optional context dictionary - it may contains keys for specifying certain options
                        like ``context_lang`` (language) or ``context_tz`` (timezone) to alter the results of the call.
                        It may contain keys in the form ``default_XXX`` (where XXX is a field name), to set
                        or override a default value for a field.
                        A special ``bin_size`` boolean flag may also be passed in the context to request the
                        value of all fields.binary columns to be returned as the size of the binary instead of its
                        contents. This can also be selectively overriden by passing a field-specific flag
                        in the form ``bin_size_XXX: True/False`` where ``XXX`` is the name of the field.
                        Note: The ``bin_size_XXX`` form is new in OpenERP v6.0.
                        If the ``__ignore_ir_values`` is passed and is positive, 
                        defaults will NOT be looked up in ir.values
        :return: dictionary of the default values (set on the object model class, through user preferences, or in the context)

        `default_get()` returns values as in a `create()` or `write()` call, NOT like the
        result of `read()` !  This means that many2one fields, for example, will be ID only,
        not a (ID, name) pair.

        """
        # trigger view init hook
        self.view_init(cr, uid, fields_list, context)

        if not context:
            context = {}
        defaults = {}

        # Most significant: get the default values from the context
        for f in fields_list:
            if ('default_' + f) in context:
                defaults[f] = context['default_'  + f]

        # Next significant:
        # get the default values set by the user and override the default
        # values defined in the object
        ir_values_obj = self.pool.get('ir.values')
        if context.get('__ignore_ir_values', False):
            res = []
        else:
            res = ir_values_obj.get(cr, uid, 'default', False, [self._name])
        
        for id, field, field_value in res:
            if field in defaults or (field not in fields_list):
                continue

            fld_def = (field in self._columns) and self._columns[field] or self._inherit_fields[field][2]
            if fld_def._type in ('many2one', 'one2one'):
                obj = self.pool.get(fld_def._obj)
                if not obj.search(cr, uid, [('id', '=', field_value or False)]):
                    continue
            if fld_def._type in ('many2many'):
                obj = self.pool.get(fld_def._obj)
                # make sure all the values exist
                # Side effect: this will also sort them!
                field_value2 = obj.search(cr, uid, [('id', 'in', field_value)])
                field_value = field_value2
            if fld_def._type in ('one2many'):
                obj = self.pool.get(fld_def._obj)
                field_value2 = []
                for i in range(len(field_value)):
                    field_value2.append({})
                    for field2 in field_value[i]:
                        if field2 in obj._columns.keys() and obj._columns[field2]._type in ('many2one', 'one2one'):
                            obj2 = self.pool.get(obj._columns[field2]._obj)
                            if not obj2.search(cr, uid,
                                    [('id', '=', field_value[i][field2])]):
                                continue
                        elif field2 in obj._inherit_fields.keys() and obj._inherit_fields[field2][2]._type in ('many2one', 'one2one'):
                            obj2 = self.pool.get(obj._inherit_fields[field2][2]._obj)
                            if not obj2.search(cr, uid,
                                    [('id', '=', field_value[i][field2])]):
                                continue
                        # TODO add test for many2many and one2many
                        field_value2[i][field2] = field_value[i][field2]
                field_value = field_value2
            defaults[field] = field_value

        # Next method: get the default values defined in the object
        defaults_props = {} # will take last-resort values
        for f in fields_list:
            if f in defaults:
                continue
            if f in self._defaults:
                if callable(self._defaults[f]):
                    defaults[f] = self._defaults[f](self, cr, uid, context)
                else:
                    defaults[f] = self._defaults[f]

            fld_def = ((f in self._columns) and self._columns[f]) \
                    or ((f in self._inherit_fields) and self._inherit_fields[f][2]) \
                    or False

            if isinstance(fld_def, fields.property):
                property_obj = self.pool.get('ir.property')
                prop_value = property_obj.get(cr, uid, f, self._name, context=context)
                if prop_value:
                    if isinstance(prop_value, (browse_record, browse_null)):
                        defaults[f] = prop_value.id
                    else:
                        defaults[f] = prop_value
                else:
                    if f not in defaults:
                        defaults_props[f] = False

        remaining_fields = [ f for f in fields_list if f not in defaults ]

        if remaining_fields:
            # get the default values for the inherited fields
            for t in self._inherits.keys():
                defaults.update(self.pool.get(t).default_get(cr, uid, remaining_fields,
                    context))

        if defaults_props:
            # must be updated after the _inherits.keys() lookup
            defaults.update(defaults_props)

        return defaults


    def perm_read(self, cr, user, ids, context=None, details=True):
        raise NotImplementedError(_('The perm_read method is not implemented on this object !'))

    def unlink(self, cr, uid, ids, context=None):
        raise NotImplementedError(_('The unlink method is not implemented on this object !'))

    def write(self, cr, user, ids, vals, context=None):
        raise NotImplementedError(_('The write method is not implemented on this object !'))

    def create(self, cr, user, vals, context=None):
        raise NotImplementedError(_('The create method is not implemented on this object !'))

    def fields_get_keys(self, cr, user, context=None):
        res = self._columns.keys()
        for parent in self._inherits:
            res.extend(self.pool.get(parent).fields_get_keys(cr, user, context))
        return res

    # returns the definition of each field in the object
    # the optional fields parameter can limit the result to some fields
    def fields_get(self, cr, user, allfields=None, context=None, write_access=True):
        if context is None:
            context = {}
        res = {}
        translation_obj = self.pool.get('ir.translation')
        for parent in self._inherits:
            res.update(self.pool.get(parent).fields_get(cr, user, allfields, context))

        all_selectable = False
        if getattr(self, '_fallback_search', False) == True:
            all_selectable = True

        if self._columns.keys():
            for f in self._columns.keys():
                field_col = self._columns[f]
                if allfields and f not in allfields:
                    continue
                res[f] = {'type': field_col._type}
                field_col._get_field_def(cr, user, f, self, res[f], context=context)

                if not write_access:
                    res[f]['readonly'] = True
                    res[f]['states'] = {}

                if all_selectable:
                    res[f]['selectable'] = True
        
            # Now, collectively translate the fields' strings and help:
            fld_list = []
            
            for f in res.keys():
                if 'string' in res[f]:
                    fld_list.append((f, 'field'))
                if 'help' in res[f]:
                    fld_list.append((f, 'help'))
            
            if context.get('lang', False):
                res_trans = translation_obj._get_multifield(cr, user, fld_list,
                               lang=context['lang'], prepend=self._name+',')
                for f, attr, val in res_trans:
                    if attr == 'field':
                        res[f]['string'] = val
                    else:
                        res[f][attr] = val
        else:
            #TODO : read the fields from the database
            pass

        if allfields:
            # filter out fields which aren't in the fields list
            for r in res.keys():
                if r not in allfields:
                    del res[r]
        return res

    #
    # Overload this method if you need a window title which depends on the context
    #
    def view_header_get(self, cr, user, view_id=None, view_type='form', context=None):
        return False

    def __view_look_dom(self, cr, user, node, view_id, context=None):
        """Examine the DOM of a view and find the fields, attributes
           @return a dict of fields, with their attributes.
        """
        if not context:
            context = {}
        result = False
        fields = {}
        children = True

        def encode(s):
            #if isinstance(s, unicode):
            #    return s.encode('utf8')
            return s

        # return True if node can be displayed to current user
        def check_group(node):
            if node.get('groups'):
                groups = node.get('groups').split(',')
                access_pool = self.pool.get('ir.model.access')
                can_see = access_pool.check_groups(cr, user, groups)
                if not can_see:
                    node.set('invisible', '1')
                    if 'attrs' in node.attrib:
                        del(node.attrib['attrs']) #avoid making field visible later
                del(node.attrib['groups'])
                return can_see
            else:
                return True

        if node.tag in ('field', 'node', 'arrow'):
            if node.get('object'):
                attrs = {}
                views = {}
                new_xml = etree.fromstring('<form/>')
                xml = "<form>"
                for f in node:
                    if f.tag in ('field'):
                        new_xml.append(f)
                ctx = context.copy()
                ctx['base_model_name'] = self._name
                xarch, xfields = self.pool.get(node.get('object')).__view_look_dom_arch(cr, user, new_xml, view_id, ctx)
                views['form'] = {
                    'arch': xarch,
                    'fields': xfields
                }
                attrs = {'views': views}
                fields = xfields
            node_name = node.get('name')
            if node_name:
                attrs = {}
                try:
                    if node_name in self._columns:
                        column = self._columns[node_name]
                    elif node_name in self._inherit_fields:
                        column = self._inherit_fields[node_name][2]
                    else:
                        column = False
                except Exception:
                    column = False

                if column:
                    relation = self.pool.get(column._obj)

                    children = False
                    views = {}
                    if relation and node.get('view_ids'):
                        iview_ids = [int(x.strip()) for x in node.get('view_ids').split(',')]
                        for iview_id in iview_ids:
                            ires = relation.fields_view_get(cr, user, iview_id, context=context, toolbar=False, submenu=False)
                            views[ires['type']] = { 'arch': ires['arch'], 'fields': ires['fields']}
                    for f in node:
                        if f.tag in ('form', 'tree', 'graph'): # TODO: expand ;)
                            # That's a nested view, lookup its fields, too
                            if not relation:
                                _logger.error("Cannot find nested object %r "
                                            "for view #%d , at node <%s %r>",
                                            column._obj, view_id, node.tag, node_name)
                                raise ValueError("View %d: cannot locate %r for <%s %r>" % (view_id, column._obj, node.tag, node_name))
                            node.remove(f)
                            ctx = context.copy()
                            ctx['base_model_name'] = self._name
                            xarch, xfields = relation.__view_look_dom_arch(cr, user, f, view_id, ctx)
                            views[str(f.tag)] = {
                                'arch': xarch,
                                'fields': xfields
                            }
                    attrs = {'views': views}
                    if node.get('widget') == 'selection' and node.get('selection'):
                        # If we explicitly give the selection in the view xml, use that
                        try:
                                attrs['selection'] = eval(node.get('selection','[]'), {'uid':user, 'time':time})
                        except Exception, e:
                                _logger.error("Exception %s For domain %s" %(e, node.get('domain')))
                                raise
                    elif node.get('widget') == 'selection':
                        # Prepare the cached selection list for the client. This needs to be
                        # done even when the field is invisible to the current user, because
                        # other events could need to change its value to any of the selectable ones
                        # (such as on_change events, refreshes, etc.)

                        # If domain and context are strings, we keep them for client-side, otherwise
                        # we evaluate them server-side to consider them when generating the list of
                        # possible values
                        # TODO: find a way to remove this hack, by allow dynamic domains
                        dom = []
                        if column._domain and not isinstance(column._domain, basestring):
                            dom = column._domain
                        try:
                                dom += eval(node.get('domain','[]'), {'uid':user, 'time':time})
                        except Exception, e:
                                _logger.error("Exception %s For domain %s" %(e, node.get('domain')))
                                raise

                        search_context = dict(context)
                        if column._context and not isinstance(column._context, basestring):
                            search_context.update(column._context)
                        attrs['selection'] = relation._name_search(cr, user, '', dom, context=search_context, limit=None, name_get_uid=1)
                        if (node.get('required') and not int(node.get('required'))) or not column.required:
                            attrs['selection'].append((False,''))
                fields[node_name] = attrs

        elif node.tag in ('form', 'tree'):
            result = self.view_header_get(cr, user, False, node.tag, context)
            if result:
                node.set('string', result)

        elif node.tag == 'calendar':
            for additional_field in ('date_start', 'date_delay', 'date_stop', 'color'):
                if node.get(additional_field):
                    fields[node.get(additional_field)] = {}

        if 'groups' in node.attrib:
            check_group(node)

        # translate view
        if ('lang' in context) and not result:
            if node.get('string'):
                trans = self.pool.get('ir.translation')._get_source(cr, user, self._name, 'view', context['lang'], node.get('string'))
                if trans == node.get('string') and ('base_model_name' in context):
                    # If translation is same as source, perhaps we'd have more luck with the alternative model name
                    # (in case we are in a mixed situation, such as an inherited view where parent_view.model != model
                    trans = self.pool.get('ir.translation')._get_source(cr, user, context['base_model_name'], 'view', context['lang'], node.get('string'))
                if trans:
                    node.set('string', trans)
            if node.get('confirm'):
                trans = self.pool.get('ir.translation')._get_source(cr, user, self._name, 'view', context['lang'], node.get('confirm'))
                if trans:
                    node.set('confirm', trans)
            if node.get('sum'):
                trans = self.pool.get('ir.translation')._get_source(cr, user, self._name, 'view', context['lang'], node.get('sum'))
                if trans:
                    node.set('sum', trans)
            if node.get('help'):
                trans = self.pool.get('ir.translation')._get_source(cr, user, self._name, 'view', context['lang'], node.get('help'))
                if trans:
                    node.set('help', trans)

        for f in node:
            if children or (node.tag == 'field' and f.tag in ('filter','separator')):
                fields.update(self.__view_look_dom(cr, user, f, view_id, context))

        return fields

    def _disable_workflow_buttons(self, cr, user, node):
        if user == 1:
            # admin user can always activate workflow buttons
            return node

        # TODO handle the case of more than one workflow for a model or multiple
        # transitions with different groups and same signal
        usersobj = self.pool.get('res.users')
        buttons = (n for n in node.getiterator('button') if n.get('type') != 'object')
        for button in buttons:
            user_groups = usersobj.read(cr, user, [user], ['groups_id'])[0]['groups_id']
            cr.execute_prepared('orm_disable_wkf_buttons', """SELECT DISTINCT t.group_id
                        FROM wkf
                  INNER JOIN wkf_activity a ON a.wkf_id = wkf.id
                  INNER JOIN wkf_transition t ON (t.act_to = a.id)
                       WHERE wkf.osv = %s
                         AND t.signal = %s
                         AND t.group_id is NOT NULL
                  """, (self._name, button.get('name'),), debug=self._debug)
            group_ids = [x[0] for x in cr.fetchall() if x[0]]
            can_click = not group_ids or bool(set(user_groups).intersection(group_ids))
            button.set('readonly', str(int(not can_click)))
        return node

    def __view_look_dom_arch(self, cr, user, node, view_id, context=None):
        if self._debug:
            _logger.debug('%s.view_look_dom_arch(.., view_id=%r, {lang=%s}', 
                            self._name, view_id, context and context.get('lang',None))
        fields_def = self.__view_look_dom(cr, user, node, view_id, context=context)
        node = self._disable_workflow_buttons(cr, user, node)
        arch = etree.tostring(node, encoding="utf-8").replace('\t', '')
        fields = {}
        if node.tag == 'diagram':
            if node.getchildren()[0].tag == 'node':
                node_fields = self.pool.get(node.getchildren()[0].get('object')).fields_get(cr, user, fields_def.keys(), context)
            if node.getchildren()[1].tag == 'arrow':
                arrow_fields = self.pool.get(node.getchildren()[1].get('object')).fields_get(cr, user, fields_def.keys(), context)
            for key, value in node_fields.items():
                fields[key] = value
            for key, value in arrow_fields.items():
                fields[key] = value
        else:
            fields = self.fields_get(cr, user, fields_def.keys(), context)
        for field in fields_def:
            if field == 'id':
                # sometime, the view may contain the (invisible) field 'id' needed for a domain (when 2 objects have cross references)
                fields['id'] = {'readonly': True, 'type': 'integer', 'string': 'ID'}
            elif field in fields:
                fields[field].update(fields_def[field])
            elif view_id is False:
                msg = _("Can't find view for field '%s' in view parts of object model '%s':"
                        "\nPlease define some view for that model.") % \
                        (field, self._name)
                _logger.error(msg)
                raise except_orm('View error', msg)
            else:
                # we don't ask ir_model_data, but do queries in both cases, so
                # that code looks symmetric.
                cr.execute( "SELECT module || '.' || name FROM ir_model_data "
                            "WHERE model = 'ir.ui.view' AND res_id = %s AND source='xml' AND res_id != 0",
                            (view_id,), debug=self._debug)
                ref_res = cr.fetchone()
                if ref_res:
                    view_revref = ', ref "%s"' % ref_res[0]
                else:
                    view_revref = ''
                
                cr.execute('SELECT iv.name, iv.model, iv.id, '
                           " COALESCE(md.module || '.' || md.name, '') AS ref_name "
                           'FROM ir_ui_view AS iv LEFT JOIN ir_model_data AS md '
                           " ON (iv.id = md.res_id AND md.model = 'ir.ui.view' AND md.source='xml')"
                            'WHERE (iv.id=%s OR iv.inherit_id=%s) AND iv.arch LIKE %s',
                            (view_id, view_id, '%%%s%%' % field), 
                            debug=self._debug)
                res = cr.fetchall()[:]
                
                if res:
                    parts = ''
                    for r in res:
                        parts += _('\n %s for %s (id: %d %s)') % tuple(r)
                else:
                    parts = _('\n <no view found>')
                msg = _("Can't find field '%s' in the following view parts composing the view #%d%s of object model '%s':"
                        "\n %s\n "
                        "\nEither you wrongly customized this view, or some modules bringing those views are not compatible with your current data model.") % \
                        (field, view_id, view_revref, self._name,
                         parts)
                _logger.error(msg)
                raise except_orm('View error', msg)
        return arch, fields

    #
    # if view_id, view_type is not required
    #
    def fields_view_get(self, cr, user, view_id=None, view_type='form', context=None, toolbar=False, submenu=False):
        """
        Get the detailed composition of the requested view like fields, model, view architecture

        :param cr: database cursor
        :param user: current user id
        :param view_id: id of the view or None
        :param view_type: type of the view to return if view_id is None ('form', tree', ...)
        :param context: context arguments, like lang, time zone
        :param toolbar: true to include contextual actions
        :param submenu: example (portal_project module)
        :return: dictionary describing the composition of the requested view (including inherited views and extensions)
        :raise AttributeError:
                            * if the inherited view has unknown position to work with other than 'before', 'after', 'inside', 'replace'
                            * if some tag other than 'position' is found in parent view
        :raise Invalid ArchitectureError: if there is view type other than form, tree, calendar, search etc defined on the structure

        """
        if not context:
            context = {}

        def _inherit_apply(src, inherit, base_id=0, apply_id=0):
            # FIXME: it would be interesting if moving this fn() out of the parent
            # fields_view_get() has any performance impact.
            # It is not trivial, though, as (self, cr, uid, .. context) need to be
            # passed, to the expense of a detached version.
            def _find(node, node2):
                if node2.tag == 'xpath':
                    res = node.xpath(node2.get('expr'))
                    if res:
                        return res[0]
                    else:
                        return None
                else:
                    for n in node.getiterator(node2.tag):
                        res = True
                        if node2.tag == 'field':
                            # only compare field names, a field can be only once in a given view
                            # at a given level (and for multilevel expressions, we should use xpath
                            # inheritance spec anyway)
                            if node2.get('name') == n.get('name'):
                                return n
                            else:
                                continue
                        for attr in node2.attrib:
                            if attr == 'position':
                                continue
                            if n.get(attr):
                                if n.get(attr) == node2.get(attr):
                                    continue
                            res = False
                        if res:
                            return n
                return None

            # End: _find(node, node2)

            doc_dest = etree.fromstring(inherit)
            toparse = [ doc_dest ]

            while len(toparse):
                node2 = toparse.pop(0)
                if isinstance(node2, SKIPPED_ELEMENT_TYPES):
                    continue
                if node2.tag == 'data':
                    toparse += [ c for c in doc_dest ]
                    continue
                node = _find(src, node2)
                if node is not None:
                    pos = 'inside'
                    if node2.get('position'):
                        pos = node2.get('position')
                    if pos == 'replace':
                        parent = node.getparent()
                        if parent is None:
                            src = copy.deepcopy(node2[0])
                        else:
                            for child in node2:
                                node.addprevious(child)
                            node.getparent().remove(node)
                    elif pos == 'attributes':
                        for child in node2.getiterator('attribute'):
                            attribute = (child.get('name'), child.text and child.text.encode('utf8') or None)
                            if attribute[1]:
                                node.set(attribute[0], attribute[1])
                            else:
                                del(node.attrib[attribute[0]])
                    else:
                        sib = node.getnext()
                        for child in node2:
                            if pos == 'inside':
                                node.append(child)
                            elif pos == 'after':
                                if sib is None:
                                    node.addnext(child)
                                    node = child
                                else:
                                    sib.addprevious(child)
                            elif pos == 'before':
                                node.addprevious(child)
                            else:
                                raise AttributeError(_('Unknown position "%s" in inherited view %s !') % \
                                        (pos, apply_id))
                else:
                    attrs = ''.join([
                        ' %s="%s"' % (attr, node2.get(attr))
                        for attr in node2.attrib
                        if attr != 'position'
                    ])
                    tag = "<%s%s>" % (node2.tag, attrs)
                    rr_base = ''
                    rr_apply = ''
                    try:
                        # Attempt to resolve the ids into ref names.
                        imd = self.pool.get('ir.model.data')
                        rres_base = imd.get_rev_ref(cr, user, 'ir.ui.view', base_id)
                        if rres_base and rres_base[1]:
                            rr_base = ', '.join(rres_base[1])
                        
                        rres_apply = imd.get_rev_ref(cr, user, 'ir.ui.view', apply_id)
                        if rres_apply and rres_apply[1]:
                            rr_apply = ', '.join(rres_apply[1])
                    except Exception, e:
                        _logger.debug("Rev ref exception: %s" % e)
                        # but pass, anyway..
                    
                    raise AttributeError(_("Couldn't find tag '%s' of #%d %s in parent view %s %s!") % \
                        (tag, apply_id, rr_apply, base_id, rr_base))
            return src
        # End: _inherit_apply(src, inherit)

        result = {'type': view_type, 'model': self._name}

        parent_view_model = None
        view_ref = context.get(view_type + '_view_ref', False)
        if self._debug:
            logging.getLogger('orm').debug("Getting %s view %r for %s.", 
                    view_type, view_id or view_ref, self._name)

        if view_ref and (not view_id) and '.' in view_ref:
            module, view_ref = view_ref.split('.', 1)
            cr.execute("SELECT res_id FROM ir_model_data "
                        "WHERE model='ir.ui.view' AND module=%s "
                        " AND source = 'xml' AND res_id != 0 "
                        " AND name=%s", (module, view_ref),
                        debug=self._debug)
            view_ref_res = cr.fetchone()
            if view_ref_res:
                view_id = view_ref_res[0]

        ok = (cr.pgmode < 'pg84')
        model = True
        sql_res = False
        while ok:
            if view_id:
                query = "SELECT arch,name,field_parent,id,type,inherit_id,model FROM ir_ui_view WHERE id=%s"
                params = (view_id,)
                if model:
                    query += " AND model=%s"
                    params += (self._name,)
                cr.execute(query, params, debug=self._debug)
            else:
                cr.execute('''SELECT arch,name,field_parent,id,type,inherit_id, model
                    FROM ir_ui_view
                    WHERE model=%s AND type=%s AND inherit_id IS NULL
                    ORDER BY priority''', (self._name, view_type), 
                    debug=self._debug)
            sql_res = cr.fetchone()

            if not sql_res:
                break

            ok = sql_res[5]
            view_id = ok or sql_res[3]
            model = False
            parent_view_model = sql_res[6]

        if sql_res:
            # if a view was found in non-pg84 mode
            result['type'] = sql_res[4]
            result['view_id'] = sql_res[3]
            result['arch'] = sql_res[0]

            def _inherit_apply_rec(result, inherit_id):
                # get all views which inherit from (ie modify) this view
                cr.execute('SELECT arch,id FROM ir_ui_view '
                        'WHERE inherit_id=%s AND model=%s ORDER BY PRIORITY',
                        (inherit_id, self._name), debug=self._debug)
                sql_inherit = cr.fetchall()
                for (inherit, id) in sql_inherit:
                    result = _inherit_apply(result, inherit, inherit_id, id)
                    result = _inherit_apply_rec(result, id)
                return result

            inherit_result = etree.fromstring(result['arch'])
            result['arch'] = _inherit_apply_rec(inherit_result, sql_res[3])

            result['name'] = sql_res[1]
            result['field_parent'] = sql_res[2] or False

        if cr.pgmode >= 'pg84':
            if view_id:
                # If we had been asked for some particular view id, we have to
                # recursively select the views down to the base one that view_id
                # inherits from
                sql_in = 'WITH RECURSIVE rcrs_view_in(id, inher, model) AS (' \
                        'SELECT id, inherit_id, model FROM ir_ui_view ' \
                                'WHERE id = %s  AND model = %s'  \
                        ' UNION ALL SELECT irv.id, irv.inherit_id, irv.model ' \
                                ' FROM ir_ui_view AS irv, rcrs_view_in AS rcv ' \
                                ' WHERE irv.id = rcv.inher ' \
                        ') ' \
                        ' SELECT id FROM rcrs_view_in ' \
                        ' WHERE inher IS NULL LIMIT 1'
                sql_in_parms = (view_id, self._name, self._name)
            else:
                sql_in = 'SELECT id FROM ir_ui_view ' \
                        'WHERE model=%s AND type=%s AND inherit_id IS NULL '\
                        'ORDER BY priority LIMIT 1'
                sql_in_parms = (self._name, view_type, self._name)
        
            sql_out = '''WITH RECURSIVE rec_view(arch,name,field_parent,id,type,
                                        inherit_id, priority, model, path)
                  AS ( SELECT arch,name,field_parent,id,type,
                                inherit_id, priority, model, ARRAY[] :: integer[] AS path
                            FROM ir_ui_view
                            WHERE id IN ( %s )
                        
                        UNION ALL SELECT v.arch,v.name,v.field_parent,v.id,v.type,
                                v.inherit_id, v.priority, v.model, rec_view.path || v.inherit_id
                            FROM ir_ui_view v, rec_view
                            WHERE v.inherit_id = rec_view.id
                              AND v.model = %%s
                     )
                  SELECT arch, name, field_parent, id, type, inherit_id, model
                      FROM rec_view ORDER BY path, priority ;
                  ''' % sql_in
                
            cr.execute(sql_out, sql_in_parms, debug=self._debug)
            last_res = [] # list of views already applied
            
            for res in cr.fetchall():
                if not last_res:   # first, non-inheriting view
                    sql_res = True
                    result['arch'] = etree.fromstring(res[0])
                    result['name'] = res[1]
                    result['field_parent'] = res[2] or False
                    result['view_id'] = res[3]
                    view_id = res[3]
                    result['type'] = res[4]
                    last_res = [res[3],]
                    parent_view_model = res[6]
                elif not (res[5] and res[5] in last_res):
                    _logger.warning("Cannot apply view %d because it inherits from %d, not in %s" % \
                                (res[3], res[5], last_res))
                    # non-fatal, carry on
                else:
                        result['arch'] = _inherit_apply(result['arch'], res[0], res[5], res[3])
                        last_res.append(res[3])

        if not sql_res:
            # otherwise, build some kind of default view
            try:
                result['arch'] = oo_view[view_type].default_view(cr, user, self, context=context)
            except KeyError:
                raise except_orm(_('Invalid Architecture!'), _("There is no view of type '%s' defined for the structure!") % view_type)
            result['name'] = 'default'
            result['field_parent'] = False
            result['view_id'] = 0

        if parent_view_model != self._name:
            ctx = context.copy()
            ctx['base_model_name'] = parent_view_model
        else:
            ctx = context
        xarch, xfields = self.__view_look_dom_arch(cr, user, result['arch'], view_id, context=ctx)
        result['arch'] = xarch
        result['fields'] = xfields

        if submenu:
            if context and context.get('active_id', False):
                data_menu = self.pool.get('ir.ui.menu').browse(cr, user, context['active_id'], context).action
                if data_menu:
                    act_id = data_menu.id
                    if act_id:
                        data_action = self.pool.get('ir.actions.act_window').browse(cr, user, [act_id], context)[0]
                        result['submenu'] = getattr(data_action, 'menus', False)
        if toolbar:
            def clean(x):
                x = x[2]
                for key in ('report_sxw_content', 'report_rml_content',
                        'report_sxw', 'report_rml',
                        'report_sxw_content_data', 'report_rml_content_data'):
                    if key in x:
                        del x[key]
                return x
            ir_values_obj = self.pool.get('ir.values')
            resprint = ir_values_obj.get(cr, user, 'action',
                    'client_print_multi', [(self._name, False)], False,
                    context)
            resaction = ir_values_obj.get(cr, user, 'action',
                    'client_action_multi', [(self._name, False)], False,
                    context)

            resrelate = ir_values_obj.get(cr, user, 'action',
                    'client_action_relate', [(self._name, False)], False,
                    context)

            if self._debug:
                if resprint:
                    logging.getLogger('orm').debug('%s: client_print_multi actions: %r', self._name,
                            [ '%s: %s' % (x[0], x[1]) for x in resprint])
                if resaction:
                    logging.getLogger('orm').debug('%s: client_action_multi actions: %r', self._name,
                            [ '%s: %s' % (x[0], x[1]) for x in resaction])
                if resrelate:
                    logging.getLogger('orm').debug('%s: client_action_relate actions: %r', self._name,
                            [ '%s: %s' % (x[0], x[1]) for x in resrelate])
            resprint = map(clean, resprint)
            resaction = map(clean, resaction)
            resaction = filter(lambda x: not x.get('multi', False), resaction)
            resprint = filter(lambda x: not x.get('multi', False), resprint)
            resrelate = map(itemgetter(2), resrelate)

            for x in resprint + resaction + resrelate:
                x['string'] = x['name']

            result['toolbar'] = {
                'print': resprint,
                'action': resaction,
                'relate': resrelate
            }
        return result

    _view_look_dom_arch = __view_look_dom_arch

    def search_count(self, cr, user, args, context=None):
        """ Old-style API equvalent to self.search(..., count=True)

            Returns the number of db records matching the criteria.
        """
        if not context:
            context = {}
        res = self.search(cr, user, args, context=context, count=True)
        if isinstance(res, list):
            return len(res)
        return res

    def search(self, cr, user, args, offset=0, limit=None, order=None, context=None, count=False):
        """
        Search for records based on a search domain.

        :param cr: database cursor
        :param user: current user id
        :param args: list of tuples specifying the search domain [('field_name', 'operator', value), ...]. Pass an empty list to match all records.
        :param offset: optional number of results to skip in the returned values (default: 0)
        :param limit: optional max number of records to return (default: **None**)
        :param order: optional columns to sort by (default: self._order=id )
        :param context: optional context arguments, like lang, time zone
        :type context: dictionary
        :param count: optional (default: **False**), if **True**, returns only the number of records matching the criteria, not their ids
        :return: id or list of ids of records matching the criteria
        :rtype: integer or list of integers
        :raise AccessError: * if user tries to bypass access rules for read on the requested object.

        **Expressing a search domain (args)**

        Each tuple in the search domain needs to have 3 elements, in the form: **('field_name', 'operator', value)**, where:

            * **field_name** must be a valid name of field of the object model, possibly following many-to-one relationships using dot-notation, e.g 'street' or 'partner_id.country' are valid values.
            * **operator** must be a string with a valid comparison operator from this list: ``=, !=, >, >=, <, <=, like, ilike, in, not in, child_of, parent_left, parent_right``
              The semantics of most of these operators are obvious.
              The ``child_of`` operator will look for records who are children or grand-children of a given record,
              according to the semantics of this model (i.e following the relationship field named by
              ``self._parent_name``, by default ``parent_id``.
            * **value** must be a valid value to compare with the values of **field_name**, depending on its type.

        Domain criteria can be combined using 3 logical operators than can be added between tuples:  '**&**' (logical AND, default), '**|**' (logical OR), '**!**' (logical NOT).
        These are **prefix** operators and the arity of the '**&**' and '**|**' operator is 2, while the arity of the '**!**' is just 1.
        Be very careful about this when you combine them the first time.

        Here is an example of searching for Partners named *ABC* from Belgium and Germany whose language is not english ::

            [('name','=','ABC'),'!',('language.code','=','en_US'),'|',('country_id.code','=','be'),('country_id.code','=','de'))

        The '&' is omitted as it is the default, and of course we could have used '!=' for the language, but what this domain really represents is::

            (name is 'ABC' AND (language is NOT english) AND (country is Belgium OR Germany))

        """
        return self._search(cr, user, args, offset=offset, limit=limit, order=order, context=context, count=count)

    def _search(self, cr, user, args, offset=0, limit=None, order=None, context=None, count=False, access_rights_uid=None):
        """
        Private implementation of search() method, allowing specifying the uid to use for the access right check.
        This is useful for example when filling in the selection list for a drop-down and avoiding access rights errors,
        by specifying ``access_rights_uid=1`` to bypass access rights check, but not ir.rules!

        :param access_rights_uid: optional user ID to use when checking access rights
                                  (not for ir.rules, this is only for ir.model.access)
        """
        raise NotImplementedError(_('The search method is not implemented on this object !'))

    def name_get(self, cr, user, ids, context=None):
        """

        :param ids: list of ids
        :param context: context arguments, like lang, time zone
        :return: tuples with the text representation of requested objects for to-many relationships

        """
        # TODO: make it work for browse-browse ids
        if not context:
            context = {}
        if not ids:
            return []
        if isinstance(ids, (int, long)):
            ids = [ids]
        return [(r['id'], tools.ustr(r[self._rec_name])) for r in self.read(cr, user, ids,
            [self._rec_name], context, load='_classic_write')]

    def name_search(self, cr, user, name='', args=None, operator='ilike', context=None, limit=100):
        """
        Search for records and their display names according to a search domain.

        :param cr: database cursor
        :param user: current user id
        :param name: object name to search
        :param args: list of tuples specifying search criteria [('field_name', 'operator', 'value'), ...]
        :param operator: operator for search criterion
        :param context: context arguments, like lang, time zone
        :type context: dictionary
        :param limit: optional max number of records to return
        :return: list of object names matching the search criteria, used to provide completion for to-many relationships

        This method is equivalent of :py:meth:`~osv.osv.osv.search` on **name** + :py:meth:`~osv.osv.osv.name_get` on the result.
        See :py:meth:`~osv.osv.osv.search` for an explanation of the possible values for the search domain specified in **args**.

        """
        return self._name_search(cr, user, name, args, operator, context, limit)

    # private implementation of name_search, allows passing a dedicated user for the name_get part to
    # solve some access rights issues
    def _name_search(self, cr, user, name='', args=None, operator='ilike', context=None, limit=100, name_get_uid=None):
        if args is None:
            args = []
        if context is None:
            context = {}
        args = args[:]
        if name:
            args += [(self._rec_name, operator, name)]
        access_rights_uid = name_get_uid or user
        ids = self._search(cr, user, args, limit=limit, context=context, access_rights_uid=access_rights_uid)
        res = self.name_get(cr, access_rights_uid, ids, context)
        return res

    name_search.original_orm = True # Mark this simple implementation
    _name_search.original_orm = True # Sigh! some models override this one...

    def copy(self, cr, uid, id, default=None, context=None):
        raise NotImplementedError(_('The copy method is not implemented on this object !'))

    def exists(self, cr, uid, ids, context=None):
        raise NotImplementedError(_('The exists method is not implemented on this object !'))

    def read_string(self, cr, uid, id, langs, fields=None, context=None):
        res = {}
        res2 = {}
        self.pool.get('ir.model.access').check(cr, uid, 'ir.translation', 'read', context=context)
        if not fields:
            fields = self._columns.keys() + self._inherit_fields.keys()
        #FIXME: collect all calls to _get_source into one SQL call.
        for lang in langs:
            res[lang] = {'code': lang}
            for f in fields:
                if f in self._columns:
                    res_trans = self.pool.get('ir.translation')._get_source(cr, uid, self._name+','+f, 'field', lang)
                    if res_trans:
                        res[lang][f] = res_trans
                    else:
                        res[lang][f] = self._columns[f].string
        for table in self._inherits:
            cols = intersect(self._inherit_fields.keys(), fields)
            res2 = self.pool.get(table).read_string(cr, uid, id, langs, cols, context)
        for lang in res2:
            if lang in res:
                res[lang]['code'] = lang
            for f in res2[lang]:
                res[lang][f] = res2[lang][f]
        return res

    def write_string(self, cr, uid, id, langs, vals, context=None):
        self.pool.get('ir.model.access').check(cr, uid, 'ir.translation', 'write', context=context)
        #FIXME: try to only call the translation in one SQL
        for lang in langs:
            for field in vals:
                if field in self._columns:
                    src = self._columns[field].string
                    self.pool.get('ir.translation')._set_ids(cr, uid, self._name+','+field, 'field', lang, [0], vals[field], src)
        for table in self._inherits:
            cols = intersect(self._inherit_fields.keys(), vals)
            if cols:
                self.pool.get(table).write_string(cr, uid, id, langs, vals, context)
        return True

    def _check_removed_columns(self, cr, log=False):
        raise NotImplementedError()

    def _add_missing_default_values(self, cr, uid, values, context=None):
        missing_defaults = []
        avoid_tables = [] # avoid overriding inherited values when parent is set
        for tables, parent_field in self._inherits.items():
            if parent_field in values:
                avoid_tables.append(tables)
        for field in self._columns.keys():
            if (not field in values) and (not isinstance(self._columns[field], fields.property)):
                missing_defaults.append(field)
        for field in self._inherit_fields.keys():
            if (field not in values) and (self._inherit_fields[field][0] not in avoid_tables) \
                    and (not isinstance(self._inherit_fields[field][2], fields.property)):
                missing_defaults.append(field)

        if len(missing_defaults):
            #if self._debug:
            #    _logger.debug("Have to add missing defaults for %s: %s", 
            #                    self._name, ','.join(missing_defaults))
            # override defaults with the provided values, never allow the other way around
            defaults = self.default_get(cr, uid, missing_defaults, context)
            for dv in defaults:
                # TODO refactor
                if ((dv in self._columns and self._columns[dv]._type == 'many2many') \
                     or (dv in self._inherit_fields and self._inherit_fields[dv][2]._type == 'many2many')) \
                        and defaults[dv] and isinstance(defaults[dv][0], (int, long)):
                    defaults[dv] = [(6, 0, defaults[dv])]
                if (dv in self._columns and self._columns[dv]._type == 'one2many' \
                    or (dv in self._inherit_fields and self._inherit_fields[dv][2]._type == 'one2many')) \
                        and isinstance(defaults[dv], (list, tuple)) and defaults[dv] and isinstance(defaults[dv][0], dict):
                    defaults[dv] = [(0, 0, x) for x in defaults[dv]]
            #if self._debug:
            #    _logger.debug("Missing defaults for %s: %r", 
            #                    self._name, defaults)
            defaults.update(values)
            values = defaults
        return values

    def check_split_record(self, cr, uid, id, args=None, context=None):
        """Check if these record can be split to multiple ones

            @param id a single record to split
            @param args a dict? indicating the split type
            @return boolean True if this record can be split
        """
        return False

    def split_record(self, cr, uid, id, args=None, context=None):
        """ Split this record into multiple ones

            @param id a single record to split
            @param args a dict? indicating the split type
            @return list of new ids (including `id` )
        """
        return [id,]

    def merge_get_values(self, cr, uid, ids, fields_ignore=None, context=None):
        """Compute the values for merging these records

            @param ids records to merge (see below)
            @param fields_ignore Fields to consider equal and skip
            @return dictionary, values

            Before a merge is possible, we have to check that the values are
            eligible for a merge, and then compute those of the remaining
            record. *By default* ids[0] is the one that will be preserved,
            while ids[1:] will be merged and discarded.

            May raise an exception if records cannot be merged.
        """

        if not ids:
            # Make sure the user never sees this!
            raise ValueError("ids must be at least 2")

        if self._inherits:
            raise NotImplementedError # TODO

        if len(ids) < 2:
            raise ValueError("ids must be at least 2")

        vals = {}
        touched = set()
        if fields_ignore is None:
            fields_ignore = []
        for bres in self.browse(cr, uid, ids, context=context):
            upd = {}
            if not 'id' in vals:
                # must happen at the end of first record
                upd['id'] = bres.id
            for cname, col in self._columns.items():
                if cname in fields_ignore:
                    continue
                rv = col.calc_merge(cr, uid, self, cname, vals, bres, context=context)
                if rv is not None:
                    upd[cname] = rv
                    if vals:
                        touched.add(cname)

            vals.update(upd)

        ret = {}
        for fld in touched:
            ret[fld] = self._columns[fld]._browse2val(vals[fld], fld)

        return ret

    def merge_records(self, cr, uid, ids, fields_ignore=[], vals=None, context=None):
        """ Merge these records into a common one

            TODO: doc
            @param fields_ignore Skip these fields ( `as in merge_get_values()` )
            @param vals if given, pre-computed values from `merge_get_values()`
        """
        if not ids:
            # Make sure the user never sees this!
            raise ValueError("ids must be at least 2")

        if self._inherits:
            raise NotImplementedError # TODO

        if len(ids) < 2:
            raise ValueError("ids must be at least 2")

        if self._debug:
            _logger.debug("%s: merge records %s into %s", only_ids(ids[1:]), only_ids(ids[:1])[0])
        if vals is None:
            vals = self.merge_get_values(cr, uid, ids, fields_ignore=fields_ignore, context=context)
        
        # Switch to read/write part
        if isinstance(ids, browse_record_list):
            ids[0]._invalidate_others(ids, model=self._name)
            ids = only_ids(ids)
        
        self.write(cr, uid, ids[0], vals, context=context)

        imd_obj = self.pool.get('ir.model.data')

        # find all the reverse references
        self.pool.get('ir.model.fields')._merge_ids(cr, uid, self._name, ids[0], ids[1:], context=context)
        # move *all* ir.model.data references (of all sources)
        cr.execute('UPDATE ' + imd_obj._table + ' SET res_id = %s WHERE model = %s AND res_id = ANY(%s)',
                (ids[0], self._name, ids[1:]), debug=self._debug)
        self.unlink(cr, uid, ids[1:], context=context)
        
        # Add a reference in ir.model.data for every id removed
        for i in ids[1:]:
            imd_obj.create(cr, uid, dict(name='merged#%d' %i, model=self._name,
                            res_id=ids[0], noupdate=True, source='merged'),
                        context=context)
        
        return ids[0]

class orm_memory(orm_template):
    """ Memory-based objects
    
        Note: _Freaky_ When an orm_memory model inherits from plain `orm` one,
        the class inheritance (through osv) is manipulated to have `orm` as a
        base class. This means that instead of our models using the `orm_template`
        methods, they would fall back to `orm` ones! We *must* redefine all
        of them!
    """

    _protected = ['read', 'write', 'create', 'default_get', 'perm_read', 'unlink', 'fields_get', 'fields_view_get', 'search', 'name_get', 'distinct_field_get', 'name_search', 'copy', 'import_data', 'search_count', 'exists']
    _inherit_fields = {}
    _max_count = config.get_misc('osv_memory', 'count_limit')
    _max_hours = config.get_misc('osv_memory', 'age_limit')
    _check_time = 20
    # TODO: statistics in server.get_stats?

    def __init__(self, cr):
        super(orm_memory, self).__init__(cr)
        self.datas = {}
        self.next_id = 0
        self.check_id = 0
        # Do we need that? can't be groupped outside ?
        cr.execute('DELETE FROM wkf_instance WHERE res_type=%s', 
                (self._name,), debug=self._debug)

        self._load_manual_fields(cr)

    def _load_manual_fields(self, cr):
        # Load manual fields
        if True:
            cr.execute('SELECT * FROM ir_model_fields WHERE model=%s AND state=%s', (self._name, 'manual'))
            for field in cr.dictfetchall():
                if field['name'] in self._columns:
                    continue
                attrs = {
                    'string': field['field_description'],
                    'required': bool(field['required']),
                    'readonly': bool(field['readonly']),
                    'domain': field['domain'] or None,
                    'size': field['size'],
                    'ondelete': field['on_delete'],
                    'translate': (field['translate']),
                    #'select': int(field['select_level'])
                }

                if field['ttype'] == 'selection':
                    self._columns[field['name']] = getattr(fields, field['ttype'])(eval(field['selection']), **attrs)
                elif field['ttype'] == 'reference':
                    self._columns[field['name']] = getattr(fields, field['ttype'])(selection=eval(field['selection']), **attrs)
                elif field['ttype'] == 'many2one':
                    self._columns[field['name']] = getattr(fields, field['ttype'])(field['relation'], **attrs)
                elif field['ttype'] == 'one2many':
                    self._columns[field['name']] = getattr(fields, field['ttype'])(field['relation'], field['relation_field'], **attrs)
                elif field['ttype'] == 'many2many':
                    _rel1 = field['relation'].replace('.', '_')
                    _rel2 = field['model'].replace('.', '_')
                    _rel_name = 'x_%s_%s_%s_rel' %(_rel1, _rel2, field['name'])
                    self._columns[field['name']] = getattr(fields, field['ttype'])(field['relation'], _rel_name, 'id1', 'id2', **attrs)
                else:
                    self._columns[field['name']] = getattr(fields, field['ttype'])(**attrs)

    def _check_access(self, uid, object_id, mode):
        if uid != 1 and self.datas[object_id]['internal.create_uid'] != uid:
            raise except_orm('AccessError', '%s access is only allowed on your own records for osv_memory objects except for the super-user' % mode.capitalize())

    def vaccum(self, cr, uid, force=False):
        """Run the vaccuum cleaning system, expiring and removing old records from the
        virtual osv_memory tables if the "max count" or "max age" conditions are enabled
        and have been reached. This method can be called very often (e.g. everytime a record
        is created), but will only actually trigger the cleanup process once out of
        "_check_time" times (by default once out of 20 calls)."""
        self.check_id += 1
        if (not force) and (self.check_id % self._check_time):
            return True
        tounlink = []

        # Age-based expiration
        if self._max_hours:
            max = time.time() - self._max_hours * 60 * 60
            for k,v in self.datas.iteritems():
                if v['internal.date_access'] < max:
                    tounlink.append(k)
            self.unlink(cr, 1, tounlink)

        # Count-based expiration
        if self._max_count and len(self.datas) > self._max_count:
            # sort by access time to remove only the first/oldest ones in LRU fashion
            records = self.datas.items()
            records.sort(key=lambda x:x[1]['internal.date_access'])
            self.unlink(cr, 1, [x[0] for x in records[:len(self.datas)-self._max_count]])

        return True

    def read(self, cr, user, ids, fields_to_read=None, context=None, load='_classic_read'):
        if context is None:
            context = {}
        if not fields_to_read:
            fields_to_read = self._columns.keys()
        if self._debug:
            _logger.debug("%s.read(%r, fields=%r)", self._name, ids, fields_to_read)
        result = []
        ids_orig = ids
        if self.datas:
            if isinstance(ids, (int, long)):
                ids = [ids]
            for id in ids:
                if not id in self.datas:
                    continue
                r = {'id': id}
                record = self.datas[id]
                for f in fields_to_read:
                    if f == '_vptr':
                        r[f] = record.get(f, None)
                        continue
                    self._check_access(user, id, 'read')
                    r[f] = record.get(f, False)
                    if r[f] and isinstance(self._columns[f], fields.binary) and context.get('bin_size', False):
                        r[f] = len(r[f])
                result.append(r)
                self.datas[id]['internal.date_access'] = time.time()
            # all non inherited fields for which the attribute whose name is in load is False
            fields_post = filter(lambda x: x in self._columns and not getattr(self._columns[x], load), fields_to_read)
            for f in fields_post:
                res2 = self._columns[f].get_memory(cr, self, ids, f, user, context=context, values=result)
                for record in result:
                    record[f] = res2[record['id']]
        if isinstance(ids_orig, (int, long)):
            return result and result[0] or False
        return result

    def write(self, cr, user, ids, vals, context=None):
        if not ids:
            return True
        vals2 = {}
        upd_todo = []
        for field in vals:
            if field == '_vptr':
                vals2[field] = vals[field]
            elif self._columns[field]._classic_write:
                if self._columns[field].required \
                    and ( (vals[field] is False  \
                            and self._columns[field]._type != 'boolean')
                        or vals[field] is None ):
                    raise except_orm(_("Integrity Error!"),
                        _("Empty value at required %s column of %s is not permitted!") % \
                            (self._columns[field].string, self._description )) # TODO: translate!
                vals2[field] = vals[field]
            else:
                upd_todo.append(field)

        if self._debug:
            _logger.debug('%s.write(#%s, %r)', self._name, ids, vals)

        wkf_signals = {}
        self._workflow.pre_write(cr, user, ids, vals, wkf_signals, context)
        for object_id in ids:
            self._check_access(user, object_id, mode='write')
            if object_id not in self.datas:
                raise except_orm(_("ID not found!"),
                        _("Id #%d of %s object not found, you may need to repeat the operation!") % \
                            (object_id, self._name))
                        # This advice suggests that orm_memory actions are repeatable
            self.datas[object_id].update(vals2)
            self.datas[object_id]['internal.date_access'] = time.time()
            for field in upd_todo:
                self._columns[field].set_memory(cr, self, object_id, field, vals[field], user, context)
        
        self._validate(cr, user, ids, context)
        self._workflow.write(cr, user, ids, wkf_signals, context)
        return object_id

    def create(self, cr, user, vals, context=None):
        self.vaccum(cr, user)
        self.next_id += 1   # We advance even if we fail, just like Postgres does
        id_new = self.next_id

        vals = self._add_missing_default_values(cr, user, vals, context)

        vals2 = {}
        upd_todo = []
        for field in vals:
            if field == '_vptr':
                vals2[field] = vals[field]
            elif self._columns[field]._classic_write:
                vals2[field] = vals[field]
            else:
                upd_todo.append(field)

        for field, column in self._columns.items():
            if column._classic_write and column.required \
                and ( vals.get(field, None) is None
                    or (vals.get(field) is False  \
                        and column._type != 'boolean') ):
                raise except_orm(_("Integrity Error!"),
                    _("Empty value at required %s column of %s is not permitted!") % \
                        ( column.string, self._description )) # TODO: translate!

        self.datas[id_new] = vals2
        self.datas[id_new]['internal.date_access'] = time.time()
        self.datas[id_new]['internal.create_uid'] = user

        for field in upd_todo:
            self._columns[field].set_memory(cr, self, id_new, field, vals[field], user, context)
        self._validate(cr, user, [id_new], context) # FIXME
        if self._log_create and not (context and context.get('no_store_function', False)):
            message = self._description + \
                " '" + \
                self.name_get(cr, user, [id_new], context=context)[0][1] + \
                "' "+ _("created.")
            self.log(cr, user, id_new, message, True, context=context)
        
        self._workflow.create(cr, user, [id_new,], context)
        return id_new

    def _where_calc(self, cr, user, args, active_test=True, context=None):
        if not context:
            context = {}
        args = args[:]
        res=[]
        # if the object has a field named 'active', filter out all inactive
        # records unless they were explicitly asked for
        if 'active' in self._columns and (active_test and context.get('active_test', True)):
            if args:
                active_in_args = False
                for a in args:
                    if a[0] == 'active':
                        active_in_args = True
                if not active_in_args:
                    args.insert(0, ('active', '=', True))
            else:
                args = [('active', '=', True)]
        if args:
            import expression
            e = expression.expression(args, debug=self._debug)
            e.parse(cr, user, self, context)
            res = e.exp
        return res or []

    def _search(self, cr, user, args, offset=0, limit=None, order=None, context=None, count=False, access_rights_uid=None):
        if not context:
            context = {}

        # implicit filter on current user except for superuser
        if user != 1:
            if not args:
                args = []
            args.insert(0, ('internal.create_uid', '=', user))

        result = self._where_calc(cr, user, args, context=context)
        if result==[]:
            return self.datas.keys()

        res=[]
        counter = 1
        #Find the value of dict
        f=False
        if result:
            for id, data in self.datas.items():
                data['id'] = id
                if limit and (counter > int(limit) + int(offset)):
                    break
                f = True
                for arg in result:
                    #
                     # FIXME: use safe_eval with arg, data in context
                    if arg[1] == '=':
                        val = eval('data[arg[0]]'+'==' +' arg[2]', locals())
                    elif arg[1] in ['<','>','in','not in','<=','>=','<>']:
                        val = eval('data[arg[0]]'+arg[1] +' arg[2]', locals())
                    elif arg[1] in ['ilike']:
                        val = (str(data[arg[0]]).find(str(arg[2]))!=-1)
                    f = f and val
                if f:
                    if counter > offset:
                        res.append(id)
                    counter += 1
        if count:
            return len(res)
        return res or []

    def unlink(self, cr, uid, ids, context=None):
        for id in ids:
            self._check_access(uid, id, 'unlink')
            self.datas.pop(id, None)
        if len(ids):
            cr.execute('DELETE FROM wkf_instance '
                       'WHERE res_type=%s AND res_id = ANY (%s)',
                       (self._name,ids), debug=self._debug)
        return True

    def perm_read(self, cr, user, ids, context=None, details=True):
        result = []
        credentials = self.pool.get('res.users').name_get(cr, user, [user])[0]
        create_date = time.strftime('%Y-%m-%d %H:%M:%S')
        for id in ids:
            self._check_access(user, id, 'read')
            result.append({
                'create_uid': credentials,
                'create_date': create_date,
                'write_uid': False,
                'write_date': False,
                'id': id,
                'xmlid' : False,
            })
        return result

    def _check_removed_columns(self, cr, log=False):
        # nothing to check in memory...
        pass

    def exists(self, cr, uid, ids, context=None):
        """Return if `ids` are valid records in the temporary storage
        """
        if isinstance(ids, (int,long)):
            ids = [ids]
        return all(( id in self.datas for id in ids ))

    # Force inheritance (see freaky note above)
    def _auto_init_prefetch(self, schema, context=None):
        return orm_template._auto_init_prefetch(self, schema=schema, context=context)
    
    def _field_model2db(self, cr,context=None):
        return orm_template._field_model2db(self, cr, context=context)

    def _auto_init(self, cr, context=None):
        """ Deprecated initialization function of model -> db schema
        """
        pass

    _auto_init.deferrable = True #: magic attribute for init algorithm

    def _auto_init_sql(self, schema, context=None):
        return orm_template._auto_init_sql(self, schema, context=context)

class orm(orm_template):
    """ ORM for regular, database-stored models

        @attribute _fallback_search enables *slow*, fallback search for those
                function fields that do not provide a _fnct_search() .
                Use with care, as this may perform a read() of the full dataset
                of that model, in order to compute the search condition.
                Takes 3 values:
                    None    the default 'ignore' behavior,
                    True    use the slow method
                    False   stop and raise an exception on those fields
        @attribute _field_group_acl defines group ACLs per field. The key to
                this dictionary is the field name, the value is a set of
                integer group ids .
                It is stored here instead of the fields, because there is a
                rare case that field objects can be shared among models/dbs.
    """
    _sql_constraints = []
    _indices = {}
    _table = None
    _field_group_acl = {} # per field access control
    _protected = ['read','write','create','default_get','perm_read','unlink','fields_get','fields_view_get','search','name_get','distinct_field_get','name_search','copy','import_data','search_count', 'exists']
    __logger = logging.getLogger('orm')
    __schema = NotImplemented   # please don't use this logger

    def read_group(self, cr, uid, domain, fields, groupby, offset=0, limit=None, context=None, orderby=False):
        """
        Get the list of records in list view grouped by the given ``groupby`` fields

        :param domain: search criteria selecting the subset of data we are aggregating
        :param fields: list or dictionary of fields to use/return. When a list is
                provided, the 6.0 API is inferred and default aggregate functions
                are selected for each of these fields. With a dictionary, keys are
                the fields and values the "function expression" that selects which
                aggregate function will be used. Value `True` means to use the
                default function.
        :param groupby: list of fields on which to groupby the records. On 6.0
                API, the first one is used, others are transferred to 'group_by'
                of the resulting context.
        :type fields_list: list (example ['field_name_1', ...])
        :param offset: optional number of records to skip
        :param limit: optional max number of records to return
        :param context: context arguments, like lang, time zone
        :param order: optional ``order by`` specification, for overriding the natural
                      sort ordering of the groups, see also :py:meth:`~osv.osv.osv.search`
                      (supported only for many2one fields currently)
        :return: 6.0 API: list of dictionaries(one dictionary for each record) containing:

                    * the values of fields grouped by the fields in ``groupby`` argument
                    * __domain: list of tuples specifying the search criteria
                    * __context: dictionary with argument like ``groupby``
                F3 API: list of dictionaries, each for each level of grouping:
                    * group_level
                    * __context To be used for all records in `values`
                    * values: list of dicts, each for each value, appended with
                        a `__domain` key, as above
        :rtype: [{'field_name_1': value, ...]
        :raise AccessError: * if user has no read rights on the requested object
                            * if user tries to bypass access rules for read on the requested object

        """
        from expression import expression
        if context:
            context = context.copy()
        else:
            context = {}
        self.pool.get('ir.model.access').check(cr, uid, self._name, 'read', context=context)

        auto_fields = False
        if not fields:
            auto_fields = True
            if isinstance(fields, dict):
                fields = dict.fromkeys(self._columns.keys(), True)
            else:
                fields = self._columns.keys()

        expression([]) # load a dummy one, so that implicit fields are loaded
        query = self._where_calc(cr, uid, domain, context=context)
        self._apply_ir_rules(cr, uid, query, 'read', context=context)

        if self._debug:
            _logger.debug("%s.read_group(%r, fields=%r, groupby=%r)", 
                    self._name, domain, fields, groupby)

        if isinstance(groupby, basestring):
            groupby = [groupby,]
        elif not isinstance(groupby, (list, tuple)):
            raise TypeError("read_group() groupby must be a list, not %s" % type(groupby))

        mode_API = context.get('mode_API', 'F3')

        if isinstance(fields, list):
            mode_API = '6.0'
            fields = dict.fromkeys(fields, True)

        context['mode_API'] = mode_API

        if 'id' not in fields:
            # always include the "id" field, `fields.id_field()` class will do the rest
            fields['id'] = True

        # check that groupby is in our fields definitions
        for g in groupby:
            if g not in fields:
                raise KeyError("Fields in 'groupby' must appear in the list of fields to read (perhaps it's missing in the list view?)")

        joined_models = []
        group_fields = defaultdict(dict)
        for field_expr, field_afn  in fields.items():
            fargs = field_expr.split('.')

            # get the field, dive into inherited models:
            cur_model = self
            field = None
            fargs0 = fargs[0]
            while not field:
                # code mostly copied from expression.__cleanup()
                if fargs0 in cur_model._columns:
                    field = cur_model._columns[fargs0]
                elif fargs0 in expression._implicit_fields:
                    field = expression._implicit_fields[fargs0]
                elif cur_model._log_access and fargs0 in expression._implicit_log_fields:
                    field = expression._implicit_log_fields[fargs0]
                elif fargs0 in joined_models:
                    cur_model = joined_models[fargs0]
                elif fargs0 in cur_model._inherit_fields:
                    next_model = self.pool.get(cur_model._inherit_fields[fargs0][0])
                    # join that model and try to find the field there..
                    query.join((cur_model._table, next_model._table,
                                    cur_model._inherits[next_model._name],'id'),
                                outer=False)
                    joined_models[fargs0] = next_model
                    cur_model = next_model
                else:
                    raise KeyError("No field \"%s\" in model \"%s\"" % (fargs0, cur_model._name))

            k, d = field.calc_group(cr, uid, cur_model, fargs, field_afn, context)
            if k:
                group_fields[k].update(d)

        if mode_API == '6.0':
            # we only group by one level in 6.0
            group_level = 1
            max_group_level = 1
        else:
            # All F3-related modes
            group_level = context.get('min_group_level', 0)
            max_group_level = context.get('max_group_level', len(groupby))

        # these will be used for all subsequent queries
        from_clause, where_clause, where_clause_params = query.get_sql()

        r_results = []
        while group_level <= max_group_level:
            group_by_now = groupby[:group_level]

            select_fields = []
            query_params = []
            domain_fns = []
            post_fns = {}        # post-processing functions, per field expression

            has_aggregate = ('id' not in group_by_now)
            if has_aggregate:
                if mode_API != '6.0' or (not group_by_now) \
                        or (len(group_by_now) < 2 and context.get('group_by_no_leaf')):
                    field_alias = '__count'
                else:
                    field_alias = '%s_count' % (group_by_now[0])
                select_fields.append('COUNT("%s".id) AS "%s"' %(self._table, field_alias))

            for field_expr in fields:
                gfield = group_fields.get(field_expr, False)
                if not gfield:
                    if auto_fields or mode_API == "6.0":
                        # auto-skip fields not explicitly requested
                        continue
                    else:
                        # some field didn't register into 'group_fields',
                        # this is reserved for future tricks
                        raise NotImplementedError("Cannot handle field \"%s\" in %s.read_group()" % (field_expr, self._name))

                if (field_expr in group_by_now) or not has_aggregate:
                    # straight clause, we are groupped
                    select_fields.append('%s AS "%s"' % ( gfield['field_expr'], field_expr))
                    field_params = gfield.get('field_expr_params', False)
                    if field_params is not False:
                        query_params.append(field_params)
                    post_fn = gfield.get('field_post', False)
                    if post_fn:
                        post_fns[field_expr] = post_fn
                else: # has_aggregate
                    field_aggr = gfield.get('field_aggr', None)
                    if field_aggr is None and not auto_fields:
                        raise KeyError("Field %s.\"%s\" cannot be aggregated" % (self._name, field_expr))
                    elif field_aggr:
                        select_fields.append('%s AS "%s"' % (field_aggr, field_expr))
                        field_params = gfield.get('field_expr_params', False)
                        if field_params is not False:
                            query_params.append(field_params)
                        post_fn = gfield.get('field_aggr_post', False)
                        if post_fn:
                            post_fns[field_expr] = post_fn

            query = 'SELECT %s FROM %s'  %( ', '.join(select_fields), from_clause)
            if where_clause:
                query += ' WHERE ' + where_clause
                query_params += where_clause_params

            if has_aggregate:
                group_exprs = []
                def identity_dom(d):
                    return lambda row: [(d, '=', row[d])]
                for g in group_by_now:
                    gfield = group_fields.get(g, False)
                    if not gfield:
                        raise except_orm(_('Invalid group_by'),
                                 _('Invalid group_by specification: "%s".\n'
                                    'A group_by specification must be a list of valid fields.') % \
                                    (g,))
                    group_exprs.append(gfield['group_by'])
                    domain_fns.append(gfield.get('domain_fn', identity_dom(g)))

                if group_exprs:
                    query += ' GROUP BY %s' % ( ', '.join(group_exprs))
                del group_exprs

            our_result = {'group_level': group_level }
            if group_level < len(groupby):
                our_result['__context'] = {'group_by':groupby[group_level:]}
            cr.execute(query, query_params, debug=self._debug)

            our_result['values'] = []
            # Query is expected to return values directly usable as an RPC
            # result. We may only map None (aka. SQL NULL) to False.

            ids = []
            for row in cr.dictfetchall():
                for k in row:
                    if row[k] is None:
                        row[k] = False
                if domain_fns:
                    dom2 = []
                    for dfn in domain_fns:
                        dom2 += dfn(row)
                    row['__domain'] = dom2 + domain
                our_result['values'].append(row)
                ids.append(row['id'])

            if post_fns:
                rev_map = {}
                # build a single id => result line map for all post functions
                for n, v in enumerate(our_result['values']):
                    rev_map[v['id']] = n
                for field_expr, post_fn in post_fns.items():
                    res = post_fn(cr, self, ids, field_expr, user=uid, context=context,
                                    values=our_result['values'])
                    # res now holds id:value pairs
                    for rid, val in res.items():
                        n = rev_map.get(rid, False)
                        if n is not False:
                            our_result['values'][n][field_expr] = val
                    del res
                del rev_map

            r_results.append(our_result)
            group_level += 1
            # end while

        if mode_API == '6.0':
            assert len(r_results) == 1, "Strange: %d results for 6.0 API" % len(r_results)
            ret = r_results[0]['values']
            ctx2 = r_results[0].get('__context', False)
            if ctx2:
                # embed context in each row of results
                for r in ret:
                    r['__context'] = ctx2
            return ret
        else:
            return r_results


    def _inherits_join_add(self, parent_model_name, query):
        """
        Add missing table SELECT and JOIN clause to ``query`` for reaching the parent table (no duplicates)

        :param parent_model_name: name of the parent model for which the clauses should be added
        :param query: query object on which the JOIN should be added
        """
        inherits_field = self._inherits[parent_model_name]
        parent_model = self.pool.get(parent_model_name)
        parent_table_name = parent_model._table
        quoted_parent_table_name = '"%s"' % parent_table_name
        if quoted_parent_table_name not in query.tables:
            query.tables.append(quoted_parent_table_name)
            query.where_clause.append('("%s".%s = %s.id)' % (self._table, inherits_field, parent_table_name))

    def _inherits_join_calc(self, field, query):
        """
        Adds missing table select and join clause(s) to ``query`` for reaching
        the field coming from an '_inherits' parent table (no duplicates).

        :param field: name of inherited field to reach
        :param query: query object on which the JOIN should be added
        :return: qualified name of field, to be used in SELECT clause
        """
        current_table = self
        while field in current_table._inherit_fields and not field in current_table._columns:
            parent_model_name = current_table._inherit_fields[field][0]
            parent_table = self.pool.get(parent_model_name)
            current_table._inherits_join_add(parent_model_name, query)
            current_table = parent_table
        return '"%s".%s' % (current_table._table, field)

    def _parent_store_compute(self, cr):
        if not self._parent_store:
            return
        _logger.info('Computing parent left and right for table %s...' % (self._table, ))
        def browse_rec(root, pos=0):
# TODO: set order
            where = self._parent_name+'='+str(root)
            if not root:
                where = self._parent_name+' IS NULL'
            if self._parent_order:
                where += ' order by '+self._parent_order
            cr.execute('SELECT id FROM '+self._table+' WHERE '+where)
            pos2 = pos + 1
            for id in cr.fetchall():
                pos2 = browse_rec(id[0], pos2)
            cr.execute('update '+self._table+' set parent_left=%s, parent_right=%s where id=%s', (pos,pos2,root))
            return pos2+1
        query = 'SELECT id FROM '+self._table+' WHERE '+self._parent_name+' IS NULL'
        if self._parent_order:
            query += ' order by '+self._parent_order
        pos = 0
        cr.execute(query)
        for (root,) in cr.fetchall():
            pos = browse_rec(root, pos)
        return True

    def _update_store(self, cr, f, k):
        """ Fills computed values for function field "k"
        """
        _logger.debug("storing computed values of field '%s.%s'" % (self._name, k,))
        ss = self._columns[k]._symbol_set
        update_query = 'UPDATE "%s" SET "%s"=%s WHERE id = ANY(%%s)' % (self._table, k, ss[0])

        upd_vals = defaultdict(list) # value: [id,...] map *in that order*
        def __flush():
            for val, upd_ids in upd_vals.iteritems():
                cr.execute(update_query, (val, upd_ids), debug=self._debug)
            upd_vals.clear()

        cr.execute('select id from '+self._table, debug=self._debug)
        ids_lst = map(itemgetter(0), cr.fetchall())
        count = 0
        while ids_lst:
            iids = ids_lst[:100]
            ids_lst = ids_lst[100:]
            res = f.get(cr, self, iids, k, 1, {})
            for key,val in res.items():
                if f._multi:
                    val = val.get(k, False)
                # if val is a many2one, just write the ID
                if isinstance(val, tuple):
                    val = val[0]
                if (val is not False) or f._type == 'boolean':
                    upd_vals[ss[1](val)].append(key)
                    count += 1
                if count >= 1000:
                    __flush()
                    count = 0
        __flush()

    def force_update_store(self, cr, uid, ids=False, column=False, context=None):
        """ Externally visible call to update the stored fields
        
        This will allow the administrator (only) to re-compute the stored fields
        if we suspect that something has gone wrong. If you are using this function
        too often, you already have some trouble with the server!
        
        @param ids  list of ids to update (NOT implemented, yet)
        @param column  empty (for all columns), list or string of column to update
        """
        if uid != 1:
            raise except_orm('AccessError',
                            _('Forced update of the stored fields is only permitted to the admin user!'))
        assert not ids, "force_update_store cannot be called for specific fields, yet"
        update_custom_fields = context and context.get('update_custom_fields', False)
        if not column:
            cols = self._columns.keys()
        elif isinstance(column, basestring):
            cols = [column,]
        else:
            cols = column

        cols2 = []
        
        for k in cols:
            if k in ('id', 'write_uid', 'write_date', 'create_uid', 'create_date', '_vptr'):
                continue
            #Not Updating Custom fields
            if k.startswith('x_') and not update_custom_fields:
                continue

            f = self._columns[k] # KeyError: does column refer to existing _columns?
            if not isinstance(f, fields.function):
                continue
            if not f.store:
                continue
            cols2.append((k, f))

        if self._debug:
            _logger.debug("%s.force_update_store: columns: %s", self._name, 
                            ', '.join([ c[0] for c in cols2]))
        for k, f in cols2:
            self._update_store(cr, f, k)
        return True

    def _check_selection_field_value(self, cr, uid, field, value, context=None):
        """Raise except_orm if value is not among the valid values for the selection field"""
        if self._columns[field]._type == 'reference':
            val_model, val_id_str = value.split(',', 1)
            val_id = False
            try:
                val_id = long(val_id_str)
            except ValueError:
                pass
            if not val_id:
                raise except_orm(_('ValidateError'),
                                 _('Invalid value for reference field "%s" (last part must be a non-zero integer): "%s"') % (field, value))
            val = val_model
        else:
            val = value
        if isinstance(self._columns[field].selection, (tuple, list)):
            if val in dict(self._columns[field].selection):
                return
        elif val in dict(self._columns[field].selection(self, cr, uid, context=context)):
            return
        raise except_orm(_('ValidateError'),
                         _('The value "%s" for the field "%s" is not in the selection') % (value, field))

    def _check_removed_columns(self, cr, log=False):
        # iterate on the database columns to drop the NOT NULL constraints
        # of fields which were required but have been removed (or will be added by another module)
        columns = [c for c in self._columns if not (isinstance(self._columns[c], fields.function) and not self._columns[c].store)]
        columns += ('id', 'write_uid', 'write_date', 'create_uid', 'create_date') # openerp access columns
        # TODO: refactor
        return None # FIXME
        if self._vtable:
            columns.append('_vptr')
        cr.execute("SELECT a.attname, a.attnotnull"
                   "  FROM pg_class c, pg_attribute a"
                   " WHERE c.relname=%s"
                   "   AND c.oid=a.attrelid"
                   "   AND c.relnamespace IN (SELECT oid from pg_namespace WHERE nspname = ANY(current_schemas(false)))"
                   "   AND a.attisdropped=%s"
                   "   AND pg_catalog.format_type(a.atttypid, a.atttypmod) NOT IN ('cid', 'tid', 'oid', 'xid')"
                   "   AND a.attname NOT IN %s" ,(self._table, False, tuple(columns))),

        for column in cr.dictfetchall():
            if log:
                self.__logger.debug("column %s is in the table %s but not in the corresponding object %s",
                                    column['attname'], self._table, self._name)
            if column['attnotnull']:
                cr.execute('ALTER TABLE "%s" ALTER COLUMN "%s" DROP NOT NULL' % \
                            (self._table, column['attname']), debug=self._debug)

    def _auto_init_prefetch(self, schema, context=None):
        if self._debug:
            schema.set_debug(self._table)
        schema.hints['tables'].append(self._table)
        orm_template._auto_init_prefetch(self, schema, context=context)

    def _auto_init_sql(self, schema, context=None):
        if context is None:
            context = {}
        create = False
        todo_end = []

        def _update_parent_vtables(cr, parent_table, inherit_column):
            """ helper that updates missing _vptr entries for our virtual ancestors
            """
            cr.execute('UPDATE "%(a)s" SET _vptr = \'%(self)s\' ' \
                ' FROM "%(b)s" WHERE "%(a)s".id = "%(b)s"."%(i)s" AND "%(a)s"._vptr IS NULL ' % \
                    {'a': parent_table, 'b': self._table,
                    'i': inherit_column, 'self': self._name },
                debug=self._debug)
        
        if getattr(self, '_auto', True):
            if self._table in schema.tables:
                schema_table = schema.tables[self._table]
                if not isinstance(schema_table, sql_model.Table):
                    # perhaps extend this, one day, to support views
                    raise TypeError("Cannot adapt model %s to %r" % (self._name, schema_table))
                if not 'id' in schema_table.columns:
                    _logger.error("%s: table %s doesn't have an 'id' column. Please fix it!",
                            self._name, self._table)
            else:
                schema_table = schema.tables.append(sql_model.\
                            Table(name=self._table, comment=self._description))
                assert schema_table._state == sql_model.CREATE, schema_table._state
                # Create the 'id' column only on new tables, otherwise we are doing sth wrong
                schema_table.columns.append(sql_model.Column('id','SERIAL', not_null=True, primary_key=True))

            if self._parent_store:
                if 'parent_left' not in schema_table.columns:
                    if 'parent_left' not in self._columns:
                        _logger.error('create a column parent_left on object %s: fields.integer(\'Left Parent\', select=1)' % (self._table, ))
                    elif not self._columns['parent_left'].select:
                        _logger.error('parent_left column on object %s must be indexed! Add select=1 to the field definition)',
                                            self._table)
                    if 'parent_right' not in self._columns:
                        _logger.error( 'create a column parent_right on object %s: fields.integer(\'Right Parent\', select=1)' % (self._table, ))
                    elif not self._columns['parent_right'].select:
                        _logger.error('parent_right column on object %s must be indexed! Add select=1 to the field definition)',
                                            self._table)
                    if self._columns[self._parent_name].ondelete != 'cascade':
                        _logger.error( "the columns %s on object must be set as ondelete='cascasde'" % (self._name, self._parent_name))
                    schema_table.columns.append(sql_model.Column('parent_left', 'INTEGER'))
                    schema_table.columns.append(sql_model.Column('parent_right', 'INTEGER'))

                    # See note at end of this function
                    todo_end.append((5, self._parent_store_compute, ()))
                    # will it work if some function _defaults depend on it?

            if self._log_access:
                schema_table.check_column('create_uid', ctype='INTEGER', 
                        references={'table':'res_users', 'on_delete': 'SET NULL'})
                schema_table.check_column('create_date', ctype='TIMESTAMP',
                        default=sql_model.Column.now())
                schema_table.check_column('write_uid', ctype='INTEGER', 
                        references={'table':'res_users', 'on_delete': 'SET NULL'})
                schema_table.check_column('write_date', ctype='TIMESTAMP')

            if self._vtable:
                schema_table.check_column('_vptr', ctype='VARCHAR', size=64)

            # self._check_removed_columns(cr, log=False) TODO

            # iterate on the "object columns"
            update_custom_fields = context.get('update_custom_fields', False)

            for k in self._columns:
                if k in ('id', '_vptr'): # never controlled by _columns
                    continue
                if self._log_access and k in ('write_uid', 'write_date', 'create_uid', 'create_date'):
                    # they are automatically maintained above.
                    # Still, if _log_access == False, _columns are allowed to create them
                    continue

                #Not Updating Custom fields
                if k.startswith('x_') and not update_custom_fields:
                    continue

                fr = self._columns[k]._auto_init_sql(k, self, schema_table, context=context)
                
                if fr and isinstance(fr, list):
                    # is that enough?
                    todo_end.extend(fr)
                elif fr:
                    todo_end.append(fr)

            for inh in self._inherits:
                pclass = self.pool.get(inh)
                if not pclass._vtable:
                    continue
                todo_end.append((5, _update_parent_vtables,(pclass._table, self._inherits[inh])))
        else:
            # non "_auto" model
            create = self._table in schema.tables

        if self._sql_constraints:
            if self._table not in schema.tables:
                _logger.error('%s: sql constraints defined for table %s, but that table does not exist in the model!',
                        self._name, self._table)
            else:
                schema_table = schema.tables[self._table]
                for (key, con, _) in self._sql_constraints:
                    conname = '%s_%s' % (self._table, key)
                    schema_table.check_constraint(conname, self, con)
                # then, drop rest of them
                for con in schema_table.constraints:
                    if con._state == sql_model.SQL:
                        _logger.info("Dropping constraint %s off %s", con._name, self._table)
                        con.drop()

        if self._indices:
            if self._table not in schema.tables:
                _logger.error('%s: sql indices defined for table %s, but that table does not exist in the model!',
                        self._name, self._table)
            else:
                if self._debug:
                    _logger.debug("%s: Updating indices", self._name)
                schema_table = schema.tables[self._table]
                for name, idx in self._indices.items():
                    fr = idx._auto_init_sql(name, self, schema_table, context=context)

                    if fr and isinstance(fr, list):
                        # is that enough?
                        todo_end.extend(fr)
                    elif fr:
                        todo_end.append(fr)

                # then, drop rest of them
                for idx in schema_table.indices:
                    if idx._state == sql_model.SQL:
                        _logger.info("Should drop index %s off %s", idx._name, self._table)
                        #idx.drop() ..but we don't

        # Note about order:
        # Since we put these operations in the "todo_end" list, they may
        # be executed *after* all the models are updated in SQL. This has
        # not been the case in 6.0, where they would run immediately after
        # each table. Now, we assume it is safe to update all schema and
        # then compute.

        if create and hasattr(self, "_sql"):
            todo_end.append((100, lambda cr: cr.execute(self._sql),()))

        return todo_end

    def __init__(self, cr):
        super(orm, self).__init__(cr)

        if not hasattr(self, '_log_access'):
            # if not access is not specify, it is the same value as _auto
            self._log_access = getattr(self, "_auto", True)

        if config.get_misc_db(cr.dbname, 'orm', 'fallback_search', None) == True:
            self._fallback_search = True

        # Make shallow copy of _columns, some of them may be deep-copied
        # during post_init()
        self._columns = self._columns.copy()

        self._load_manual_fields(cr)

        for (key, _, msg) in self._sql_constraints:
            if self._debug:
                _logger.debug("Installing sql error \"%s\" for %s", key, self._name)
            self.pool._sql_error[self._table+'_'+key] = msg

        self._inherits_check()
        self._inherits_reload()
        self._reload_field_acls(cr)
        if not self._sequence:
            self._sequence = self._table+'_id_seq'
        for k in self._defaults.keys():
            if self._defaults[k] is None:
                del self._defaults[k]
            else:
                assert (k in self._columns) or (k in self._inherit_fields), \
                    'Default function defined in %s but field %s does not exist %r !' %\
                    (self._name, k, self._defaults[k])

    def _load_manual_fields(self, cr):
        if True:
            cr.execute('SELECT * FROM ir_model_fields WHERE model=%s AND state=%s', (self._name, 'manual'))
            for field in cr.dictfetchall():
                if field['name'] in self._columns:
                    continue
                attrs = {
                    'string': field['field_description'],
                    'required': bool(field['required']),
                    'readonly': bool(field['readonly']),
                    'domain': eval(field['domain']) if field['domain'] else None,
                    'size': field['size'],
                    'ondelete': field['on_delete'],
                    'translate': (field['translate']),
                    #'select': int(field['select_level'])
                }

                klass = fields.get_field_class(field['ttype'])
                self._columns[field['name']] = klass.from_manual(field, attrs)

        for name in self._columns:
            nf = self._columns[name].post_init(cr, name, self)
            if nf:
                self._columns[name] = nf

    def _reload_field_acls(self, cr):
        self._field_group_acl = {}
        # Read field-specificic permissions from db:
        cr.execute('SELECT imf.name AS field, group_id FROM ir_model_fields AS imf, ir_model_fields_group_rel AS imgr '
                    'WHERE imf.id = imgr.field_id AND imf.model=%s', (self._name,), debug=self._debug)
        for field, group_id in cr.fetchall():
            self._field_group_acl.setdefault(field, set()).add(group_id)

    #
    # Update objects that uses this one to update their _inherits fields
    #

    def _inherits_reload_src(self):
        for obj in self.pool.obj_pool.values():
            if self._name in obj._inherits:
                obj._inherits_reload()

    def _inherits_reload(self):
        res = {}
        for table in self._inherits:
            tbl_obj = self.pool.get(table)
            res.update(tbl_obj._inherit_fields)
            for col in tbl_obj._columns.keys():
                res[col] = (table, self._inherits[table], tbl_obj._columns[col], table)
            for col in tbl_obj._inherit_fields.keys():
                res[col] = (table, self._inherits[table], tbl_obj._inherit_fields[col][2], tbl_obj._inherit_fields[col][3])
        self._inherit_fields = res
        self._inherits_reload_src()

    def _inherits_check(self):
        for table, field_name in self._inherits.items():
            if field_name not in self._columns:
                logging.getLogger('init').info('Missing many2one field definition for _inherits reference "%s" in "%s", using default one.' % (field_name, self._name))
                self._columns[field_name] =  fields.many2one(table, string="Automatically created field to link to parent %s" % table,
                                                             required=True, ondelete="cascade")
            elif not self._columns[field_name].required or self._columns[field_name].ondelete.lower() != "cascade":
                logging.getLogger('init').warning('Field definition for _inherits reference "%s" in "%s" must be marked as "required" with ondelete="cascade", forcing it.' % (field_name, self._name))
                self._columns[field_name].required = True
                self._columns[field_name].ondelete = "cascade"

    #def __getattr__(self, name):
    #    """
    #    Proxies attribute accesses to the `inherits` parent so we can call methods defined on the inherited parent
    #    (though inherits doesn't use Python inheritance).
    #    Handles translating between local ids and remote ids.
    #    Known issue: doesn't work correctly when using python's own super(), don't involve inherit-based inheritance
    #                 when you have inherits.
    #    """
    #    for model, field in self._inherits.iteritems():
    #        proxy = self.pool.get(model)
    #        if hasattr(proxy, name):
    #            attribute = getattr(proxy, name)
    #            if not hasattr(attribute, '__call__'):
    #                return attribute
    #            break
    #    else:
    #        return super(orm, self).__getattr__(name)

    #    def _proxy(cr, uid, ids, *args, **kwargs):
    #        objects = self.browse(cr, uid, ids, kwargs.get('context', None))
    #        lst = [obj[field].id for obj in objects if obj[field]]
    #        return getattr(proxy, name)(cr, uid, lst, *args, **kwargs)

    #    return _proxy


    def fields_get(self, cr, user, fields=None, context=None):
        """
        Get the description of list of fields

        :param cr: database cursor
        :param user: current user id
        :param fields: list of fields
        :param context: context arguments, like lang, time zone
        :return: dictionary of field dictionaries, each one describing a field of the business object
        :raise AccessError: * if user has no create/write rights on the requested object

        """
        ira = self.pool.get('ir.model.access')
        write_access = ira.check(cr, user, self._name, 'write', raise_exception=False, context=context) or \
                       ira.check(cr, user, self._name, 'create', raise_exception=False, context=context)
        return super(orm, self).fields_get(cr, user, fields, context, write_access)

    def read(self, cr, user, ids, fields=None, context=None, load='_classic_read'):
        if not context:
            context = {}
        self.pool.get('ir.model.access').check(cr, user, self._name, 'read', context=context)
        if not fields:
            fields = list(set(self._columns.keys() + self._inherit_fields.keys()))
            if self._vtable:
                fields.append('_vptr')
        if isinstance(ids, (int, long)):
            select = [ids]
        else:
            select = ids
        select = map(lambda x: isinstance(x,dict) and x['id'] or x, select)
        if self._debug:
            _logger.debug("%s.read(%r, fields=%r)", self._name, select, fields)
        result = self._read_flat(cr, user, select, fields, context, load)

        for r in result:
            for key, v in r.items():
                if v is None:
                    r[key] = False

        if isinstance(ids, (int, long, dict)):
            return result and result[0] or False
        return result

    def search_read(self, cr, user, domain, offset=0, limit=None, order=None,
                    fields=None, context=None, load='_classic_read'):
        """ Perform search and read in one query.
        @param order can accept the empty string '', meaning "no order"
        See orm_template.search_read().
        """
        if context is None:
            context = {}
        self.pool.get('ir.model.access').check(cr, user, self._name, 'read', context=context)
        if not fields:
            fields = self._columns.keys() + self._inherit_fields.keys()
            if self._vtable:
                fields.append('_vptr')

        query = self._where_calc(cr, user, domain, context=context)
        self._apply_ir_rules(cr, user, query, 'read', context=context)
        query.order_by = self._generate_order_by(order, query)
        query.limit = limit
        query.offset = offset

        if self._debug:
            _logger.debug("%s.search_read(%s, fields=%r)", self._name, query, fields)
        result = self._read_flat(cr, user, query, fields_to_read=fields,
                                context=context, load=load)

        for r in result:
            for key, v in r.items():
                if v is None:
                    r[key] = False

        return result

    def _read_flat(self, cr, user, ids, fields_to_read, context=None, load='_classic_read'):
        """ Perform the SQL query for reading data
          @param ids can be a list of integers, *or* a tuple of (query, order, limit, offset)
                    for search_read
        """
        if not context:
            context = {}
        if not ids:
            return []
        s_query = None
        if isinstance(ids, (list, tuple)):
            ids = map(int, ids)
        elif isinstance(ids, Query):
            s_query = ids
            ids = None

        if fields_to_read == None:
            fields_to_read = self._columns.keys()
            if self._vtable:
                fields_to_read.append('_vptr')


        # all inherited fields + all non inherited fields for which the attribute whose name is in load is True
        fields_pre = [f for f in fields_to_read if
                           f == self.CONCURRENCY_CHECK_FIELD
                           or f == '_vptr'
                        or (f in self._columns and getattr(self._columns[f], '_classic_write'))
                     ] + self._inherits.values()

        if len(fields_pre) and s_query is None:
            # Construct a clause for the security rules.
            # 'tables' hold the list of tables necessary for the SELECT including the ir.rule clauses,
            # or will at least contain self._table.
            s_query = Query(tables=['"%s"' % self._table,],
                    where_clause=['"%s".id = ANY(%%s)' % self._table,],
                    where_clause_params=[ids,])
            self._apply_ir_rules(cr, user, s_query, 'read', context=context)

        if self._debug:
            _logger.debug('%s.read_flat: tables=%s, fields_pre=%s' %
                (self._name, s_query and s_query.tables or '-', fields_pre))

        res = []
        if s_query :
            if len(s_query.tables) > 1:
                table_prefix = self._table + '.'
            else:
                table_prefix = ''

            def convert_field(f):
                if f in ('create_date', 'write_date'):
                    return "date_trunc('second', %s%s) as %s" % (table_prefix, f, f)
                if f == self.CONCURRENCY_CHECK_FIELD:
                    if self._log_access:
                        return "COALESCE(%swrite_date, %screate_date, now())::timestamp AS %s" % (table_prefix, table_prefix, f,)
                    return "now()::timestamp AS %s" % (f,)
                if f == '_vptr':
                    return '%s_vptr' % table_prefix
                if f == 'id':
                    return table_prefix + 'id'
                if isinstance(self._columns[f], fields.binary) and context.get('bin_size', False):
                    return 'length(%s"%s") as "%s"' % (table_prefix, f, f)
                return '%s"%s"' % (table_prefix, f,)
                
            if 'id' not in fields_pre:
                fields_pre.insert(0, 'id')
            fields_pre2 = map(convert_field, fields_pre)
            select_fields = ','.join(fields_pre2)
            params = []
            if s_query:
                qfrom, qwhere, qargs = s_query.get_sql()

                if not qwhere:
                    qwhere = 'true'
                query = 'SELECT %s FROM %s WHERE %s' % \
                            (select_fields, qfrom, qwhere)
                params += qargs

                if s_query.order_by or s_query.order_by is  '':
                    order_by = s_query.order_by
                elif self._parent_order:
                    order_by = ' ORDER BY ' + self._parent_order
                elif self._order:
                    order_by = ' ORDER BY ' + self._order
            else:
                raise RuntimeError()

            if isinstance(order_by, pythonOrderBy):
                pass
            else:
                if order_by:  # could be '' == no order
                    query += order_by
                if s_query and s_query.offset:
                    query += " OFFSET %s"
                    params.append(s_query.offset)
                if s_query and s_query.limit:
                    query += " LIMIT %s"
                    params.append(s_query.limit)

            if 'for update' in context and tools.server_bool.equals(context['for update'], True):
                query += ' FOR UPDATE'

            # Perform the big read of the table, fetch the data!
            cr.execute(query, params, debug=self._debug)

            if ids is not None and s_query and (len(s_query.where_clause) > 1):
                # if we are searching by ids, and rules have been applied
                ids = list(set(ids)) # eliminate duplicates
                if cr.rowcount != len(ids):
                    # Some "access errors" may not be due to rules, but
                    # due to incorrectly cached data, which won't match
                    # the result fetched again from the db.
                    if self._debug:
                        rc = cr.rowcount
                        sd = {}.fromkeys(ids)
                        _logger.debug("access error @%s  %d != %d " %(self._name, rc, len(sd)))
                        _logger.debug("len(%s) != len(%s)" % (cr.fetchall(), sd))
                    raise except_orm('AccessError',
                                         _('Operation prohibited by access rules, or performed on an already deleted document (Operation: %s, Document type: %s).')
                                         % ( _('read'), self._description,))
            res.extend(cr.dictfetchall())
        else:
            # can only happen w/o s_query
            res = [{'id': x} for x in ids]

        tmp_ids = [x['id'] for x in res]
        tmp_fs = []
        for f in fields_pre:
            if f in ('id', self.CONCURRENCY_CHECK_FIELD, '_vptr'):
                continue
            if self._columns[f].translate:
                tmp_fs.append(f)
        
        if len(tmp_ids) and len(tmp_fs) and context.get('lang', False):
            res_trans = self.pool.get('ir.translation')._get_multi_ids(cr, user, 
                                tmp_fs, tmp_ids, ttype='model',
                                lang=context['lang'],
                                prepend=self._name+',')
            res_rmap = {}
            for i, r in enumerate(res):
                res_rmap[r['id']] = i
            for tr in res_trans:
                # tr: (field, id, translation)
                res[res_rmap[tr[1]]][tr[0]] = tr[2]

            del res_rmap

        del tmp_fs

        for table in self._inherits:
            col = self._inherits[table]
            cols = [x for x in intersect(self._inherit_fields.keys(), fields_to_read) if x not in self._columns.keys()]
            if not cols:
                continue
            inh_ids = filter(None, [x[col] for x in res])
            # _read_flat ?
            res2 = self.pool.get(table).read(cr, user, inh_ids , cols, context, load)

            res3 = {}
            for r in res2:
                res3[r['id']] = r
                del r['id']

            res_empty = {}
            for c in cols:
                res_empty[c] = None

            for record in res:
                if not record[col]:# if the record is deleted from _inherits table?
                    record.update(res_empty)
                    continue
                record.update(res3[record[col]])
                if col not in fields_to_read:
                    del record[col]

        # all fields which need to be post-processed by a simple function (symbol_get)
        fields_post = filter(lambda x: x in self._columns and self._columns[x]._symbol_get, fields_to_read)
        if fields_post:
            for r in res:
                for f in fields_post:
                    r[f] = self._columns[f]._symbol_get(r[f])
        ids = [x['id'] for x in res]

        # all non inherited fields for which the attribute whose name is in load is False
        fields_post = filter(lambda x: x in self._columns and not getattr(self._columns[x], load), fields_to_read)
        if not ids:
            # If we are not going to return any result, don't bother doing all this
            # function field calculations
            fields_post = []

        # Compute POST fields
        todo = {}
        for f in fields_post:
            todo.setdefault(self._columns[f]._multi, [])
            todo[self._columns[f]._multi].append(f)
        browse_records = ids
        if todo and getattr(self, '_function_field_browse', False):
            if self._debug:
                _logger.debug('%s: using browse_records in function fields for %r', self._name, ids)
            browse_cache = {self._name: {} }
            for r in res:
                # pre-fill the cache with all data we have so far
                browse_cache[self._name][r['id']] = r
            browse_records = browse_record_list( [ \
                    browse_record(cr, user, id, table=self, cache=browse_cache, context=context, fields_only=True)
                    for id in ids ])
        for key,val in todo.items():
            if key:
                if isinstance(self._columns[val[0]], fields.function):
                    get_ids = browse_records
                else:
                    get_ids = ids
                res2 = self._columns[val[0]].get(cr, self, get_ids, val, user, context=context, values=res)
                if not res2:
                    _logger.warning("%s.%s didn't provide us data for %s",
                                    self._name, val[0], get_ids)
                    res2 = {}

                for pos in val:
                    for record in res:
                        if not (record['id'] in res2):
                            # Is it right not to have that?
                            continue
                        if isinstance(res2[record['id']], str):
                            res2[record['id']] = eval(res2[record['id']])
                            #TOCHECK : why got string instend of dict in python2.6
                        multi_fields = res2[record['id']]
                        if multi_fields:
                            record[pos] = multi_fields.get(pos,[])
            else:
                for f in val:
                    if isinstance(self._columns[f], fields.function):
                        get_ids = browse_records
                    else:
                        get_ids = ids
                    res2 = self._columns[f].get(cr, self, get_ids, f, user, context=context, values=res)
                    for record in res:
                        if res2:
                            record[f] = res2[record['id']]
                        else:
                            record[f] = []

        if s_query and isinstance(order_by, pythonOrderBy):
            while order_by.needs_more():
                ndir, nkey = order_by.get_next_sort()
                res.sort(key=nkey, reverse=not ndir)
            if s_query and s_query.offset:
                res = res[s_query.offset:]
            if s_query and s_query.limit:
                res = res[:s_query.limit]
        ima_obj = self.pool.get('ir.model.access')
        no_perm = "=No Permission=" # TODO translate, outside of this fn
        for vals in res:
            for field in vals:
                fobj = None
                if field in self._columns:
                    fobj = self._columns[field]

                if not fobj:
                    continue
                groups = fobj.read
                if groups:
                    edit = ima_obj.check_groups(cr, user, groups)
                    if not edit:
                        if isinstance(vals[field], list):
                            vals[field] = []
                        elif isinstance(vals[field], (float, int, long)): # FIXME: have False
                            vals[field] = 0
                        elif isinstance(vals[field], basestring):
                            vals[field] = no_perm
                        else:
                            vals[field] = False
        return res

    def perm_read(self, cr, user, ids, context=None, details=True):
        """
        Returns some metadata about the given records.

        :param details: if True, \*_uid fields are replaced with the name of the user
        :return: list of ownership dictionaries for each requested record
        :rtype: list of dictionaries with the following keys:

                    * id: object id
                    * create_uid: user who created the record
                    * create_date: date when the record was created
                    * write_uid: last user who changed the record
                    * write_date: date of the last change to the record
                    * xmlid: XML ID to use to refer to this record (if there is one), in format ``module.name``
        """
        if not context:
            context = {}
        if not ids:
            return []
        fields = ['id']
        if self._log_access:
            fields += ['create_uid', 'create_date', 'write_uid', 'write_date']
        if isinstance(ids, (int, long)):
            uniq = True
            ids = [ids,]
        elif isinstance(ids, (list,tuple)):
            ids = map(int, ids)
            uniq = False
            
        fields_str = ",".join('"%s".%s'%(self._table, f) for f in fields)
        query = '''SELECT %(fields)s, imd.module, imd.name
                   FROM "%(table)s" LEFT JOIN ir_model_data imd
                       ON ( imd.model = '%(model)s' AND imd.res_id = %(table)s.id AND imd.source = 'xml')
                   WHERE "%(table)s".id = ANY(%%s) ''' % \
                    { 'fields': fields_str, 'table': self._table, 'model': self._name }

        cr.execute(query, (ids,), debug=self._debug)
        res = cr.dictfetchall()
        for r in res:
            for key in r:
                r[key] = r[key] or False
                if key in ('write_uid', 'create_uid', 'uid') and details and r[key]:
                    try:
                        r[key] = self.pool.get('res.users').name_get(cr, user, [r[key]])[0]
                    except Exception:
                        pass # Leave the numeric uid there
            r['xmlid'] = ("%(module)s.%(name)s" % r) if r['name'] else False
            del r['name'], r['module']
        if uniq:
            return res[ids[0]]
        return res

    def _check_concurrency(self, cr, ids, context):
        if not context:
            return
        if not (context.get(self.CONCURRENCY_CHECK_FIELD) and self._log_access):
            return
        check_clause = "(id = %s AND %s < date_trunc('second', COALESCE(write_date, create_date, now())::timestamp))"
        for sub_ids in cr.split_for_in_conditions(ids):
            ids_to_check = []
            for id in sub_ids:
                id_ref = "%s,%s" % (self._name, id)
                update_date = context[self.CONCURRENCY_CHECK_FIELD].pop(id_ref, None)
                if update_date:
                    ids_to_check.extend([id, update_date])
            if not ids_to_check:
                continue
            cr.execute("SELECT id FROM %s WHERE %s" % (self._table, " OR ".join([check_clause]*(len(ids_to_check)/2))), tuple(ids_to_check), debug=self._debug)
            res = cr.fetchone()
            if res:
                # mention the first one only to keep the error message readable
                raise except_orm('ConcurrencyException', _('A document was modified since you last viewed it (%s:%d)') % (self._description, res[0]))

    def check_access_rule(self, cr, uid, ids, operation, context=None):
        """Verifies that the operation given by ``operation`` is allowed for the user
           according to ir.rules.

           :param operation: one of ``write``, ``unlink``
           :raise except_orm: * if current ir.rules do not permit this operation.
           :return: None if the operation is allowed
        """
        
        query = Query(tables=['"%s"' % self._table,], allow_where_joins=False)
        self._apply_ir_rules(cr, uid, query, operation, context=context)
        if query.where_clause:
            qfrom, qwhere, qwhere_params = query.get_sql()
            if qwhere.lower() == 'true':
                return
            if self._debug:
                _logger.debug("Calculating prohibited ids for user %d on %s", uid, self._name)
            # In this query we negate the rule clause, so that we pick the offending ids
            cr.execute('SELECT DISTINCT "%s".id FROM %s ' \
                        ' WHERE "%s".id = ANY(%%s) AND NOT (%s) LIMIT 20' % \
                            (self._table, qfrom, self._table, qwhere),
                        [ids,] + qwhere_params, debug=self._debug)
            if cr.rowcount:
                dfrom = ''
                # TODO
                # if cr.auth_proxy:
                #     dfrom = 'from %s ' % cr.auth_proxy.get_short_info()
                _logger.error("%s: attempted access violation for user #%d@%s %s on records %r",
                        self._name, uid, cr.dbname, dfrom, [x[0] for x in cr.fetchall()])

                opls = {'read': _('read'), 'write': _('write'),
                    'create': _('create'), 'unlink': _('delete')}
                raise except_orm('AccessError',
                                _('Operation prohibited by access rules, or performed on an already deleted document (Operation: %s, Document type: %s).')
                                % (opls.get(operation, operation), self._description))

    def unlink(self, cr, uid, ids, context=None):
        """
        Delete records with given ids

        :param cr: database cursor
        :param uid: current user id
        :param ids: id or list of ids
        :param context: (optional) context arguments, like lang, time zone
        :return: True
        :raise AccessError: * if user has no unlink rights on the requested object
                            * if user tries to bypass access rules for unlink on the requested object
        :raise UserError: if the record is default property for other records

        """
        if not ids:
            return True
        if isinstance(ids, (int, long)):
            ids = [ids]

        result_store = self._store_get_values(cr, uid, ids, None, context)

        self._check_concurrency(cr, ids, context)

        self.pool.get('ir.model.access').check(cr, uid, self._name, 'unlink', context=context)

        ir_property = self.pool.get('ir.property')

        # Check if the records are used as default properties.
        domain = [('res_id', '=', False),
                  ('value_reference', 'in', ['%s,%s' % (self._name, i) for i in ids]),
                 ]
        if ir_property.search(cr, uid, domain, context=context):
            raise except_orm(_('Error'), _('Unable to delete this document because it is used as a default property'))

        # Delete the records' properties.
        property_ids = ir_property.search(cr, uid, [('res_id', 'in', ['%s,%s' % (self._name, i) for i in ids])], context=context)
        ir_property.unlink(cr, uid, property_ids, context=context)

        self._workflow.delete(cr, uid, ids, context)

        # Shall we also remove the inherited records in python, here?

        self.check_access_rule(cr, uid, ids, 'unlink', context=context)
        pool_model_data = self.pool.get('ir.model.data')
        pool_ir_values = self.pool.get('ir.values')
        cr.execute('DELETE FROM ' + self._table + ' ' \
                       'WHERE id = ANY(%s)', (ids,), debug=self._debug)


        # Mark this record as deleted (res_id: 0) in ir.model.data table
        # If the reference is from an XML source, it will be deleted by
        # the `ir_model_data_delete` SQL rule. Otherwise, it will be
        # preserved, to indicate that a remote side needs to act, too.
        cr.execute('UPDATE "' + pool_model_data._table + '" '\
                    ' SET res_id = 0, date_update = now() ' \
                    ' WHERE model = %s AND res_id = ANY(%s)',
                (self._name, list(ids)), debug=self._debug)

        cr.execute('DELETE FROM "%s" WHERE (model = %%s AND res_id = ANY(%%s) ) OR value = ANY(%%s)' \
                    % pool_ir_values._table,
                    (self._name, list(ids), ['%s,%s' % (self._name, sid) for sid in ids]),
                    debug=self._debug)

        for order, object, store_ids, fields in result_store:
            if object != self._name:
                obj =  self.pool.get(object)
                cr.execute('SELECT id FROM '+obj._table+' WHERE id = ANY(%s)', (store_ids,))
                rids = map(itemgetter(0), cr.fetchall())
                if rids:
                    obj._store_set_values(cr, uid, rids, fields, context)

        return True

    #
    # TODO: Validate
    #
    def write(self, cr, user, ids, vals, context=None):
        """
        Update records with given ids with the given field values

        :param cr: database cursor
        :param user: current user id
        :type user: integer
        :param ids: object id or list of object ids to update according to **vals**
        :param vals: field values to update, e.g {'field_name': new_field_value, ...}
        :type vals: dictionary
        :param context: (optional) context arguments, e.g. {'lang': 'en_us', 'tz': 'UTC', ...}
        :type context: dictionary
        :return: True
        :raise AccessError: * if user has no write rights on the requested object
                            * if user tries to bypass access rules for write on the requested object
        :raise ValidateError: if user tries to enter invalid value for a field that is not in selection
        :raise UserError: if a loop would be created in a hierarchy of objects a result of the operation (such as setting an object as its own parent)

        **Note**: The type of field values to pass in ``vals`` for relationship fields is specific:

            + For a many2many field, a list of tuples is expected.
              Here is the list of tuple that are accepted, with the corresponding semantics ::

                 (0, 0,  { values })    link to a new record that needs to be created with the given values dictionary
                 (1, ID, { values })    update the linked record with id = ID (write *values* on it)
                 (2, ID)                remove and delete the linked record with id = ID (calls unlink on ID, that will delete the object completely, and the link to it as well)
                 (3, ID)                cut the link to the linked record with id = ID (delete the relationship between the two objects but does not delete the target object itself)
                 (4, ID)                link to existing record with id = ID (adds a relationship)
                 (5)                    unlink all (like using (3,ID) for all linked records)
                 (6, 0, [IDs])          replace the list of linked IDs (like using (5) then (4,ID) for each ID in the list of IDs)

                 Example:
                    [(6, 0, [8, 5, 6, 4])] sets the many2many to ids [8, 5, 6, 4]

            + For a one2many field, a lits of tuples is expected.
              Here is the list of tuple that are accepted, with the corresponding semantics ::

                 (0, 0,  { values })    link to a new record that needs to be created with the given values dictionary
                 (1, ID, { values })    update the linked record with id = ID (write *values* on it)
                 (2, ID)                remove and delete the linked record with id = ID (calls unlink on ID, that will delete the object completely, and the link to it as well)

                 Example:
                    [(0, 0, {'field_name':field_value_record1, ...}), (0, 0, {'field_name':field_value_record2, ...})]

            + For a many2one field, simply use the ID of target record, which must already exist, or ``False`` to remove the link.
            + For a reference field, use a string with the model name, a comma, and the target object id (example: ``'product.product, 5'``)

        """
        for field in vals.keys():
            if field == '_vptr':
                continue

            if self._field_group_acl.get(field, False):
                groups = list(self._field_group_acl[field])
                if not self.pool.get('res.groups').check_user_groups(cr, user, groups):
                    if vals[field] == []:
                        # Special case for 'control' values of o2m, m2o, m2m , where
                        # an empty list means no modification
                        vals.pop(field)
                        continue
                    _logger.warning("Access error for %s.%s by user #%d: not in group %s",
                            self._name, field, user, groups)
                    _logger.debug("Value for field %s.%s: %r", self._name, field, vals[field])
                    raise except_orm('AccessError',
                                     _('You are not permitted to write into column %s.%s.') % (self._name, field))

        if not context:
            context = {}
        if not ids:
            return True
        if isinstance(ids, (int, long)):
            ids = [ids]

        self._check_concurrency(cr, ids, context)
        self.pool.get('ir.model.access').check(cr, user, self._name, 'write', context=context)

        wkf_signals = {}
        self._workflow.pre_write(cr, user, ids, vals, wkf_signals, context)
        result = self._store_get_values(cr, user, ids, vals.keys(), context) or []

        # No direct update of parent_left/right
        vals.pop('parent_left', None)
        vals.pop('parent_right', None)

        parents_changed = []
        if self._parent_store and (self._parent_name in vals):
            # The parent_left/right computation may take up to
            # 5 seconds. No need to recompute the values if the
            # parent is the same. Get the current value of the parent
            parent_val = vals[self._parent_name]
            if parent_val:
                query = "SELECT id FROM %s WHERE id IN %%s AND (%s != %%s OR %s IS NULL)" % \
                                (self._table, self._parent_name, self._parent_name)
                cr.execute(query, (tuple(ids), parent_val))
            else:
                query = "SELECT id FROM %s WHERE id IN %%s AND (%s IS NOT NULL)" % \
                                (self._table, self._parent_name)
                cr.execute(query, (tuple(ids),))
            parents_changed = map(itemgetter(0), cr.fetchall())

        if self._debug:
            _logger.debug('%s.write(#%s, %r)', self._name, ids, vals)

        upd0 = []
        upd1 = []
        upd_todo = []
        updend = []
        direct = []
        totranslate = context.get('lang', False) and (context['lang'] != 'en_US')
        for field in vals:
            if field == '_vptr':
                upd0.append('_vptr=%s')
                upd1.append(vals[field] or None)
            elif field in self._columns:
                cf = self._columns[field]
                if cf._classic_write and not (hasattr(cf, '_fnct_inv')):
                    if (not totranslate) or not cf.translate:
                        upd0.append('"'+field+'"=' + cf._symbol_set[0])
                        upd1.append(cf._symbol_set[1](vals[field]))
                    direct.append(field)
                else:
                    upd_todo.append(field)
            else:
                updend.append(field)
            if field in self._columns \
                    and vals[field] \
                    and hasattr(self._columns[field], 'selection'):
                self._check_selection_field_value(cr, user, field, vals[field], context=context)

        if self._log_access:
            upd0.append('write_uid=%s')
            upd0.append('write_date=now()')
            upd1.append(user)

        if len(upd0):
            self.check_access_rule(cr, user, ids, 'write', context=context)
            cr.execute('UPDATE "%s" SET %s WHERE id = ANY(%%s)' % (self._table, ','.join(upd0)),
                           upd1 + [ids,], debug=self._debug)
            if cr.rowcount != len(ids):
                    raise except_orm('AccessError',
                                     _('One of the records you are trying to modify has already been deleted (Document type: %s).') % self._description)

            if totranslate:
                # TODO: optimize
                for f in direct:
                    if self._columns[f].translate:
                        src_trans = self.pool.get(self._name).read(cr,user,ids,[f])[0][f]
                        if not src_trans:
                            src_trans = vals[f]
                            # Inserting value to DB
                            self.write(cr, user, ids, {f:vals[f]})
                        self.pool.get('ir.translation')._set_ids(cr, user, self._name+','+f, 'model', context['lang'], ids, vals[f], src_trans)


        # call the 'set' method of fields which are not classic_write
        upd_todo.sort(lambda x, y: self._columns[x].priority-self._columns[y].priority)

        # default element in context must be removed when call a one2many or many2many
        rel_context = context.copy()
        for k in context:
            if k.startswith('default_'):
                del rel_context[k]

        for field in upd_todo:
            for id in ids:
                result += self._columns[field].set(cr, self, id, field, vals[field], user, context=rel_context) or []

        for table in self._inherits:
            col = self._inherits[table]
            nids = []
            cr.execute('SELECT DISTINCT "'+col+'" FROM "'+self._table+'" ' \
                           'WHERE id = ANY(%s)', (ids,), debug=self._debug)
            nids.extend([x[0] for x in cr.fetchall()])
            v = {}
            for val in updend:
                if self._inherit_fields[val][0] == table:
                    v[val] = vals[val]

            # we update the parent object, if the column has been written to.
            # note that the old table._vptr will still hold a wrong ref
            # to this record
            if col in vals and self.pool.get(table)._vtable:
                v['_vptr'] = self._name

            if v:
                self.pool.get(table).write(cr, user, nids, v, context)

        self._validate(cr, user, ids, context)

        # TODO: use _order to set dest at the right position and not first node of parent
        # We can't defer parent_store computation because the stored function
        # fields that are computer may refer (directly or indirectly) to
        # parent_left/right (via a child_of domain)
        if parents_changed:
            if self.pool._init:
                self.pool._init_parent[self._name]=True
            else:
                order = self._parent_order or self._order
                parent_val = vals[self._parent_name]
                if parent_val:
                    clause, params = '%s=%%s' % (self._parent_name,), (parent_val,)
                else:
                    clause, params = '%s IS NULL' % (self._parent_name,), ()

                for id in parents_changed:
                    cr.execute('SELECT parent_left, parent_right FROM %s WHERE id=%%s' % (self._table,), (id,))
                    pleft, pright = cr.fetchone()
                    distance = pright - pleft + 1

                    # Positions of current siblings, to locate proper insertion point;
                    # this can _not_ be fetched outside the loop, as it needs to be refreshed
                    # after each update, in case several nodes are sequentially inserted one
                    # next to the other (i.e computed incrementally)
                    cr.execute('SELECT parent_right, id FROM %s WHERE %s ORDER BY %s' % (self._table, clause, order), params)
                    parents = cr.fetchall()

                    # Find Position of the element
                    position = None
                    for (parent_pright, parent_id) in parents:
                        if parent_id == id:
                            break
                        position = parent_pright+1

                    # It's the first node of the parent
                    if not position:
                        if not parent_val:
                            position = 1
                        else:
                            cr.execute('SELECT parent_left FROM '+self._table+' WHERE id=%s', (parent_val,))
                            position = cr.fetchone()[0]+1

                    if pleft < position <= pright:
                        raise except_orm(_('UserError'), _('Recursivity Detected.'))

                    if pleft < position:
                        cr.execute('UPDATE '+self._table+' SET parent_left=parent_left+%s WHERE parent_left >= %s', (distance, position))
                        cr.execute('UPDATE '+self._table+' SET parent_right=parent_right+%s where parent_right >= %s', (distance, position))
                        cr.execute('UPDATE '+self._table+' SET parent_left=parent_left+%s, parent_right=parent_right+%s WHERE parent_left >= %s AND parent_left < %s', (position-pleft,position-pleft, pleft, pright))
                    else:
                        cr.execute('UPDATE '+self._table+' SET parent_left=parent_left+%s WHERE parent_left >= %s', (distance, position))
                        cr.execute('UPDATE '+self._table+' SET parent_right=parent_right+%s WHERE parent_right >= %s', (distance, position))
                        cr.execute('UPDATE '+self._table+' SET parent_left=parent_left-%s, parent_right=parent_right-%s WHERE parent_left >= %s AND parent_left < %s', (pleft-position+distance,pleft-position+distance, pleft+distance, pright+distance))

        result += self._store_get_values(cr, user, ids, vals.keys(), context)
        result.sort()

        done = {}
        for order, object, ids_r, fields_r in result:
            key = (object,tuple(fields_r))
            done.setdefault(key, {})
            # avoid to do several times the same computation
            todo = []
            for id in ids_r:
                if id not in done[key]:
                    done[key][id] = True
                    todo.append(id)
            self.pool.get(object)._store_set_values(cr, user, todo, fields_r, context)

        self._workflow.write(cr, user, ids, wkf_signals, context)
        return True

    #
    # TODO: Should set perm to user.xxx
    #
    def create(self, cr, user, vals, context=None):
        """
        Create new record with specified value

        :param cr: database cursor
        :param user: current user id
        :type user: integer
        :param vals: field values for new record, e.g {'field_name': field_value, ...}
        :type vals: dictionary
        :param context: optional context arguments, e.g. {'lang': 'en_us', 'tz': 'UTC', ...}
        :type context: dictionary
        :return: id of new record created
        :raise AccessError: * if user has no create rights on the requested object
                            * if user tries to bypass access rules for create on the requested object
        :raise ValidateError: if user tries to enter invalid value for a field that is not in selection
        :raise UserError: if a loop would be created in a hierarchy of objects a result of the operation (such as setting an object as its own parent)

        **Note**: The type of field values to pass in ``vals`` for relationship fields is specific.
        Please see the description of the :py:meth:`~osv.osv.osv.write` method for details about the possible values and how
        to specify them.

        """
        if not context:
            context = {}
        self.pool.get('ir.model.access').check(cr, user, self._name, 'create', context=context)

        for field in vals.keys():
            if field == '_vptr':
                continue
            if self._field_group_acl.get(field, False):
                if not self.pool.get('res.groups').\
                        check_user_groups(cr, user, list(self._field_group_acl[field])):
                    if not vals[field] :
                        # Empty values are still permitted, we remove them and
                        # let the default be used
                        vals.pop(field)
                        continue
                    _logger.warning("Access error for %s.%s by user #%d: not in group %s",
                            self._name, field, user, list(self._field_group_acl[field]))
                    _logger.debug("Value for field %s.%s: %r", self._name, field, vals[field])
                    raise except_orm('AccessError',
                                     _('You are not permitted to create column %s.%s.') % (self._name, field))

        vals = self._add_missing_default_values(cr, user, vals, context)

        tocreate = {}
        for v in self._inherits:
            if self._inherits[v] not in vals:
                tocreate[v] = {}
            else:
                tocreate[v] = {'id' : vals[self._inherits[v]]}
            if self.pool.get(v)._vtable:
                tocreate[v]['_vptr'] = self._name

        (upd0, upd1, upd2) = ([], [], [])
        upd_todo = []
        for v in vals.keys():
            if v == '_vptr':
                continue
            if v in self._inherit_fields and v not in self._columns:
                (table, col, col_detail, original_parent) = self._inherit_fields[v]
                tocreate[table][v] = vals[v]
                del vals[v]
            else:
                if (v not in self._inherit_fields) and (v not in self._columns):
                    del vals[v]

        # We here assume that the model is a real table. If not, the end of this block
        # will raise an SQL exception and rollback the intermediate steps. Hopefully.
        # Example : any dashboard which has all the fields readonly.(due to Views(database views))

        for table in tocreate:
            if self._inherits[table] in vals:
                del vals[self._inherits[table]]

            record_id = tocreate[table].pop('id', None)
            
            # When linking/creating parent records, force context without 'no_store_function' key that
            # defers stored functions computing, as these won't be computed in batch at the end of create(). 
            parent_context = dict(context)
            parent_context.pop('no_store_function', None)
            
            if record_id is None or not record_id:
                record_id = self.pool.get(table).create(cr, user, tocreate[table], context=parent_context)
            else:
                self.pool.get(table).write(cr, user, [record_id], tocreate[table], context=parent_context)

            upd0.append(self._inherits[table])
            upd1.append('%s')
            upd2.append(record_id)

        #Start : Set bool fields to be False if they are not touched(to make search more powerful)
        bool_fields = [x for x in self._columns.keys() if self._columns[x]._type=='boolean']

        for bool_field in bool_fields:
            if bool_field not in vals:
                vals[bool_field] = False
        #End

        for field in vals:
            if field == '_vptr':
                upd0.append('_vptr')
                upd1.append('%s')
                upd2.append(vals[field] or None)
                continue
            if field in self._columns:
                if self._columns[field]._classic_write:
                    upd0.append('"' + field + '"')
                    upd1.append(self._columns[field]._symbol_set[0])
                    upd2.append(self._columns[field]._symbol_set[1](vals[field]))
                else:
                    if not isinstance(self._columns[field], fields.related):
                        upd_todo.append(field)
            if field in self._columns \
                    and hasattr(self._columns[field], 'selection') \
                    and vals[field]:
                self._check_selection_field_value(cr, user, field, vals[field], context=context)
        if self._log_access:
            upd0 += ['create_uid', 'create_date']
            upd1 += ['%s', 'now()']
            upd2.append(user)
        cr.execute('INSERT INTO "%s" (%s) VALUES (%s) RETURNING id' % \
                    (self._table, ', '.join(upd0), ','.join(upd1)), tuple(upd2), debug=self._debug)
        id_new = cr.fetchone()[0]
        self.check_access_rule(cr, user, [id_new], 'create', context=context)
        upd_todo.sort(lambda x, y: self._columns[x].priority-self._columns[y].priority)

        if self._parent_store and not context.get('defer_parent_store_computation'):
            if self.pool._init:
                self.pool._init_parent[self._name]=True
            else:
                parent = vals.get(self._parent_name, False)
                if parent:
                    cr.execute('SELECT parent_right FROM '+self._table+' WHERE '+self._parent_name+'=%s ORDER BY '+(self._parent_order or self._order), (parent,))
                    pleft_old = None
                    result_p = cr.fetchall()
                    for (pleft,) in result_p:
                        if not pleft:
                            break
                        pleft_old = pleft
                    if not pleft_old:
                        cr.execute('SELECT parent_left FROM '+self._table+' WHERE id=%s', (parent,))
                        pleft_old = cr.fetchone()[0]
                    pleft = pleft_old
                else:
                    cr.execute('SELECT max(parent_right) FROM '+self._table)
                    pleft = cr.fetchone()[0] or 0
                cr.execute('UPDATE '+self._table+' SET parent_left=parent_left+2 WHERE parent_left > %s', (pleft,))
                cr.execute('UPDATE '+self._table+' SET parent_right=parent_right+2 WHERE parent_right > %s', (pleft,))
                cr.execute('UPDATE '+self._table+' SET parent_left=%s,parent_right=%s WHERE id=%s', (pleft+1,pleft+2,id_new))

        # default element in context must be removed when call a one2many or many2many
        rel_context = context.copy()
        for c in context.items():
            if c[0].startswith('default_'):
                del rel_context[c[0]]

        result = []
        for field in upd_todo:
            result += self._columns[field].set(cr, self, id_new, field, vals[field], user, rel_context) or []
        self._validate(cr, user, [id_new], context)

        if not context.get('no_store_function', False):
            result += self._store_get_values(cr, user, [id_new], vals.keys(), context)
            result.sort()
            done = []
            for order, object, ids, fields2 in result:
                if not (object, ids, fields2) in done:
                    self.pool.get(object)._store_set_values(cr, user, ids, fields2, context)
                    done.append((object, ids, fields2))

        if self._log_create and not (context and context.get('no_store_function', False)):
            message = self._description + \
                " '" + \
                self.name_get(cr, user, [id_new], context=context)[0][1] + \
                "' " + _("created.")
            self.log(cr, user, id_new, message, True, context=context)
        self._workflow.create(cr, user, [id_new,], context)
        return id_new

    def _store_get_values(self, cr, uid, ids, fields, context):
        """Returns an ordered list of fields.functions to call due to
           an update operation on ``fields`` of records with ``ids``,
           obtained by calling the 'store' functions of these fields,
           as setup by their 'store' attribute.

           :return: [(priority, model_name, [record_ids,], [function_fields,])]
        """
        result = {}
        fncts = self.pool._store_function.get(self._name, [])
        for i, fnct in enumerate(fncts):
            if fnct[3]:
                ok = False
                if not fields:
                    ok = True
                for f in (fields or []):
                    if f in fnct[3]:
                        ok = True
                        break
                if not ok:
                    continue

            result.setdefault(fnct[0], {})

            # uid == 1 for accessing objects having rules defined on store fields
            ids2 = fnct[2](self, cr, 1, ids, context)
            for id in filter(None, ids2):
                result[fnct[0]].setdefault(id, [])
                result[fnct[0]][id].append(i)
        rdict = {}
        l_fnct = lambda x: fncts[x][1]
        for obj in result:
            k2 = {}
            for id, fnct in result[obj].items():
                k2.setdefault(tuple(fnct), [])
                k2[tuple(fnct)].append(id)
            for fnct, id in k2.items():
                rdict.setdefault(fncts[fnct[0]][4], [])
                rdict[fncts[fnct[0]][4]].append((fncts[fnct[0]][4], obj, id, map(l_fnct, fnct)))
        result2 = []
        tmp = rdict.keys()
        tmp.sort()
        for k in tmp:
            result2 += rdict[k]
        return result2

    def _store_set_values(self, cr, uid, ids, fields, context):
        """Calls the fields.function's "implementation function" for all ``fields``, on records with ``ids`` (taking care of
           respecting ``multi`` attributes), and stores the resulting values in the database directly."""
        if not ids:
            return True
        field_flag = False
        field_dict = {}
        if self._log_access:
            store_fns = []
            for sfn in self.pool._store_function.get(self._name, []):
                if sfn[5] and sfn[1] in fields:
                    store_fns.append(sfn)

            if store_fns:
                cr.execute('SELECT id,write_date FROM '+self._table+' WHERE id = ANY (%s) and write_date IS NOT NULL', (map(int, ids),), debug=self._debug)
                res = cr.fetchall()
            else:
                # just suppress the loop below
                res = []

            # we try to compute which fields have a cache persistence more than
            # dt = now - last write
            dt_now = datetime.datetime.now()
            for r in res:
                field_dict.setdefault(r[0], [])
                write_ago = dt_now - r[1] # a timedelta
                write_hours = write_ago.seconds / 3600
                for sfn in store_fns:
                    if write_hours < sfn[5]:
                        field_dict[r[0]].append(sfn[1])
                        field_flag = True
        todo = {}
        keys = []
        for f in fields:
            if self._columns[f]._multi not in keys:
                keys.append(self._columns[f]._multi)
            todo.setdefault(self._columns[f]._multi, [])
            todo[self._columns[f]._multi].append(f)
        for key in keys:
            val = todo[key]
            if key:
                # uid == 1 for accessing objects having rules defined on store fields
                result = self._columns[val[0]].get(cr, self, ids, val, 1, context=context)
                for id, value in result.items():
                    if field_flag:
                        for f in value.keys():
                            if f in field_dict[id]:
                                value.pop(f)
                    upd0 = []
                    upd1 = []
                    for v in value:
                        if v not in val:
                            continue
                        if self._columns[v]._type in ('many2one', 'one2one') \
                                    and isinstance(value[v], tuple):
                            value[v] = value[v][0]
                        # prefer the _shadow of a function field, rather than the field itself
                        sset = getattr(self._columns[v], '_shadow', self._columns[v])._symbol_set
                        upd0.append('"'+v+'"='+sset[0])
                        upd1.append(sset[1](value[v]))
                    upd1.append(id)
                    if upd0 and upd1:
                        cr.execute('UPDATE "' + self._table + '" SET ' + \
                            ','.join(upd0) + ' WHERE id = %s', upd1,
                            debug=self._debug)

            else:
                for f in val:
                    # uid == 1 for accessing objects having rules defined on store fields
                    result = self._columns[f].get(cr, self, ids, f, 1, context=context)
                    for r in result.keys():
                        if field_flag:
                            if r in field_dict.keys():
                                if f in field_dict[r]:
                                    result.pop(r)
                    for id, value in result.items():
                        if self._columns[f]._type in ('many2one', 'one2one') \
                                and isinstance(value, tuple):
                            value = value[0]
                        if self._debug:
                            _logger.debug('%s: %s %r=%r  (%r %s|%r)', 
                                    self._name, f, id, value,
                                    self._columns[f], self._columns[f]._type,
                                    self._columns[f]._symbol_set)
                        cr.execute('UPDATE "' + self._table + '" SET ' + \
                            '"'+f+'"='+self._columns[f]._symbol_set[0] + ' WHERE id = %s', 
                                (self._columns[f]._symbol_set[1](value),id),
                                debug=self._debug)
        return True

    #
    # TODO: Validate
    #
    def perm_write(self, cr, user, ids, fields, context=None):
        raise NotImplementedError(_('This method does not exist anymore'))

    # TODO: ameliorer avec NULL
    def _where_calc(self, cr, user, domain, active_test=True, context=None):
        """Computes the WHERE clause needed to implement an OpenERP domain.
        :param domain: the domain to compute
        :type domain: list
        :param active_test: whether the default filtering of records with ``active``
                            field set to ``False`` should be applied.
        :return: the query expressing the given domain as provided in domain
        :rtype: osv.query.Query
        """
        if not context:
            context = {}
        domain = domain[:]
        # if the object has a field named 'active', filter out all inactive
        # records unless they were explicitly asked for
        if 'active' in (self._columns.keys() + self._inherit_fields.keys()) and (active_test and context.get('active_test', True)):
            if domain:
                active_in_args = False
                for a in domain:
                    if a[0] == 'active':
                        active_in_args = True
                if not active_in_args:
                    domain.insert(0, ('active', '=', True))
            else:
                domain = [('active', '=', True)]

        qry = Query(tables=['"%s"' % self._table,])
        if domain:
            import expression
            e = expression.expression(domain, debug=self._debug)
            e.parse_into_query(cr, user, self, qry, context)
            if self._debug:
                _logger.debug("where calc of %s: qu1 = %s, qu2 = %s" % 
                        (self._table, qry.where_clause, qry.where_clause_params))
        
        return qry

    def _check_qorder(self, word):
        if not regex_order.match(word):
            raise except_orm('AccessError', _('Invalid "order" specified. A valid "order" specification is a comma-separated list of valid field names (optionally followed by asc/desc for the direction)'))
        return True

    def _apply_ir_rules(self, cr, uid, query, mode='read', context=None):
        """Add what's missing in ``query`` to implement all appropriate ir.rules
          (using the ``model_name``'s rules or the current model's rules if ``model_name`` is None)

           :param query: the current query object
        """
        if uid == 1: # one more shortcut
            return
        # apply main rules on the object
        rule_obj = self.pool.get('ir.rule')
        rules_list = []
        
        dom = rule_obj._compute_domain(cr, uid, self._name, mode)
        if dom:
            rules_list.append((dom, None, None))
            
        # apply ir.rules from the parents (through _inherits)
        for inherited_model in self._inherits:
            dom = rule_obj._compute_domain(cr, uid, inherited_model, mode)
            if dom:
                rules_list.append((dom, inherited_model, self))

        if rules_list:
            import expression
            for adom, parent_model, child_object in rules_list:
                if self._debug:
                    mname = self._name
                    if parent_model:
                        mname = "%s (%s)" %(parent_model, self._name)
                    _logger.debug("Add clause to %s: %r", mname, adom)
                if parent_model and child_object:
                    # as inherited rules are being applied, we need to add the missing JOIN
                    # to reach the parent table (if it was not JOINed yet in the query)
                    # We must use query.join() because only that method can properly
                    # handle the where clause.
                    parent_obj = self.pool.get(parent_model)
                    query.join((self._table, parent_obj._table, self._inherits[parent_model], 'id'),)
                aexp = expression.expression(adom)
                # Rest of computation is done as root, because we don't want to
                # recurse further into access limitations.
                aexp.parse_into_query(cr, 1, self, query, context)

    def _generate_m2o_order_by(self, order_field, query):
        """
        Add possibly missing JOIN to ``query`` and generate the ORDER BY clause for m2o fields,
        either native m2o fields or function/related fields that are stored, including
        intermediate JOINs for inheritance if required.

        :return: the qualified field name to use in an ORDER BY clause to sort by ``order_field``
        """
        if order_field not in self._columns and order_field in self._inherit_fields:
            # also add missing joins for reaching the table containing the m2o field
            qualified_field = self._inherits_join_calc(order_field, query)
            order_field_column = self._inherit_fields[order_field][2]
        else:
            qualified_field = '"%s"."%s"' % (self._table, order_field)
            order_field_column = self._columns[order_field]

        assert order_field_column._type == 'many2one', 'Invalid field passed to _generate_m2o_order_by()'
        if not order_field_column._classic_write and not getattr(order_field_column, 'store', False):
            logging.getLogger('orm.search').debug("Many2one function/related fields must be stored " \
                                                  "to be used as ordering fields! Ignoring sorting for %s.%s",
                                                  self._name, order_field)
            return

        # figure out the applicable order_by for the m2o
        dest_model = self.pool.get(order_field_column._obj)
        m2o_order = dest_model._order
        if not regex_order.match(m2o_order):
            # _order is complex, can't use it here, so we default to _rec_name
            m2o_order = dest_model._rec_name
        else:
            # extract the field names, to be able to qualify them and add desc/asc
            m2o_order_list = []
            for order_part in m2o_order.split(","):
                m2o_order_list.append(order_part.strip().split(" ",1)[0].strip())
            m2o_order = m2o_order_list

        # Join the dest m2o table if it's not joined yet. We use [LEFT] OUTER join here
        # as we don't want to exclude results that have NULL values for the m2o
        src_table, src_field = qualified_field.replace('"','').split('.', 1)
        query.join((src_table, dest_model._table, src_field, 'id'), outer=True)
        qualify = lambda field: '"%s"."%s"' % (dest_model._table, field)
        return map(qualify, m2o_order) if isinstance(m2o_order, list) else qualify(m2o_order)


    def _generate_order_by(self, order_spec, query):
        """
        Attempt to consruct an appropriate ORDER BY clause based on order_spec, which must be
        a comma-separated list of valid field names, optionally followed by an ASC or DESC direction.

        :raise" except_orm in case order_spec is malformed
        """
        order_by_clause = self._order
        if self._debug:
            _logger.debug('Generate order from %s and %s', self._order, order_spec)
        if order_spec:
            order_by_elements = []
            python_order = False
            self._check_qorder(order_spec)
            for order_part in order_spec.split(','):
                order_split = order_part.strip().split(' ')
                order_field = order_split[0].strip()
                if order_field.startswith('"') and order_field.endswith('"'):
                    order_field = order_field[1:-1]
                order_direction = order_split[1].strip() if len(order_split) == 2 else ''
                inner_clause = None
                if order_field in ('id', 'create_date', 'create_uid', 'write_date', 'write_uid', '_vptr'):
                    # builtin columns first
                    inner_clause = '"%s"."%s"' % (self._table, order_field)
                elif order_field in self._columns:
                    order_column = self._columns[order_field]
                    if order_column._classic_read:
                        inner_clause = '"%s"."%s"' % (self._table, order_field)
                    elif order_column._type == 'many2one' \
                            and (order_column._classic_write \
                                or getattr(order_column, 'store', False)):
                        inner_clause = self._generate_m2o_order_by(order_field, query)
                    else:
                        do_fallback = None
                        if hasattr(self, '_fallback_search'):
                            do_fallback = self._fallback_search
                        if do_fallback is None:
                            continue # ignore non-readable or "non-joinable" fields
                        elif do_fallback is True:
                            inner_clause = order_field
                            if order_column._type in ('many2one',):
                                inner_clause += ':'
                            python_order = True
                        else:
                            raise except_orm(_('Error!'), 
                                    _('Object model %s does not support order by function field "%s"!') % \
                                     (self._name, order_field))
                elif order_field in self._inherit_fields:
                    parent_obj = self.pool.get(self._inherit_fields[order_field][3])
                    order_column = parent_obj._columns[order_field]
                    if order_column._classic_read:
                        inner_clause = self._inherits_join_calc(order_field, query)
                    elif order_column._type == 'many2one' \
                            and (order_column._classic_write \
                                or getattr(order_column, 'store', False)):
                        inner_clause = self._generate_m2o_order_by(order_field, query)
                    else:
                        do_fallback = None
                        if hasattr(self, '_fallback_search'):
                            do_fallback = self._fallback_search
                        elif hasattr(parent_obj, '_fallback_search'):
                            do_fallback = parent_obj._fallback_search
                        if do_fallback is None:
                            continue # ignore non-readable or "non-joinable" fields
                        elif do_fallback is True:
                            inner_clause = order_field
                            if order_column._type in ('many2one',):
                                inner_clause += ':'
                            python_order = True
                        else:
                            raise except_orm(_('Error!'),
                                    _('Object model %s does not support order by function field "%s"!') % \
                                     (self._name, order_field))
                else:
                    raise except_orm(_('Error!'), _('Object model does not support order by "%s"!') % order_field)
                if inner_clause:
                    if isinstance(inner_clause, list):
                        for clause in inner_clause:
                            order_by_elements.append("%s %s" % (clause, order_direction))
                    else:
                        order_by_elements.append("%s %s" % (inner_clause, order_direction))
                    if self._debug:
                        _logger.debug("Order for %s: %r", self._name, order_by_elements[-1])
            if python_order and order_by_elements:
                return pythonOrderBy(order_by_elements)
            if order_by_elements:
                order_by_clause = ",".join(order_by_elements)

        return order_by_clause and (' ORDER BY %s ' % order_by_clause) or ''

    def _search(self, cr, user, args, offset=0, limit=None, order=None, context=None, count=False, access_rights_uid=None):
        """
        Private implementation of search() method, allowing specifying the uid to use for the access right check.
        This is useful for example when filling in the selection list for a drop-down and avoiding access rights errors,
        by specifying ``access_rights_uid=1`` to bypass access rights check, but not ir.rules!
        This is ok at the security level because this method is private and not callable through XML-RPC.

        :param access_rights_uid: optional user ID to use when checking access rights
                                  (not for ir.rules, this is only for ir.model.access)
        """
        if context is None:
            context = {}
        self.pool.get('ir.model.access').check(cr, access_rights_uid or user, self._name, 'read', context=context)

        query = self._where_calc(cr, user, args, context=context)
        self._apply_ir_rules(cr, user, query, 'read', context=context)
        order_by = self._generate_order_by(order, query)
        from_clause, where_clause, where_clause_params = query.get_sql()

        limit_str = limit and ' LIMIT %d' % limit or ''
        offset_str = offset and ' OFFSET %d' % offset or ''
        post_str = ''
        where_str = where_clause and (" WHERE %s" % where_clause) or ''

        if 'for update' in context and tools.server_bool.equals(context['for update'], True):
            post_str = ' FOR UPDATE'

        if count:
            cr.execute('SELECT count("%s".id) FROM ' % self._table +
                    from_clause + where_str + limit_str + offset_str + post_str,
                    where_clause_params,
                    debug=self._debug)
            res = cr.fetchall()
            return res[0][0]
        elif isinstance(order_by, pythonOrderBy):
            # Fall back to pythonic sorting (+ offset, limit)
            cr.execute('SELECT "%s".id FROM ' % self._table + from_clause +
                    where_str + post_str, # no offset or limit
                    where_clause_params, debug=self._debug)
            res = cr.fetchall()
            data_res = self.read(cr, user, [x[0] for x in res],
                        fields=order_by.get_fields(), context=context)
            # Repeat sorting in passes until all columns are satisfied
            while order_by.needs_more():
                ndir, nkey = order_by.get_next_sort()
                data_res.sort(key=nkey, reverse=not ndir)
            if offset:
                data_res = data_res[offset:]
            if limit:
                data_res = data_res[:limit]
            return [x['id'] for x in data_res]
        else:
            cr.execute('SELECT "%s".id FROM ' % self._table + from_clause +
                    where_str + order_by + limit_str + offset_str + post_str,
                    where_clause_params, debug=self._debug)
            res = cr.fetchall()
            return [x[0] for x in res]

    # returns the different values ever entered for one field
    # this is used, for example, in the client when the user hits enter on
    # a char field
    def distinct_field_get(self, cr, uid, field, value, args=None, offset=0, limit=None):
        if not args:
            args = []
        if field in self._inherit_fields:
            return self.pool.get(self._inherit_fields[field][0]).distinct_field_get(cr, uid, field, value, args, offset, limit)
        else:
            return self._columns[field].search(cr, self, args, field, value, offset, limit, uid)

    def copy_data(self, cr, uid, id, default=None, context=None):
        """
        Copy given record's data with all its fields values

        :param cr: database cursor
        :param user: current user id
        :param id: id of the record to copy
        :param default: field values to override in the original values of the copied record
        :type default: dictionary
        :param context: context arguments, like lang, time zone
        :type context: dictionary
        :return: dictionary containing all the field values
        """

        if context is None:
            context = {}

        # avoid recursion through already copied records in case of circular relationship
        seen_map = context.setdefault('__copy_data_seen',{})
        if id in seen_map.setdefault(self._name,[]):
            return
        seen_map[self._name].append(id)

        if default is None:
            default = {}
        if 'state' not in default:
            if 'state' in self._defaults:
                if callable(self._defaults['state']):
                    default['state'] = self._defaults['state'](self, cr, uid, context)
                else:
                    default['state'] = self._defaults['state']

        context_wo_lang = context.copy()
        if 'lang' in context:
            del context_wo_lang['lang']
        
        # First, prepare the columns we need to fetch from the old record
        copying_fields = []
        read_fields = [] # an even smaller list, to avoid unnecessary fetches
        for f in (self._columns.keys() + self._inherit_fields.keys()):
            if f in ('id', 'parent_left', 'parent_right'):
                # make sure we don't break the current parent_store structure and
                # force a clean recompute!
                continue
            if self._log_access and f in ('create_date', 'create_uid', 'write_date', 'write_uid'):
                continue
            if f in self._inherits.values():
                # Don't copy the inherits /foreign key/ field(s)
                continue
            if f in self._columns:
                field_col = self._columns[f]
            elif f in self._inherit_fields:
                field_col = self._inherit_fields[f][2]
            if isinstance(field_col, fields.function):
                continue
            copying_fields.append(f)
            if f not in default:
                read_fields.append(f)
            elif callable(default[f]):
                read_fields.append(f)

        if self._vtable:
            copying_fields.append('_vptr')
            read_fields.append('_vptr')

        data = self.read(cr, uid, [id,], fields=read_fields, context=context_wo_lang)
        if data:
            data = data[0]
        else:
            raise IndexError( _("Record #%d of %s not found, cannot copy!") %( id, self._name))

        if '_vptr' in default:
            # it won't be in copying_fields
            data['_vptr'] = default['_vptr']

        for f in copying_fields:
            if f in self._columns:
                field_col = self._columns[f]
            elif f in self._inherit_fields:
                field_col = self._inherit_fields[f][2]
            elif f == '_vptr':
                # leave as is, even though there is no corresponding column
                continue
                # TODO: shall we also copy inherited children, from virtual table?
            else:
                raise KeyError(f) # how did a column end up here?

            copy_fn = getattr(field_col, 'copy_data', False)
            if f in default:
                if callable(default[f]):
                    # it's a copying function! Same API as `copy_data` one..
                    res = default[f](cr, uid, obj=self, id=id, f=f, data=data, context=context)
                    if res is not None:
                        data[f] = res
                    else:
                        del data[f]
                else:
                    data[f] = default[f]
            elif copy_fn:
                if isinstance(copy_fn, basestring):
                    copy_fn = getattr(field_col, copy_fn)
                res = copy_fn(cr, uid, obj=self, id=id, f=f, data=data, context=context)
                if res is not None:
                    data[f] = res
                else:
                    del data[f]
            elif field_col._type == 'many2one': # catch both many2one and relations of many2one
                try:
                    data[f] = data[f] and data[f][0]
                except Exception:
                    pass
            elif isinstance(field_col, (fields.one2many, fields.one2one, fields.many2many)):
                # These fields should always define a copy_data()
                raise NotImplementedError('missing %s.copy_data()' % field_col._type)

        if '_vptr' in data and data['_vptr'] and data['_vptr'] != self._name:
            # We are just the baseclass of the real model the data belongs to.
            # Let's return the full data the object wants.
            # We call copy_data() again, but pass current data as defaults, so
            # that it needs not be computed again.
            vmodel = self.pool.get(data['_vptr'])
            assert vmodel, "Could not get model %s" % data['_vptr']
            vids = vmodel.search(cr, uid, [(vmodel._inherits[self._name],'=', id) ],
                                context=context_wo_lang)
            if self._debug:
                _logger.debug("Copying %s instead of %s#%d because it is virtual",
                                data['_vptr'], self._name, id)
            if len(vids) != 1:
                raise ValueError("More than 1 entries of %s#%d to %s" %(vmodel._name, id, self._name))
            data = vmodel.copy_data(cr, uid, vids[0], default=data, context=context_wo_lang)
            data['__vmodel_old_id'] = vids[0] # special value, for subsequent translations
        else:
            for d in data.keys():
                if not d in copying_fields:
                    data.pop(d)

        return data

    def copy_translations(self, cr, uid, old_id, new_id, context=None):
        if context is None:
            context = {}

        # avoid recursion through already copied records in case of circular relationship
        seen_map = context.setdefault('__copy_translations_seen',{})
        if old_id in seen_map.setdefault(self._name,[]):
            return
        seen_map[self._name].append(old_id)

        trans_obj = self.pool.get('ir.translation')
        fields = self.fields_get(cr, uid, context=context)

        translation_names = []
        for field_name, field_def in fields.items():
            # we must recursively copy the translations for o2o and o2m
            if field_def['type'] in ('one2one', 'one2many'):
                target_obj = self.pool.get(field_def['relation'])
                old_record, new_record  = self.read(cr, uid, [old_id, new_id], [field_name], context=context)
                # here we rely on the order of the ids to match the translations
                # as foreseen in copy_data()
                old_children = sorted(old_record[field_name])
                new_children = sorted(new_record[field_name])
                for (old_child, new_child) in zip(old_children, new_children):
                    target_obj.copy_translations(cr, uid, old_child, new_child, context=context)
            # and for translatable fields we keep them for copy
            elif field_def.get('translate'):
                trans_name = ''
                if field_name in self._columns:
                    trans_name = self._name + "," + field_name
                elif field_name in self._inherit_fields:
                    trans_name = self._inherit_fields[field_name][0] + "," + field_name
                if trans_name:
                    translation_names.append(trans_name)

        if translation_names:
            # first, find all ids of translations that already exist
            trans_exist_ids = trans_obj.search(cr, uid, [
                    ('name', 'in', translation_names),
                    ('res_id', '=', new_id)
                ])
            if trans_exist_ids:
                # Remove from the list the names that are already translated
                for res in trans_obj.read(cr,uid, trans_exist_ids, ['name'], context=context):
                    translation_names.remove(res['name'])
            
            trans_ids = []
            if translation_names:
                # then, locate the ones that we need to copy
                trans_ids = trans_obj.search(cr, uid, [
                        ('name', 'in', translation_names),
                        ('res_id', '=', old_id)
                    ])
            if trans_ids:
                for record in trans_obj.read(cr, uid, trans_ids, context=context):
                    del record['id']
                    record['res_id'] = new_id
                    trans_obj.create(cr, uid, record, context=context)


    def copy(self, cr, uid, id, default=None, context=None):
        """
        Duplicate record with given id updating it with default values

        :param id: id of the record to copy. Must be a single one. For compatibility with
                browse methods, this fn accepts list/tuple of ids, but length must be 1.
        :param default: dictionary of field values to override in the original values of the copied record, e.g: ``{'field_name': overriden_value, ...}``
        :type default: dictionary
        :param context: context arguments, like lang, time zone
        :type context: dictionary
        :return: id of new record

        """
        if context is None:
            context = {}
        if isinstance(id, (tuple, list)):
            if len(id) != 1:
                raise ValueError("Invalid number (%s) of ids for the orm.copy() method" % len(id))
            id = id[0]
        context = context.copy()
        data = self.copy_data(cr, uid, id, default, context)
        if self._vtable and data.get('_vptr', False) and data['_vptr'] != self._name:
            vmodel = self.pool.get(data['_vptr'])
            vnew_id = vmodel.create(cr, uid, data, context)
            vfld = vmodel._inherits[self._name]
            new_id = vmodel.read(cr, uid, [vnew_id,], fields=[vfld], context=context)[0][vfld][0]
            if '__vmodel_old_id' in data:
                vold_id = data.pop('__vmodel_old_id')
                vmodel.copy_translations(cr, uid, vold_id, vnew_id, context)
            else:
                self.copy_translations(cr, uid, id, new_id, context)
        else:
            new_id = self.create(cr, uid, data, context)
            self.copy_translations(cr, uid, id, new_id, context)
        return new_id

    def exists(self, cr, uid, ids, context=None):
        """ Validate that all `ids` exist as database records
        """
        if type(ids) in (int, long):
            ids = [ids]
        query = 'SELECT COUNT(id) FROM "%s"  WHERE ID = ANY(%%s)' % (self._table)
        cr.execute(query, (ids,), debug=self._debug)
        return cr.fetchone()[0] == len(ids)

    def check_recursion(self, cr, uid, ids, context=None, parent=None):
        warnings.warn("You are using deprecated %s.check_recursion(). Please use the '_check_recursion()' instead!" % \
                        self._name, DeprecationWarning, stacklevel=3)
        assert parent is None or parent in self._columns or parent in self._inherit_fields,\
                    "The 'parent' parameter passed to check_recursion() must be None or a valid field name"
        return self._check_recursion(cr, uid, ids, context, parent)

    def _check_recursion(self, cr, uid, ids, context=None, parent=None):
        """
        Verifies that there is no loop in a hierarchical structure of records,
        by following the parent relationship using the **parent** field until a loop
        is detected or until a top-level record is found.

        :param cr: database cursor
        :param uid: current user id
        :param ids: list of ids of records to check
        :param parent: optional parent field name (default: ``self._parent_name = parent_id``)
        :return: **True** if the operation can proceed safely, or **False** if an infinite loop is detected.
        """

        if not parent:
            parent = self._parent_name
        if isinstance(ids, (long, int)):
            ids = [ids,]
        if cr.pgmode >= 'pg84':
            # Recursive search, all inside postgres. The first part will fetch all
            # ids, the others will fetch parents, until some path contains the
            # id two times. Then, cycle -> True and not recurse further.
            cr.execute("""WITH RECURSIVE %(t)s_crsrc(parent_id, _rsrc_path, _rsrc_cycle) AS
            ( SELECT "%(t)s"."%(p)s" AS parent_id, ARRAY[id], False
                FROM "%(t)s"  WHERE id = ANY(%%s)
             UNION ALL SELECT "%(t)s"."%(p)s" AS parent_id, _rsrc_path || id, id = ANY(_rsrc_path)
                FROM "%(t)s", %(t)s_crsrc
                WHERE "%(t)s".id = %(t)s_crsrc.parent_id
                  AND %(t)s_crsrc._rsrc_cycle = False)
            SELECT 1 from %(t)s_crsrc WHERE _rsrc_cycle = True; """ %  \
                { 't':self._table, 'p': parent},
                (only_ids(ids),), debug=self._debug)
            res = cr.fetchone()
            return not (res and res[0])
        ids_parent = ids[:]
        while len(ids_parent):
            ids_parent2 = []
            for i in range(0, len(ids), cr.IN_MAX):
                sub_ids_parent = ids_parent[i:i+cr.IN_MAX]
                cr.execute('SELECT distinct "'+parent+'"'+
                    ' FROM "'+self._table+'" ' \
                    'WHERE id = ANY(%s)',(sub_ids_parent,), debug=self._debug)
                ids_parent2.extend(filter(None, map(itemgetter(0), cr.fetchall())))
            ids_parent = ids_parent2
            for i in ids_parent:
                if i in ids:
                    return False
        return True

    def _get_xml_ids(self, cr, uid, ids, *args, **kwargs):
        """Find out the XML ID(s) of any database record.

        **Synopsis**: ``_get_xml_ids(cr, uid, ids) -> { 'id': ['module.xml_id'] }``

        :return: map of ids to the list of their fully qualified XML IDs
                 (empty list when there's none).
        
        Note: Pending deprecation!
        """
        model_data_obj = self.pool.get('ir.model.data')
        data_ids = model_data_obj.search(cr, uid, [('model', '=', self._name), ('res_id', 'in', ids), ('source', '=', 'xml')])
        data_results = model_data_obj.read(cr, uid, data_ids, ['module', 'name', 'res_id'])
        result = {}
        for id in ids:
            # can't use dict.fromkeys() as the list would be shared!
            result[id] = []
        for record in data_results:
            result[record['res_id']].append('%(module)s.%(name)s' % record)
        return result

    def get_xml_id(self, cr, uid, ids, *args, **kwargs):
        """Find out the XML ID of any database record, if there
        is one. This method works as a possible implementation
        for a function field, to be able to add it to any
        model object easily, referencing it as ``osv.osv.get_xml_id``.

        When multiple XML IDs exist for a record, only one
        of them is returned (randomly).

        **Synopsis**: ``get_xml_id(cr, uid, ids) -> { 'id': 'module.xml_id' }``

        :return: map of ids to their fully qualified XML ID,
                 defaulting to an empty string when there's none
                 (to be usable as a function field).
        
        Note: pending deprecation!
        """
        results = self._get_xml_ids(cr, uid, ids)
        for k, v in results.items():
            if results[k]:
                results[k] = v[0]
            else:
                results[k] = ''
        return results

    def get_last_modified(self, cr, user, args, context=None, access_rights_uid=None):
        """Return the last modification date of objects in 'domain'
        This function has similar semantics to orm.search(), apart from the
        limit, offset and order arguments, which make no sense here.
        It is useful when we want to find if the table (aka set of records)
        has any modifications we should update at the client.
        """
        if context is None:
            context = {}
        self.pool.get('ir.model.access').check(cr, access_rights_uid or user, self._name, 'read', context=context)

        query = self._where_calc(cr, user, args, context=context)
        self._apply_ir_rules(cr, user, query, 'read', context=context)
        from_clause, where_clause, where_clause_params = query.get_sql()

        where_str = where_clause and (" WHERE %s" % where_clause) or ''

        cr.execute('SELECT MAX(COALESCE("%s".write_date, "%s".create_date)) FROM ' % (self._table, self._table) + 
                    from_clause + where_str ,
                    where_clause_params,
                    debug=self._debug)
        res = cr.fetchall()
        return res[0][0]

class orm_deprecated(object):
    """ Mix-in for deprecated models.

    Add this class as the first baseclass for your object, so that deprecation
    warnings are issued against using this ORM model.

    Example::

        class my_old_class(orm.orm_deprecated, osv.osv):
            def __init__(...):
                ...
    """
    def __init__(self, *args, **kwargs):
        self.__depr_warned = False
        super(orm_deprecated, self).__init__(*args, **kwargs)

    def __issue_depr(self):
        if self._debug or not self.__depr_warned:
            warnings.warn("You are using deprecated class %s. Please port your code!" % \
                            self._name,
                      DeprecationWarning, stacklevel=3)
            self.__depr_warned = True

    def read(self, *args, **kwargs):
        self.__issue_depr()
        return super(orm_deprecated, self).read(*args, **kwargs)
    def write(self, *args, **kwargs):
        self.__issue_depr()
        return super(orm_deprecated, self).write(*args, **kwargs)
    def copy(self, *args, **kwargs):
        self.__issue_depr()
        return super(orm_deprecated, self).copy(*args, **kwargs)
    def search(self, *args, **kwargs):
        self.__issue_depr()
        return super(orm_deprecated, self).search(*args, **kwargs)
    def unlink(self, *args, **kwargs):
        self.__issue_depr()
        return super(orm_deprecated, self).unlink(*args, **kwargs)

class orm_abstract(orm_template):
    """ Abstract Model

        See `osv.osv_abstract`

        Most methods in this class iterate over the `_child_models` and perform
        the operation on each one of them.
    """
     #   TODO: write a decorator for that iteration

    _inherit_fields = {}

    def __init__(self, cr):
        super(orm_abstract, self).__init__(cr)
        self._child_models = []

        # Load manual fields
        if True:
            cr.execute('SELECT * FROM ir_model_fields WHERE model=%s AND state=%s', (self._name, 'manual'))
            for field in cr.dictfetchall():
                raise ValueError("Manual fields are not allowed for abstract models!")

    def _check_access(self, uid, object_id, mode):
        raise NotImplementedError

    def read(self, cr, user, ids, fields_to_read=None, context=None, load='_classic_read'):
        raise NotImplementedError

    def write(self, cr, user, ids, vals, context=None):
        raise NotImplementedError

    def create(self, cr, user, vals, context=None):
        raise NotImplementedError

    def _where_calc(self, cr, user, args, active_test=True, context=None):
        raise NotImplementedError

    def _search(self, cr, user, args, offset=0, limit=None, order=None, context=None, count=False, access_rights_uid=None):
        raise NotImplementedError

    def unlink(self, cr, uid, ids, context=None):
        raise NotImplementedError

    def perm_read(self, cr, user, ids, context=None, details=True):
        raise NotImplementedError

    def _check_removed_columns(self, cr, log=False):
        pass

    def exists(self, cr, uid, ids, context=None):
        raise NotImplementedError

    # Force inheritance (see freaky note above)
    def _auto_init_prefetch(self, schema, context=None):
        return orm_template._auto_init_prefetch(self, schema=schema, context=context)

    def _field_model2db(self, cr,context=None):
        return orm_template._field_model2db(self, cr, context=context)

    def _auto_init(self, cr, context=None):
        pass

    _auto_init.deferrable = True #: magic attribute for init algorithm

    def _auto_init_sql(self, schema, context=None):
        return orm_template._auto_init_sql(self, schema, context=context)

    def browse(self, cr, uid, select, context=None, list_class=None, 
                fields_process=None, fields_only=FIELDS_ONLY_DEFAULT, cache=None):
        raise NotImplementedError

    def _append_child(self, cname):
        """Declare that model `cname` inherits from us
        """

        if cname not in self._child_models:
            self._child_models.append(cname)

    def _verify_implementations(self, new_models):
        """ If any of `new_models` is an implementation of us, verify it
        """
        if self._debug:
            _logger.debug("Verifying potential implementations of %s", self._name)
        for nmodel in new_models:
            if nmodel._name in self._child_models:
                if nmodel._debug:
                    _logger.debug("Verifying %s implementation of %s", nmodel._name, self._name)

                for cn, col in self._columns.items():
                    if cn in nmodel._columns:
                        dcol = nmodel._columns[cn]
                    elif cn in nmodel._inherit_fields:
                        dcol = nmodel._inherit_fields[2]
                    else:
                        raise KeyError('Model %s doesn\'t implement column "%s" as in %s.' % \
                                    (nmodel._name, cn, self._name))
                    col._verify_model(dcol, nmodel._name, cn)

                for fn_name, fn_abs in self.__class__.__dict__.items():
                    if fn_name in ('_append_child', '_verify_implementations'):
                        continue
                    if not callable(fn_abs):
                        continue
                    dest_fn = getattr(nmodel.__class__, fn_name, False)
                    if not dest_fn:
                        raise KeyError("Model %s does not define %s(), required for %s" % \
                                (nmodel._name, fn_name, self._name))

                    # Check footprint:
                    tools.func.fn_implements(dest_fn, fn_abs)
        pass

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

