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

import cStringIO
# import sys

# import ir
import pooler
from config import config
from yaml_import import convert_yaml_import

# Import of XML records requires the unsafe eval as well,
# almost everywhere, which is ok because it supposedly comes
# from trusted data, but at least we make it obvious now.

class ConvertError(Exception):
    def __init__(self, doc, orig_excpt):
        self.d = doc
        self.orig = orig_excpt

    def __str__(self):
        return 'Exception:\n\t%s\nUsing file:\n%s' % (self.orig, self.d)

import warnings
from data_loaders import DataLoader

class assertion_report(object):
    def __init__(self):
        self._report = {}

    def record_assertion(self, success, severity):
        """
            Records the result of an assertion for the failed/success count
            returns success
        """
        if severity in self._report:
            self._report[severity][success] += 1
        else:
            self._report[severity] = {success:1, not success: 0}
        return success

    def get_report(self):
        return self._report

    def __str__(self):
        res = '\nAssertions report:\nLevel\tsuccess\tfailed\n'
        success = failed = 0
        for sev in self._report:
            res += sev + '\t' + str(self._report[sev][True]) + '\t' + str(self._report[sev][False]) + '\n'
            success += self._report[sev][True]
            failed += self._report[sev][False]
        res += 'total\t' + str(success) + '\t' + str(failed) + '\n'
        res += 'end of report (' + str(success + failed) + ' assertion(s) checked)'
        return res

def convert_csv_import(cr, module, fname, csvcontent, idref=None, mode='init',
        noupdate=False, context=None):
    '''Import csv file :
        quote: "
        delimiter: ,
        encoding: utf-8'''
    if not idref:
        idref={}
    warnings.warn("You should no longer call tools.convert_csv_import(), but DataLoader instead",
                      DeprecationWarning, stacklevel=2)

    pool = pooler.get_pool(cr.dbname)
    dl = DataLoader['csv'](pool, 1, module, idref, mode, noupdate, context)
    dl.parse(cr, fname, cStringIO(csvcontent))

#
# xml import/export
#
def convert_xml_import(cr, module, xmlfile, idref=None, mode='init', noupdate=False, report=None, context=None):
    if not idref:
        idref={}
    warnings.warn("You should no longer call tools.convert_xml_import(), but DataLoader instead",
                      DeprecationWarning, stacklevel=2)

    pool = pooler.get_pool(cr.dbname)
    dl = DataLoader['xml'](pool, 1, module, idref, mode, noupdate, context, report=report)
    dl.parse(cr, xmlfile.name, xmlfile)
    if config.get('import_partial', False):
        cr.commit()

#eof

