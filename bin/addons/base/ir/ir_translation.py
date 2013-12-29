# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2013 P. Christeas <xrg@hellug.gr>
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

from osv import fields, osv, index
import tools
import logging

TRANSLATION_TYPE = [
    ('field', 'Field'),
    ('model', 'Object'),
    ('rml', 'RML  (deprecated - use Report)'), # Pending deprecation - to be replaced by report!
    ('report', 'Report/Template'),
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

class ir_translation_import_cursor(object):
    """Temporary cursor for optimizing mass insert into ir.translation

    Open it (attached to a sql cursor), feed it with translation data and
    finish() it in order to insert multiple translations in a batch.
    """
    _table_name = 'tmp_ir_translation_import'

    def __init__(self, cr, uid, parent, context):
        """ Initializer

        Store some values, and also create a temporary SQL table to accept
        the data.
        @param parent an instance of ir.translation ORM model
        """

        self._cr = cr
        self._uid = uid
        self._context = context
        self._overwrite = context.get('overwrite', False)
        self._debug = parent._debug
        self._parent_table = parent._table

        # Note that Postgres will NOT inherit the constraints or indexes
        # of ir_translation, so this copy will be much faster.

        cr.execute('''CREATE TEMP TABLE %s(
            imd_model VARCHAR(64),
            imd_module VARCHAR(64),
            imd_name VARCHAR(128)
            ) INHERITS (%s) ''' % (self._table_name, self._parent_table),
            debug=self._debug)

    def push(self, ddict):
        """Feed a translation, as a dictionary, into the cursor
        """

        self._cr.execute("INSERT INTO " + self._table_name \
                + """(name, lang, res_id, src, type,
                        imd_model, imd_module, imd_name, value)
                VALUES(%(name)s, %(lang)s, %(res_id)s, %(src)s, %(type)s, %(imd_model)s, %(imd_module)s, %(imd_name)s, %(value)s)""",
                ddict,
                debug=self._debug)

    def finish(self):
        """ Transfer the data from the temp table to ir.translation
        """
        logger = logging.getLogger('orm')

        cr = self._cr
        if self._debug:
            cr.execute("SELECT count(*) FROM %s" % self._table_name)
            c = cr.fetchone()[0]
            logger.debug("ir.translation.cursor: We have %d entries to process", c)

        # Step 1: resolve ir.model.data references to res_ids
        cr.execute("""UPDATE %s AS ti
            SET res_id = imd.res_id
            FROM ir_model_data AS imd
            WHERE ti.res_id IS NULL
                AND ti.imd_module IS NOT NULL AND ti.imd_name IS NOT NULL
                AND imd.source = 'xml' AND imd.res_id != 0

                AND ti.imd_module = imd.module AND ti.imd_name = imd.name
                AND ti.imd_model = imd.model; """ % self._table_name,
            debug=self._debug)

        if self._debug:
            cr.execute("SELECT imd_module, imd_model, imd_name FROM %s " \
                "WHERE res_id IS NULL AND imd_module IS NOT NULL" % self._table_name)
            for row in cr.fetchall():
                logger.debug("ir.translation.cursor: missing res_id for %s. %s/%s ", *row)

        cr.execute("DELETE FROM %s WHERE res_id IS NULL AND imd_module IS NOT NULL" % \
            self._table_name, debug=self._debug)

        # Records w/o res_id must _not_ be inserted into our db, because they are
        # referencing non-existent data.

        find_expr = "irt.lang = ti.lang AND irt.type = ti.type " \
                    " AND irt.name = ti.name AND irt.src = ti.src " \
                    " AND (ti.type != 'model' OR ti.res_id = irt.res_id) "

        # Step 2: update existing (matching) translations
        if self._overwrite:
            cr.execute("""UPDATE ONLY %s AS irt
                SET value = ti.value
                FROM %s AS ti
                WHERE %s AND ti.value IS NOT NULL AND ti.value != ''
                """ % (self._parent_table, self._table_name, find_expr),
                debug = self._debug)

        # Step 3: insert new translations

        cr.execute("""INSERT INTO %s(name, lang, res_id, src, type, value)
            SELECT name, lang, res_id, src, type, value
              FROM %s AS ti
              WHERE NOT EXISTS(SELECT 1 FROM ONLY %s AS irt WHERE %s);
              """ % (self._parent_table, self._table_name, self._parent_table, find_expr),
              debug = self._debug)

        if self._debug:
            cr.execute('SELECT COUNT(*) FROM ONLY %s' % (self._parent_table))
            c1 = cr.fetchone()[0]
            cr.execute('SELECT COUNT(*) FROM ONLY %s AS irt, %s AS ti WHERE %s' % \
                (self._parent_table, self._table_name, find_expr))
            c = cr.fetchone()[0]
            logger.debug("ir.translation.cursor:  %d entries now in ir.translation, %d common entries with tmp", c1, c)

        # Step 4: cleanup
        cr.execute("DROP TABLE %s" % self._table_name)
        return True

class ir_translation(osv.osv):
    _name = "ir.translation"
    _log_access = False

    def _get_language(self, cr, uid, context):
        lang_model = self.pool.get('res.lang')
        lang_data = lang_model.search_read(cr, uid, [('translatable', '=', True)], fields=['code','name'], context=context)
        l = [(d['code'],d['name']) for d in lang_data]
        return l

    _columns = {
        'name': fields.char('Field Name', size=128, required=True),
        'res_id': fields.integer('Resource ID', select=True),
        'lang': fields.selection(_get_language, string='Language', size=16),
        'type': fields.selection(TRANSLATION_TYPE, string='Type', size=16, select=True),
        'src': fields.text('Source'),
        'value': fields.text('Translation Value'),
    }

    _sql_constraints = [ ('lang_fkey_res_lang', 'FOREIGN KEY(lang) REFERENCES res_lang(code)',
        'Language code of translation item must be among known languages' ), ]

    _indices = {
        'ir_translation_ltnr': index.plain('lang', 'type', 'name', 'res_id'),
        'ir_translation_src': index.ihash('src'),
    }

    def _check_selection_field_value(self, cr, uid, field, value, context=None):
        if field == 'lang':
            return
        return super(ir_translation, self)._check_selection_field_value(cr, uid, field, value, context=context)

    @tools.cache(skiparg=3, multi='ids')
    def _get_ids(self, cr, uid, name, tt, lang, ids):
        translations = dict.fromkeys(ids, False)
        if ids:
            cr.execute_prepared('ir_trans_get_ids',
                    'SELECT res_id,value ' \
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
    def _get_source(self, cr, uid, name, types, lang, source=None):
        """
        Returns the translation for the given combination of name, type, language
        and source. All values passed to this method should be unicode (not byte strings),
        especially ``source``.

        :param name: identification of the term to translate, such as field name (optional if source is passed)
        :param types: single string defining type of term to translate (see ``type`` field on ir.translation), or sequence of allowed types (strings)
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
        if isinstance(types, basestring):
            types = [types,]
        else:
            types = list(types)
        if source and not name:
            cr.execute_prepared('ir_trans_get_src0',
                    'SELECT value ' \
                    'FROM ir_translation ' \
                    'WHERE lang=%s ' \
                        'AND type = ANY(%s) ' \
                        'AND src=%s ' \
                        "AND value IS NOT NULL AND value <> '' ",
                    (lang, types, source), debug=self._debug)
        elif source:
            cr.execute_prepared('ir_trans_get_src1',
                    'SELECT value ' \
                    'FROM ir_translation ' \
                    'WHERE lang=%s ' \
                        'AND type = ANY(%s) ' \
                        'AND name=%s ' \
                        'AND src=%s ' \
                        "AND value IS NOT NULL AND value <> '' ",
                    (lang, types, tools.ustr(name), source), debug=self._debug)
        else:
            cr.execute_prepared('ir_trans_get_src2',
                    'SELECT value ' \
                    'FROM ir_translation ' \
                    'WHERE lang=%s ' \
                        'AND type = ANY(%s) ' \
                        'AND name=%s ' \
                        "AND value IS NOT NULL AND value <> '' ",
                    (lang, types, tools.ustr(name)), debug=self._debug)
        res = cr.fetchone()
        trad = res and res[0] or u''
        return trad

    def _get_multisource(self, cr, uid, name, tt, lang, src_list):
        """ Retrieve translations for a list of sources.
            returns a /dictionary/ of the src: val pairs
        """
        assert lang

        cr.execute_prepared('ir_trans_get_msrc',
                    'SELECT src, value ' \
                    'FROM ir_translation ' \
                    'WHERE lang=%s ' \
                        'AND type=%s ' \
                        'AND name=%s ' \
                        'AND src = ANY(%s) ' \
                        "AND value IS NOT NULL AND value <> '' ",
                    (lang, tt, tools.ustr(name), src_list), debug=self._debug)

        res = dict(map(tuple, cr.fetchall()))

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
            nlen = (len(prepend) + 1)
        else:
            fl2 = fld_list
            nlen = 1

        cr.execute('SELECT substr(name, %s) as name, type, value ' \
                    'FROM ir_translation ' \
                    'WHERE lang=%s ' \
                       'AND  (name, type) IN %s ' \
                       "AND value IS NOT NULL AND value <> '' ",
                    (nlen, lang, tuple(fl2)), debug=self._debug)

        res = map(tuple, cr.fetchall())
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
            nlen = (len(prepend) + 1)
        else:
            fl2 = name_list
            nlen = 1

        cr.execute_prepared('ir_trans_get_mids',
                    'SELECT substr(name, %s) as name, res_id, value ' \
                    'FROM ir_translation ' \
                    'WHERE lang=%s AND type = %s '\
                    ' AND name = ANY(%s) AND res_id = ANY(%s) '
                    "AND value IS NOT NULL AND value <> '' ",
                    (nlen, lang, ttype, fl2, ids), debug=self._debug)

        res = map(tuple, cr.fetchall())
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

    def _get_import_cursor(self, cr, uid, context=None):
        """ Return a cursor-like object for fast inserting translations
        """
        if context is None:
            context = {}
        return ir_translation_import_cursor(cr, uid, self, context=context)

ir_translation()

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

