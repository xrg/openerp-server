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

import codecs
import csv
import inspect
import locale
import os
import pooler
import re
import logging
import shutil
import tarfile
import tempfile
import sys
from os.path import join

from datetime import datetime

import tools

_LOCALE2WIN32 = {
    'af_ZA': 'Afrikaans_South Africa',
    'sq_AL': 'Albanian_Albania',
    'ar_SA': 'Arabic_Saudi Arabia',
    'eu_ES': 'Basque_Spain',
    'be_BY': 'Belarusian_Belarus',
    'bs_BA': 'Serbian (Latin)',
    'bg_BG': 'Bulgarian_Bulgaria',
    'ca_ES': 'Catalan_Spain',
    'hr_HR': 'Croatian_Croatia',
    'zh_CN': 'Chinese_China',
    'zh_TW': 'Chinese_Taiwan',
    'cs_CZ': 'Czech_Czech Republic',
    'da_DK': 'Danish_Denmark',
    'nl_NL': 'Dutch_Netherlands',
    'et_EE': 'Estonian_Estonia',
    'fa_IR': 'Farsi_Iran',
    'ph_PH': 'Filipino_Philippines',
    'fi_FI': 'Finnish_Finland',
    'fr_FR': 'French_France',
    'fr_BE': 'French_France',
    'fr_CH': 'French_France',
    'fr_CA': 'French_France',
    'ga': 'Scottish Gaelic',
    'gl_ES': 'Galician_Spain',
    'ka_GE': 'Georgian_Georgia',
    'de_DE': 'German_Germany',
    'el_GR': 'Greek_Greece',
    'gu': 'Gujarati_India',
    'he_IL': 'Hebrew_Israel',
    'hi_IN': 'Hindi',
    'hu': 'Hungarian_Hungary',
    'is_IS': 'Icelandic_Iceland',
    'id_ID': 'Indonesian_indonesia',
    'it_IT': 'Italian_Italy',
    'ja_JP': 'Japanese_Japan',
    'kn_IN': 'Kannada',
    'km_KH': 'Khmer',
    'ko_KR': 'Korean_Korea',
    'lo_LA': 'Lao_Laos',
    'lt_LT': 'Lithuanian_Lithuania',
    'lat': 'Latvian_Latvia',
    'ml_IN': 'Malayalam_India',
    'id_ID': 'Indonesian_indonesia',
    'mi_NZ': 'Maori',
    'mn': 'Cyrillic_Mongolian',
    'no_NO': 'Norwegian_Norway',
    'nn_NO': 'Norwegian-Nynorsk_Norway',
    'pl': 'Polish_Poland',
    'pt_PT': 'Portuguese_Portugal',
    'pt_BR': 'Portuguese_Brazil',
    'ro_RO': 'Romanian_Romania',
    'ru_RU': 'Russian_Russia',
    'mi_NZ': 'Maori',
    'sr_CS': 'Serbian (Cyrillic)_Serbia and Montenegro',
    'sk_SK': 'Slovak_Slovakia',
    'sl_SI': 'Slovenian_Slovenia',
    #should find more specific locales for spanish countries,
    #but better than nothing
    'es_AR': 'Spanish_Spain',
    'es_BO': 'Spanish_Spain',
    'es_CL': 'Spanish_Spain',
    'es_CO': 'Spanish_Spain',
    'es_CR': 'Spanish_Spain',
    'es_DO': 'Spanish_Spain',
    'es_EC': 'Spanish_Spain',
    'es_ES': 'Spanish_Spain',
    'es_GT': 'Spanish_Spain',
    'es_HN': 'Spanish_Spain',
    'es_MX': 'Spanish_Spain',
    'es_NI': 'Spanish_Spain',
    'es_PA': 'Spanish_Spain',
    'es_PE': 'Spanish_Spain',
    'es_PR': 'Spanish_Spain',
    'es_PY': 'Spanish_Spain',
    'es_SV': 'Spanish_Spain',
    'es_UY': 'Spanish_Spain',
    'es_VE': 'Spanish_Spain',
    'sv_SE': 'Swedish_Sweden',
    'ta_IN': 'English_Australia',
    'th_TH': 'Thai_Thailand',
    'mi_NZ': 'Maori',
    'tr_TR': 'Turkish_Turkey',
    'uk_UA': 'Ukrainian_Ukraine',
    'vi_VN': 'Vietnamese_Viet Nam',
    'tlh_TLH': 'Klingon',

}


class UNIX_LINE_TERMINATOR(csv.excel):
    lineterminator = '\n'

csv.register_dialect("UNIX", UNIX_LINE_TERMINATOR)

#
# Warning: better use self.pool.get('ir.translation')._get_source if you can
#
def translate(cr, name, source_type, lang, source=None):
    if source and name:
        cr.execute('select value from ir_translation where lang=%s and type=%s and name=%s and src=%s', (lang, source_type, str(name), source))
    elif name:
        cr.execute('select value from ir_translation where lang=%s and type=%s and name=%s', (lang, source_type, str(name)))
    elif source:
        cr.execute('select value from ir_translation where lang=%s and type=%s and src=%s', (lang, source_type, source))
    res_trans = cr.fetchone()
    res = res_trans and res_trans[0] or False
    return res

class GettextAlias(object):
    def __call__(self, source):
        try:
            # we only need 2 frames, not all stack()
            frame = inspect.currentframe()
            if frame is None:
                return source
            frame = frame.f_back # need to find one frame back..
            if not frame:
                return source
        except Exception:
            return source

        own_cr = False
        cr = frame.f_locals.get('cr')
        try:
            ctx = frame.f_locals.get('context', False)
            if ctx is False:
                kwargs = frame.f_locals.get('kwargs', False)
                if kwargs is False:
                    args = frame.f_locals.get('args',False)
                    if args and isinstance(args, (list, tuple)) \
                            and isinstance(args[-1], dict):
                        ctx = args[-1]
                    else:
                        ctx = {}
                elif isinstance(kwargs, dict):
                    ctx = kwargs.get('context', {})
                else:
                    ctx = {}

            lang = ctx and ctx.get('lang', False)
            if not lang:
                return source
            if (not cr) and frame.f_globals.get('pooler',False):
                db = frame.f_locals.get('dbname') or frame.f_locals.get('db')
                if db and isinstance(db, basestring):
                    cr = pooler.get_db(db).cursor()
                    own_cr = True
            if not cr:
                return source
        except Exception:
            return source

        if hasattr(cr, 'execute'):
            try:
                # TODO: try to match the frame's filename, line_no,
                # but in a "least distance" sense
                # if so, double-check the root/base translations filenames
                
                cr.execute("SELECT value FROM ir_translation " \
                            "WHERE lang=%s and type IN (%s,%s) AND src=%s "
                            "AND value IS NOT NULL AND value != '' ",
                            (lang, 'code','sql_constraint', source))
                res_trans = cr.fetchone()
                return res_trans and res_trans[0] or source
            finally:
                try:
                    if own_cr:
                        cr.close()
                except Exception: pass
        else:
            return source
_ = GettextAlias()


# class to handle po files
class TinyPoFile(object):
    def __init__(self, buffer):
        self.logger = logging.getLogger('i18n')
        self.buffer = buffer
        self.first = True
        self.last = False
        self.re_unquote = re.compile(r"(\\.)")
        self.re_unquote_repls = {'n': '\n', } # 't': '\t', 'r': '\r'
        self.line_num = 0    # same as csv.reader's

    def warn(self, msg, *args):
        self.logger.warning(msg, *args)

    def __iter__(self):
        self.buffer.seek(0)
        self.line_num = 0

        self.first = True
        self.tnrs= []
        return self

    def _get_line(self):
        try:
            line = self.buffer.next()
            # remove the BOM (Byte Order Mark):
            if line and self.line_num == 0:
                line = unicode(line, 'utf8').lstrip(unicode( codecs.BOM_UTF8, "utf8"))

            self.line_num += 1
            return line.strip()
        except StopIteration:
            # ensure that the file ends with at least one empty line
            if not self.last:
                self.last = True
                return ''
            else:
                raise

    def next(self):
        def unquote(str):
            def d_repl(mo):
                return self.re_unquote_repls.get(mo.group(1)[1], mo.group(1)[1])
            return self.re_unquote.sub(d_repl, str[1:-1])

        type = name = res_id = source = trad = None

        if self.tnrs:
            type, name, res_id, source, trad = self.tnrs.pop(0)
            if not res_id:
                res_id = '0'
        else:
            tmp_tnrs = []
            line = None
            fuzzy = False
            while (not line):
                line = self._get_line()
            while line.startswith('#'):
                if line.startswith('#~ '):
                    break
                if line.startswith('#:'):
                    # We expect lines like
                    #: type1:place1:id1 type2:place2:id2 ...
                    # but in a rare Spanish case, id contains spaces
                    # so we have to merge the parts. Also tolerate empty
                    # lines like '#:'
                    lparts = line[2:].strip().split(' ')
                    while lparts:
                        lpart = lparts.pop(0)
                        if not lpart:
                            continue
                        if lpart.count(':') < 2:
                            self.warn("Malformed #: identifier '%s' at line %d", 
                                lpart, self.line_num)
                            break # skip the whole line
                        while lparts and lparts[0].count(':') == 0:
                            # We consider 'type1:place1:id ab1' to be one, but
                            # don't allow 'type1:place1:id ab:1'
                            lpart += ' ' + lparts.pop(0)
                        tmp_tnrs.append(lpart.strip().split(':',2))

                elif line.startswith('#,') and ('fuzzy' in line[2:]):
                    fuzzy = True
                line = self._get_line()
            while not line:
                # allow empty lines between comments and msgid
                line = self._get_line()
            if line.startswith('#~ '):
                while line.startswith('#~ ') or not line:
                    line = self._get_line()
                # This has been a deprecated entry, don't return anything
                return self.next()

            if not line.startswith('msgid'):
                raise Exception("malformed file: bad line: %s" % line)
            source = unquote(line[6:])
            line = self._get_line()
            if not source and self.first:
                # if the source is "" and it's the first msgid, it's the special
                # msgstr with the informations about the traduction and the
                # traductor; we skip it
                self.tnrs = []
                while line:
                    line = self._get_line()
                return self.next()

            while not line.startswith('msgstr'):
                if not line:
                    raise Exception('malformed file at %d'% self.line_num)
                source += unquote(line)
                line = self._get_line()

            trad = unquote(line[7:])
            line = self._get_line()
            while line:
                trad += unquote(line)
                line = self._get_line()

            if tmp_tnrs and not fuzzy:
                type, name, res_id = tmp_tnrs.pop(0)
                for t, n, r in tmp_tnrs:
                    self.tnrs.append((t, n, r, source, trad))

        self.first = False

        if name is None:
            if not fuzzy:
                self.warn('Missing "#:" formated comment at line %d for the following source:\n\t%s',
                        self.line_num, source[:30])
            return self.next()
        return type, name, res_id, source, trad

    def write_infos(self, modules):
        import release
        self.buffer.write("# Translation of %(project)s.\n" \
                          "# This file contains the translation of the following modules:\n" \
                          "%(modules)s" \
                          "#\n" \
                          "msgid \"\"\n" \
                          "msgstr \"\"\n" \
                          '''"Project-Id-Version: %(project)s %(version)s\\n"\n''' \
                          '''"Report-Msgid-Bugs-To: %(bugmail)s\\n"\n''' \
                          '''"POT-Creation-Date: %(now)s\\n"\n'''        \
                          '''"PO-Revision-Date: %(now)s\\n"\n'''         \
                          '''"Last-Translator: <>\\n"\n''' \
                          '''"Language-Team: \\n"\n'''   \
                          '''"MIME-Version: 1.0\\n"\n''' \
                          '''"Content-Type: text/plain; charset=UTF-8\\n"\n'''   \
                          '''"Content-Transfer-Encoding: \\n"\n'''       \
                          '''"Plural-Forms: \\n"\n'''    \
                          "\n"

                          % { 'project': release.description,
                              'version': release.version,
                              'modules': reduce(lambda s, m: s + "#\t* %s\n" % m, modules, ""),
                              'bugmail': release.support_email,
                              'now': datetime.utcnow().strftime('%Y-%m-%d %H:%M')+"+0000",
                            }
                          )

    def write(self, modules, tnrs, source, trad):
        def quote(s):
            ret = ''
            rl = 0
            for c in s:
                if rl == 0:
                    ret += '"'
                    rl += 1
                if c == '"':
                    ret += '\\"'
                    rl += 2
                elif c == '\\':
                    ret += '\\\\'
                    rl += 2
                elif c == '\n':
                    ret += '\\n'
                    if rl > 32:
                        ret += '"\n'
                        rl = 0
                    continue
                else:
                    ret += c
                    rl += 1
                    # Wrap long lines at space or punctuation boundaries
                    if (rl > 71 and c in (' ', '\t')) or \
                            (rl > 80 and c in ('.', ',', ':','?',')')):
                        ret += '"\n'
                        rl = 0
            if rl != 0:
                ret += '"'
            elif ret and ret[-1] == '\n':
                ret = ret[:-1]
            elif not ret:
                ret = '""'
            return ret

        plurial = len(modules) > 1 and 's' or ''
        self.buffer.write("#. module%s: %s\n" % (plurial, ', '.join(modules)))


        code = False
        for typy, name, res_id in tnrs:
            self.buffer.write("#: %s:%s:%s\n" % (typy, name, res_id))
            if typy == 'code':
                code = True

        if code:
            # only strings in python code are python formated
            self.buffer.write("#, python-format\n")

        if not isinstance(trad, unicode):
            trad = unicode(trad, 'utf8')
        if not isinstance(source, unicode):
            source = unicode(source, 'utf8')

        msg = "msgid %s\n"      \
              "msgstr %s\n\n"   \
                  % (quote(source), quote(trad))
        self.buffer.write(msg.encode('utf8'))


# Methods to export the translation file

def trans_export(lang, modules, buffer, format, cr):

    def _process(format, modules, rows, buffer, lang, newlang):
        if format == 'csv':
            writer=csv.writer(buffer, 'UNIX')
            for row in rows:
                writer.writerow(row)
        elif format == 'po':
            rows.pop(0)
            writer = tools.TinyPoFile(buffer)
            writer.write_infos(modules)

            # we now group the translations by source. That means one translation per source.
            grouped_rows = {}
            for module, type, name, res_id, src, trad in rows:
                row = grouped_rows.setdefault(src, {})
                row.setdefault('modules', set()).add(module)
                if ('translation' not in row) or (not row['translation']):
                    row['translation'] = trad
                row.setdefault('tnrs', []).append((type, name, res_id))

            for src, row in grouped_rows.items():
                writer.write(row['modules'], row['tnrs'], src, row['translation'])

        elif format == 'tgz':
            rows.pop(0)
            rows_by_module = {}
            for row in rows:
                module = row[0]
                # first row is the "header", as in csv, it will be popped
                rows_by_module.setdefault(module, [['module', 'type', 'name', 'res_id', 'src', ''],])
                rows_by_module[module].append(row)

            tmpdir = tempfile.mkdtemp()
            for mod, modrows in rows_by_module.items():
                tmpmoddir = join(tmpdir, mod, 'i18n')
                os.makedirs(tmpmoddir)
                pofilename = (newlang and mod or lang) + ".po" + (newlang and 't' or '')
                buf = file(join(tmpmoddir, pofilename), 'w')
                _process('po', [mod], modrows, buf, lang, newlang)
                buf.close()

            tar = tarfile.open(fileobj=buffer, mode='w|gz')
            tar.add(tmpdir, '')
            tar.close()
            shutil.rmtree(tmpdir)

        else:
            raise Exception(_('Bad file format'))

    newlang = not bool(lang)
    #if newlang:
    #    lang = 'en_US'
    trans = trans_generate(lang, modules, cr)
    if newlang and format!='csv':
        for trx in trans:
            trx[-1] = ''
    modules = set([t[0] for t in trans[1:]])
    _process(format, modules, trans, buffer, lang, newlang)
    del trans

def trans_generate(lang, modules, cr):
    from trans_scanner import TranslationScanner
    pool = pooler.get_pool(cr.dbname)
    ts = TranslationScanner(pool, cr, 1, modules)
    return ts.scan(lang=lang)

def trans_load(cr, filename, lang, verbose=True, context=None):
    logger = logging.getLogger('i18n')
    try:
        fileobj = open(filename,'r')
        logger.info("loading %s", filename)
        fileformat = os.path.splitext(filename)[-1][1:].lower()
        r = trans_load_data(cr, fileobj, fileformat, lang, verbose=verbose, context=context)
        fileobj.close()
        return r
    except IOError:
        if verbose:
            logger.error("couldn't read translation file %s", filename)
        return None

def trans_load_data(cr, fileobj, fileformat, lang, lang_name=None, verbose=True, context=None):
    """Populates the ir_translation table. 
    """
    logger = logging.getLogger('i18n')
    if verbose:
        logger.info('loading translation file for language %s', lang)
    if context is None:
        context = {}
    db_name = cr.dbname
    pool = pooler.get_pool(db_name)
    lang_obj = pool.get('res.lang')
    trans_obj = pool.get('ir.translation')
    iso_lang = tools.get_iso_codes(lang)
    try:
        uid = 1
        (lc, encoding) = locale.getdefaultlocale()
        ids = lang_obj.search(cr, uid, [('code','=', lang)])

        if not ids:
            # lets create the language with locale information
            lang_obj.load_lang(cr, 1, lang=lang, lang_name=lang_name)
            try:
                locale.setlocale(locale.LC_ALL, str(lc + '.' + encoding))
            except locale.Error:
                pass
        # Here we try to reset the locale regardless.
        if lc:
            locale.setlocale(locale.LC_ALL, str(lc + '.' + encoding))


        # now, the serious things: we read the language file
        fileobj.seek(0)
        if fileformat == 'csv':
            #Setting the limit of data while loading a CSV
            csv.field_size_limit(sys.maxint)
            reader = csv.reader(fileobj, quotechar='"', delimiter=',')
            # read the first line of the file (it contains columns titles)
            f = reader.next()
        elif fileformat == 'po':
            reader = TinyPoFile(fileobj)
            f = ['type', 'name', 'res_id', 'src', 'value']
        else:
            logger.error('Bad file format: %s', fileformat)
            raise Exception(_('Bad file format'))

        # read the rest of the file
        line = 1
        irt_cursor = trans_obj._get_import_cursor(cr, uid, context=context)

        for row in reader:
            line += 1
            # skip empty rows and rows where the translation field (=last fiefd) is empty
            #if (not row) or (not row[-1]):
            #    continue

            # dictionary which holds values for this line of the csv file
            # {'lang': ..., 'type': ..., 'name': ..., 'res_id': ...,
            #  'src': ..., 'value': ...}
            dic = {'lang': lang, 'imd_model': None, 'imd_module': None, 'imd_name': None}
            dic_module = False
            for i, fld in enumerate(f):
                if fld in ('module',):
                    continue
                dic[fld] = row[i]

            # This would skip terms that fail to specify a res_id
            if not dic.get('res_id', False):
                continue

            res_id = dic.pop('res_id')
            if res_id and isinstance(res_id, (int, long)) \
                or (isinstance(res_id, basestring) and res_id.isdigit()):
                    dic['res_id'] = int(res_id)
            else:
                try:
                    tmodel = dic['name'].split(',')[0]
                    if '.' in res_id:
                        tmodule, tname = res_id.split('.', 1)
                    else:
                        tmodule = dic_module
                        tname = res_id
                    dic['imd_model'] = tmodel
                    dic['imd_module'] = tmodule
                    dic['imd_name'] =  tname

                    dic['res_id'] = None
                except Exception:
                    logger.warning("Could not decode resource for %s, please fix the po file.",
                                    dic['res_id'], exc_info=True)
                    dic['res_id'] = None

            irt_cursor.push(dic)

        irt_cursor.finish()
        if verbose:
            logger.info("translation file loaded succesfully")
    except IOError:
        filename = '[lang: %s][format: %s]' % (iso_lang or 'new', fileformat)
        logger.exception("couldn't read translation file %s", filename)
    except Exception:
        raise

def get_locales(lang=None):
    if lang is None:
        lang = locale.getdefaultlocale()[0]

    if os.name == 'nt':
        lang = _LOCALE2WIN32.get(lang, lang)

    def process(enc):
        ln = locale._build_localename((lang, enc))
        yield ln
        nln = locale.normalize(ln)
        if nln != ln:
            yield nln

    for x in process('utf8'): yield x

    prefenc = locale.getpreferredencoding()
    if prefenc:
        for x in process(prefenc): yield x

        prefenc = {
            'latin1': 'latin9',
            'iso-8859-1': 'iso8859-15',
            'cp1252': '1252',
        }.get(prefenc.lower())
        if prefenc:
            for x in process(prefenc): yield x

    yield lang



def resetlocale():
    # locale.resetlocale is bugged with some locales.
    for ln in get_locales():
        try:
            return locale.setlocale(locale.LC_ALL, ln)
        except locale.Error:
            continue

def load_language(cr, lang):
    """Loads a translation terms for a language.
    Used mainly to automate language loading at db initialization.

    :param lang: language ISO code with optional _underscore_ and l10n flavor (ex: 'fr', 'fr_BE', but not 'fr-BE')
    :type lang: str
    """
    pool = pooler.get_pool(cr.dbname)
    language_installer = pool.get('base.language.install')
    uid = 1
    oid = language_installer.create(cr, uid, {'lang': lang})
    language_installer.lang_install(cr, uid, [oid], context=None)

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

