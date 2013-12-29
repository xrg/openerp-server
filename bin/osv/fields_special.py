# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2010-2011 OpenERP SA. (www.openerp.com)
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

#.apidoc title: Special fields

from fields import _column, register_field_classes
import tools
from psycopg2 import Binary
from fields_function import function
from tools.safe_eval import safe_eval as eval

class binary(_column):
    _type = 'binary'
    _sql_type = 'bytea'
    _symbol_f = lambda symb: symb and Binary(str(symb)) or None
    _symbol_c = '%s::BYTEA'
    _symbol_set = (_symbol_c, _symbol_f)
    _symbol_get = lambda self, x: x and str(x)

    _classic_read = False
    _prefetch = False

    def __init__(self, string='unknown', filters=None, **args):
        _column.__init__(self, string=string, **args)
        self.filters = filters

    def get_memory(self, cr, obj, ids, name, user=None, context=None, values=None):
        if not context:
            context = {}
        if not values:
            values = []
        res = {}
        for i in ids:
            val = None
            for v in values:
                if v['id'] == i:
                    val = v[name]
                    break

            # If client is requesting only the size of the field, we return it instead
            # of the content. Presumably a separate request will be done to read the actual
            # content if it's needed at some point.
            # TODO: after 6.0 we should consider returning a dict with size and content instead of
            #       having an implicit convention for the value
            if val and context.get('bin_size_%s' % name, context.get('bin_size')):
                res[i] = tools.human_size(long(val))
            else:
                res[i] = val
        return res

    get = get_memory

class selection(_column):
    _type = 'selection'
    _sql_type = None
    merge_op = 'eq'

    @classmethod
    def _get_sql_type(cls, selection, def_size):
        """Compute the sql type based on selection list and size
        """
        if isinstance(selection, list) and isinstance(selection[0][0], (str, unicode)):
            f_size = reduce(lambda x, y: max(x, len(y[0])), selection, def_size or 16)
        elif isinstance(selection, list) and isinstance(selection[0][0], int):
            f_size = -1
        else:
            f_size = def_size or 16

        if f_size == -1:
            return 'INTEGER',  None
        else:
            return 'VARCHAR', f_size

    @classmethod
    def from_manual(cls, field_dict, attrs):
        return cls(eval(field_dict['selection']), **attrs)

    def __init__(self, selection, string='unknown', **args):
        _column.__init__(self, string=string, **args)
        self.selection = selection
        self._sql_type, self.size = self._get_sql_type(selection, getattr(self, 'size', None))

    def _get_field_def(self, cr, uid, name, obj, ret, context=None):
        super(selection, self)._get_field_def(cr, uid, name, obj, ret, context=context)
        if isinstance(self.selection, (tuple, list)):
            translation_obj = obj.pool.get('ir.translation')
            # translate each selection option
            sel_vals = []
            sel2 = []
            for (key, val) in self.selection:
                if val:
                    sel_vals.append(val)

            if context and context.get('lang', False):
                sel_dic =  translation_obj._get_multisource(cr, uid,
                            obj._name + ',' + name, 'selection',
                            context['lang'], sel_vals)
            else:
                sel_dic = {}

            for key, val in self.selection:
                sel2.append((key, sel_dic.get(val, val)))
            ret['selection'] = sel2
        else:
            # call the 'dynamic selection' function
            ret['selection'] = self.selection(obj, cr, uid, context)


class serialized(_column):
    """Serialized fields
    
        Pending deprecation?
    """
    
    _sql_type = 'text'

    def __init__(self, string='unknown', serialize_func=repr, deserialize_func=eval, type='text', **args):
        self._serialize_func = serialize_func
        self._deserialize_func = deserialize_func
        self._type = type
        self._symbol_set = (self._symbol_c, self._serialize_func)
        self._symbol_get = self._deserialize_func
        super(serialized, self).__init__(string=string, **args)


try:
    import json
    def _symbol_set_struct(val):
        return json.dumps(val)

    def _symbol_get_struct(self, val):
        if not val:
            return None
        return json.loads(val)
except ImportError:
    def _symbol_set_struct(val):
        raise NotImplementedError

    def _symbol_get_struct(self, val):
        raise NotImplementedError

class struct(_column):
    """ A field able to store an arbitrary python data structure.
    
        Note: only plain components allowed.
    """
    _type = 'struct'
    _sql_type = 'text'
    merge_op = 'eq'

    _symbol_c = '%s'
    _symbol_f = _symbol_set_struct
    _symbol_set = (_symbol_c, _symbol_f)
    _symbol_get = _symbol_get_struct

# TODO: review completly this class for speed improvement

class property(function):
    merge_op = '|eq'

    def _get_default(self, obj, cr, uid, prop_name, context=None):
        return self._get_defaults(obj, cr, uid, [prop_name], context=None)[0][prop_name]

    def _get_defaults(self, obj, cr, uid, prop_name, context=None):
        prop = obj.pool.get('ir.property')
        domain = [('fields_id.model', '=', obj._name), ('fields_id.name','in',prop_name), ('res_id','=',False)]
        ids = prop.search(cr, uid, domain, context=context)
        replaces = {}
        default_value = {}.fromkeys(prop_name, False)
        for prop_rec in prop.browse(cr, uid, ids, context=context):
            if default_value.get(prop_rec.fields_id.name, False):
                continue
            value = prop.get_by_record(cr, uid, prop_rec, context=context) or False
            default_value[prop_rec.fields_id.name] = value
            if value and (prop_rec.type == 'many2one'):
                replaces.setdefault(value._name, {})
                replaces[value._name][value.id] = True
        return default_value, replaces

    def _get_by_id(self, obj, cr, uid, prop_name, ids, context=None):
        prop = obj.pool.get('ir.property')
        vids = [obj._name + ',' + str(oid) for oid in  ids]

        domain = [('fields_id.model', '=', obj._name), ('fields_id.name','in',prop_name)]
        #domain = prop._get_domain(cr, uid, prop_name, obj._name, context)
        if vids:
            domain = [('res_id', 'in', vids)] + domain
        return prop.search(cr, uid, domain, context=context)

    # TODO: to rewrite more clean
    def _fnct_write(self, obj, cr, uid, id, prop_name, id_val, obj_dest, context=None):
        if context is None:
            context = {}

        nids = self._get_by_id(obj, cr, uid, [prop_name], [id], context)
        if nids:
            cr.execute('DELETE FROM ir_property WHERE id IN %s', (tuple(nids),))

        default_val = self._get_default(obj, cr, uid, prop_name, context)
        #property_create = False
        #if isinstance(default_val, osv.orm.browse_record):
        #    if default_val.id != id_val:
        #        property_create = True
        #elif default_val != id_val:
        #    property_create = True

        if id_val is not default_val: # property_create
            def_id = self._field_get(cr, uid, obj._name, prop_name)
            company = obj.pool.get('res.company')
            cid = company._company_default_get(cr, uid, obj._name, def_id,
                                               context=context)
            propdef = obj.pool.get('ir.model.fields').browse(cr, uid, def_id,
                                                             context=context)
            prop = obj.pool.get('ir.property')
            return prop.create(cr, uid, {
                'name': propdef.name,
                'value': id_val,
                'res_id': obj._name+','+str(id),
                'company_id': cid,
                'fields_id': def_id,
                'type': self._type,
            }, context=context)
        return False


    def _fnct_read(self, obj, cr, uid, ids, prop_name, obj_dest, context=None):
        from orm import only_ids
        properties = obj.pool.get('ir.property')
        domain = [('fields_id.model', '=', obj._name), ('fields_id.name','in',prop_name)]
        default_val,replaces = self._get_defaults(obj, cr, uid, prop_name, context)

        res = {}
        dnids = []
        for id in only_ids(ids):
            res[id] = default_val.copy()
            dnids.append(obj._name + ',' + str(id))
        domain += [('res_id','in', dnids)]

        for prop in properties.browse(cr, uid, domain, context=context):
            value = properties.get_by_record(cr, uid, prop, context=context)
            res[prop.res_id.id][prop.fields_id.name] = value or False
            if value and (prop.type == 'many2one'):
                record_exists = obj.pool.get(value._name).exists(cr, uid, value.id)
                if record_exists:
                    replaces.setdefault(value._name, {})
                    replaces[value._name][value.id] = True
                else:
                    res[prop.res_id.id][prop.fields_id.name] = False

        for rep in replaces:
            nids = obj.pool.get(rep).search(cr, uid, [('id','in',replaces[rep].keys())], context=context)
            replaces[rep] = dict(obj.pool.get(rep).name_get(cr, uid, nids, context=context))

        for prop in prop_name:
            for id in ids:
                if res[id][prop] and hasattr(res[id][prop], '_name'):
                    res[id][prop] = (res[id][prop].id , replaces[res[id][prop]._name].get(res[id][prop].id, False))

        return res


    def _field_get(self, cr, uid, model_name, prop):
        if not self.field_id.get(cr.dbname):
            cr.execute('SELECT id \
                    FROM ir_model_fields \
                    WHERE name=%s AND model=%s', (prop, model_name))
            res = cr.fetchone()
            self.field_id[cr.dbname] = res and res[0]
        return self.field_id[cr.dbname]

    def __init__(self, obj_prop, **args):
        # TODO remove obj_prop parameter (use many2one type)
        self.field_id = {}
        function.__init__(self, self._fnct_read, False, self._fnct_write,
                          obj_prop, multi='properties', **args)

    def post_init(self, cr, name, obj):
        super(property, self).post_init(cr, name, obj)
        self.field_id = {}

register_field_classes(binary, selection, serialized, struct, property)

#eof