# -*- coding: utf-8 -*-
##############################################################################
#    
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2012 P. Christeas <xrg@hellug.gr>
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

def drop_view_if_exists(cr, viewname):
    cr.execute("select count(1) from pg_class where relkind=%s and relname=%s", ('v', viewname,))
    if cr.fetchone()[0]:
        cr.execute("DROP view %s" % (viewname,))
        cr.commit()


def column_exists(cr, table, column):
    cr.execute("SELECT count(1)"
               "  FROM pg_class c, pg_attribute a"
               " WHERE c.relname=%s"
               "   AND c.oid=a.attrelid"
               "   AND a.attname=%s",
               (table, column))
    return cr.fetchone()[0] != 0

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
