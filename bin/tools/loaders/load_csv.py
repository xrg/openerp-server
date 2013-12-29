# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2011 OpenERP s.a. (<http://openerp.com>).
#    Copyright (C) 2009,2011-2013 P.Christeas <xrg@hellug.gr>
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

import csv
import pickle
import os.path

from tools.data_loaders import DataLoader
from tools import config
from tools.misc import ustr
from tools.translate import _

#.apidoc title: CSV data loader

class _CSVloader(DataLoader):
    """ This loader can import your CSV data files in the ERP DB
    """
    _name = 'csv'

    def __init__(self, *args, **kwargs):
        super(_CSVloader,self).__init__(*args, **kwargs)
        self._import_partial = config.get('import_partial')
        self.noupdate = (self.mode == 'init')

    def _load_import_partial(self, fname, reader):
        """See if part of the CSV is already imported, shall be skipped
        """
        if not self._import_partial:
            return
        fname_partial = self.module_name + '/'+ fname
        if not os.path.isfile(self._import_partial):
            return

        data = pickle.load(file(self._import_partial, 'rb'))
        if data.get(fname_partial, False):
            skip = int(data[fname_partial])
            i = 0
            while i < skip:
                reader.next()
                i += 1

        return

    def _save_import_partial(self, cr, fname):
        if not self._import_partial:
            return
        fname_partial = self.module_name + '/'+ fname
        data = pickle.load(file(self._import_partial, 'rb'))
        data[fname_partial] = 0
        pickle.dump(data, file(self._import_partial,'wb'))
        cr.commit()

    def parse(self, cr, fname, fp):
        """ Import CSV contents into the database, using orm.import_data()
        """

        # Note: this fn() needs improvement, once orm.import_data() is refactored

        #remove folder path from model
        pdir, fm = os.path.split(fname)
        model = fm.rsplit('.', 1)[0].replace('-', '.')


        reader = csv.reader(fp, quotechar='"', delimiter=',')
        fields = reader.next()
        self._load_import_partial(fname, reader)

        if not (self.mode == 'init' or 'id' in fields):
            self.logger.error("Import specification does not contain 'id' and we are in init mode, Cannot continue.")
            return

        datas = []
        for line in reader:
            if (not line) or not reduce(lambda x,y: x or y, line):
                continue
            try:
                datas.append(map(lambda x: ustr(x), line))
            except Exception:
                self.logger.error("Cannot import the line: %s", line)

        result, rows, warning_msg, dummy = self.pool.get(model). \
                import_data(cr, self.uid, fields, datas, self.mode,
                        self.module_name, self.noupdate, filename='',
                        context=self.context)
        if result < 0:
            # Report failed import and abort module install
            uid, context = self.uid, self.context
            raise Exception(_('Module loading failed: file %s/%s could not be processed:\n %s') % \
                    (self.module_name, fname, warning_msg))

        self._save_import_partial(cr, fname)

# eof