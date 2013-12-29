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

from tools.data_loaders import DataLoader

#.apidoc title: SQL data loader

class _SQLLoader(DataLoader):
    _name = 'sql'

    def parse(self, cr, fname, fp):
        """ Load a pure SQL file onto the database

            This function uploads the *full* contents of the SQL file,
            unmodified, in one cr.execute() call. That's because we cannot
            safely split at ';' boundaries (may appear within a quoted view
            segment) or parse the '--' comments.
        """
        if self.uid != 1:
            raise RuntimeError("You must be admin to run arbitrary SQL!")
        query = fp.read()
        cr.execute(query)

#eof
