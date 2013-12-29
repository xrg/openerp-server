# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP/F3, Open Source Management Solution
#    Copyright (C) 2013 P. Christeas <xrg@hellug.gr>
#    Parts Copyright (C) 2004-2011 OpenERP SA (www.openerp.com).
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

from tools.service_meta import _ServiceMeta, abstractmethod
import logging
import itertools


class TranslationScanner(object):
    """Global translation scanner object

        You only need one of this. Feed it with the list of modules, and it
        will call all the available methods to get translations by.
    """
    def __init__(self, pool, cr, uid, modules):
        self.pool = pool
        self.cr = cr
        self.uid = uid
        self.modules = modules

    def scan(self, encode=None, lang=False):
        logger = logging.getLogger('i18n.scanner')
        logger.debug("Scanning translations at %s for %s", self.cr.dbname,
                        ', '.join(self.modules[:10]))
        _to_translate = []
        for kname in _TScanWorker.list_classes(prefix='method.'):
            logger.debug("Using %s ...", kname)
            scanner = _TScanWorker[kname](self)
            _short_array = [] # avoid re-allocation of the big array
            for line in scanner.scan(self.modules):
                assert len(line) == 5, "Invalid line: %r" % line
                if not line[1]: # source
                    continue
                _short_array.append(line)

            logger.debug("Got %d translations from %s", len(_short_array), kname)
            _to_translate.extend(_short_array)

        out = [["module","type","name","res_id","src","value"]] # header

        if lang:
            trans_obj = self.pool.get('ir.translation')
            logger.debug("Filling the translated strings (%d) from DB...", len(_to_translate))
            # translate strings marked as to be translated
            key_fn = lambda l: (l[0], l[4], l[2])

            # First, sort:
            _to_translate.sort(key=key_fn)
            for key, g_iter in itertools.groupby(_to_translate, key=key_fn):
                module, tt, name = key
                res_src = set([(l[3], l[1]) for l in g_iter ])
                src_val = trans_obj._get_multisource(self.cr, self.uid, name, tt, lang, [r[1] for r in res_src])
                # logger.debug("Got %d translations for %d sources for %s %s %s ",
                #                len(src_val), len(res_src), module, tt, name)
                for res_id, src in res_src:
                    out.append([module, tt, name, res_id, src, src_val.get(src,'')])
        else:
            logger.debug("Sorting the full list, %d entries", len(_to_translate))
            _to_translate.sort()
            # simply append an empty translation
            last_line = None

            for module, source, name, id, type in _to_translate:
                # We want to avoid any other method of finding duplicates,
                # since it would sort again or copy the array (expensive)
                if (module, source, name, id, type) == last_line:
                    continue
                out.append([module, type, name, id, source, ''])
                last_line = (module, source, name, id, type)
        
        return out

class _TScanWorker(object):
    """Translation scanner worker

        Each worker, in its way, will scan parts of the database, source files
        or modules of the ERP server and yield translatable strings.

        Workers should be constructed in a hierarchy, each capable of calling
        sub-workers for a specific method. The order workers are called should
        not matter, strings will be sorted at the end, anyway.

        The API of the worker is not defined here. Each subclass of workers,
        like the ".method" one, will have its own definition of a `scan(...)`
        method, specifying any parameters it needs.
    """
    __metaclass__ = _ServiceMeta
    _logger = logging.getLogger('i18n')

    def __init__(self, scanner):
        self.parent = scanner
        self._logger = logging.getLogger('i18n.'+self._name)


class _TScanMethod(_TScanWorker):
    _name = '.method'

    @abstractmethod
    def scan(self, modules):
        """ Go through the modules in parent scanner and yield translations

            out format:
                - module
                - source (string)
                - name
                - res_id
                - type
        """
        while False:
            yield None

    def _get_where_calc(self, modules, col='module'):
        """ Return where clause and params for module selection
        """
        query_param = ()
        if 'all_installed' in modules:
            query_patch = ' %s IN ( SELECT name FROM ir_module_module WHERE state = \'installed\') ' % col
        elif 'all' not in modules:
            query_patch = ' %s = ANY(%%s) ' % col
            query_param = (list(modules),)
        else:
            query_patch = ' true '
        return query_patch, query_param

#eof
