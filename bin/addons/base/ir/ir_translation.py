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

from osv import fields, osv
import tools

TRANSLATION_TYPE = [
    ('field', 'Field'),
    ('model', 'Object'),
    ('rml', 'RML'),
    ('selection', 'Selection'),
    ('view', 'View'),
    ('wizard_button', 'Wizard Button'),
    ('wizard_field', 'Wizard Field'),
    ('wizard_view', 'Wizard View'),
    ('xsl', 'XSL'),
    ('help', 'Help'),
    ('code', 'Code'),
    ('constraint', 'Constraint'),
    ('sql_constraint', 'SQL Constraint')
]

class ir_translation(osv.osv):
    _name = "ir.translation"
    _log_access = False

    def _get_language(self, cr, uid, context):
        lang_obj = self.pool.get('res.lang')
        lang_ids = lang_obj.search(cr, uid, [('translatable', '=', True)],
                context=context)
        langs = lang_obj.browse(cr, uid, lang_ids, context=context)
        res = [(lang.code, lang.name) for lang in langs]
        for lang_dict in tools.scan_languages():
            if lang_dict not in res:
                res.append(lang_dict)
        return res

    _columns = {
        'name': fields.char('Field Name', size=128, required=True),
        'res_id': fields.integer('Resource ID', select=True),
        'lang': fields.selection(_get_language, string='Language', size=5),
        'type': fields.selection(TRANSLATION_TYPE, string='Type', size=16, select=True),
        'src': fields.text('Source'),
        'value': fields.text('Translation Value'),
    }

    def _auto_init(self, cr, context={}):
        super(ir_translation, self)._auto_init(cr, context)
        cr.execute('SELECT indexname FROM pg_indexes WHERE indexname = %s', ('ir_translation_ltn',))
        if not cr.fetchone():
            cr.execute('CREATE INDEX ir_translation_ltn ON ir_translation (lang, type, name)')
            cr.commit()

        cr.execute('SELECT indexname FROM pg_indexes WHERE indexname = %s', ('ir_translation_src',))
        if not cr.fetchone():
            cr.execute('CREATE INDEX ir_translation_src ON ir_translation USING hash (src)')
            cr.commit()

    @tools.cache(skiparg=3, multi='ids')
    def _get_ids(self, cr, uid, name, tt, lang, ids):
        translations = dict.fromkeys(ids, False)
        if ids:
            cr.execute('SELECT res_id,value ' \
                    'FROM ir_translation ' \
                    'WHERE lang=%s ' \
                        'AND type=%s ' \
                        'AND name=%s ' \
                        'AND res_id = ANY(%s) ' \
                        # "AND value IS NOT NULL AND value <> '' "
                        ,
                    (lang,tt,name, ids), debug=self._debug)
            for res_id, value in cr.fetchall():
                translations[res_id] = value
        return translations

    def _set_ids(self, cr, uid, name, tt, lang, ids, value, src=None):
        # clear the caches
        tr = self._get_ids(cr, uid, name, tt, lang, ids)
        for res_id in tr:
            if tr[res_id]:
                self._get_source.clear_cache(cr.dbname, uid, name, tt, lang, tr[res_id])
        self._get_source.clear_cache(cr.dbname, uid, name, tt, lang)
        self._get_ids.clear_cache(cr.dbname, uid, name, tt, lang, ids)

        cr.execute('DELETE FROM ir_translation ' \
                'WHERE lang=%s ' \
                    'AND type=%s ' \
                    'AND name=%s ' \
                    'AND res_id = ANY (%s)',
                (lang,tt,name, ids), debug=self._debug)
        for id in ids:
            self.create(cr, uid, {
                'lang':lang,
                'type':tt,
                'name':name,
                'res_id':id,
                'value':value,
                'src':src,
                })
        return len(ids)

    @tools.cache(skiparg=3)
    def _get_source(self, cr, uid, name, tt, lang, source=None):
        """
        Returns the translation for the given combination of name, type, language
        and source. All values passed to this method should be unicode (not byte strings),
        especially ``source``.

        :param name: identification of the term to translate, such as field name
        :param type: type of term to translate (see ``type`` field on ir.translation)
        :param lang: language code of the desired translation
        :param source: optional source term to translate (should be unicode)
        :rtype: unicode
        :return: the request translation, or an empty unicode string if no translation was
                 found and `source` was not passed
        """
        # FIXME: should assert that `source` is unicode and fix all callers to always pass unicode
        # so we can remove the string encoding/decoding.

        if not lang:
            return u''
        if source:
            cr.execute('SELECT value ' \
                    'FROM ir_translation ' \
                    'WHERE lang=%s ' \
                        'AND type=%s ' \
                        'AND name=%s ' \
                        'AND src=%s ' \
                        "AND value IS NOT NULL AND value <> '' ",
                    (lang, tt, tools.ustr(name), source), debug=self._debug)
        else:
            cr.execute('SELECT value ' \
                    'FROM ir_translation ' \
                    'WHERE lang=%s ' \
                        'AND type=%s ' \
                        'AND name=%s ' \
                        "AND value IS NOT NULL AND value <> '' ",
                    (lang, tt, tools.ustr(name)), debug=self._debug)
        res = cr.fetchone()
        trad = res and res[0] or u''
        return trad

    def _get_multisource(self, cr, uid, name, tt, lang, src_list):
        """ Retrieve translations for a list of sources.
            returns a /dictionary/ of the src: val pairs
        """
        assert lang

        cr.execute('SELECT src, value ' \
                    'FROM ir_translation ' \
                    'WHERE lang=%s ' \
                        'AND type=%s ' \
                        'AND name=%s ' \
                        'AND src = ANY(%s) ' \
                        "AND value IS NOT NULL AND value <> '' ",
                    (lang, tt, tools.ustr(name), src_list), debug=self._debug)
        
        res = {}
        for row in cr.fetchall():
            res[row[0]] = row[1]
        
        return res

    def _get_multifield(self, cr, user, fld_list, lang, prepend=None):
        """ return multiple (field) results, for a list of (name, type) tuples,
            where language is constant.
            If prepend is specified, prepend that to the name of each tuple.
            
            Returns a list of (name, type, trans) tuples, where the name does
            not contain the prepend string.
        """
        assert(lang)
        
        if not fld_list:
            return []
        
        if prepend:
            fl2 = []
            for name, tt in fld_list:
                fl2.append( (prepend + name, tt) )
            nexpr = 'substr( name, %d) as name' % (len(prepend) + 1)
        else:
            fl2 = fld_list
            nexpr = 'name'
            
        cr.execute('SELECT ' + nexpr + ', type, value ' \
                    'FROM ir_translation ' \
                    'WHERE lang=%s ' \
                       'AND  (name, type) IN %s ' \
                       "AND value IS NOT NULL AND value <> '' ",
                    (lang, tuple(fl2)), debug=self._debug)
        
        res = []
        for row in cr.fetchall():
            res.append(tuple(row))
        
        return res


    def _get_multi_ids(self, cr, user, name_list, ids, ttype, lang, prepend=None):
        """ return multiple results, for a CROSS of names and ids
            where language and type is constant.
            If prepend is specified, prepend that to the name of each tuple.
            
            name_list and ids are simple lists of strings and ints, respectively.
            Returns a list of (name, id, trans) tuples, where the name does
            not contain the prepend string.
            Note: it /may/ return less than (name_list * ids) results, when
            some translations are not available.
        """
        assert(lang)
        
        if prepend:
            fl2 = map(lambda x: prepend + x, name_list)
            nexpr = 'substr( name, %d) as name' % (len(prepend) + 1)
        else:
            fl2 = name_list
            nexpr = 'name'
            
        cr.execute('SELECT ' + nexpr + ', res_id, value ' \
                    'FROM ir_translation ' \
                    'WHERE lang=%s AND type = %s '\
                    ' AND name = ANY(%s) AND res_id = ANY(%s) '
                    "AND value IS NOT NULL AND value <> '' ",
                    (lang, ttype, fl2, ids), debug=self._debug)
        
        res = []
        for row in cr.fetchall():
            res.append(tuple(row))
        
        return res

    def create(self, cursor, user, vals, context=None):
        if not context:
            context = {}
        ids = super(ir_translation, self).create(cursor, user, vals, context=context)
        for trans_obj in self.read(cursor, user, [ids], ['name','type','res_id','src','lang'], context=context):
            self._get_source.clear_cache(cursor.dbname, user, trans_obj['name'], trans_obj['type'], trans_obj['lang'], source=trans_obj['src'])
            self._get_ids.clear_cache(cursor.dbname, user, trans_obj['name'], trans_obj['type'], trans_obj['lang'], [trans_obj['res_id']])
        return ids

    def write(self, cursor, user, ids, vals, context=None):
        if not context:
            context = {}
        if isinstance(ids, (int, long)):
            ids = [ids]
        result = super(ir_translation, self).write(cursor, user, ids, vals, context=context)
        for trans_obj in self.read(cursor, user, ids, ['name','type','res_id','src','lang'], context=context):
            self._get_source.clear_cache(cursor.dbname, user, trans_obj['name'], trans_obj['type'], trans_obj['lang'], source=trans_obj['src'])
            self._get_ids.clear_cache(cursor.dbname, user, trans_obj['name'], trans_obj['type'], trans_obj['lang'], [trans_obj['res_id']])
        return result

    def unlink(self, cursor, user, ids, context=None):
        if not context:
            context = {}
        if isinstance(ids, (int, long)):
            ids = [ids]
        for trans_obj in self.read(cursor, user, ids, ['name','type','res_id','src','lang'], context=context):
            self._get_source.clear_cache(cursor.dbname, user, trans_obj['name'], trans_obj['type'], trans_obj['lang'], source=trans_obj['src'])
            self._get_ids.clear_cache(cursor.dbname, user, trans_obj['name'], trans_obj['type'], trans_obj['lang'], [trans_obj['res_id']])
        result = super(ir_translation, self).unlink(cursor, user, ids, context=context)
        return result

ir_translation()

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

