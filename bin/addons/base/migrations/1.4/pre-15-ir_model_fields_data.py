# -*- coding: utf8 -*-
# Copyright P. Christeas, 2011

__name__ = "ir.model.fields: add field_data column"

def column_exists(cr, table, column):
    cr.execute("SELECT count(1)"
               "  FROM pg_class c, pg_attribute a"
               " WHERE c.relname=%s"
               "   AND c.oid=a.attrelid"
               "   AND a.attname=%s",
               (table, column))
    return cr.fetchone()[0] != 0

def migrate(cr, version):
    if not column_exists(cr, 'ir_model_fields', 'field_data'):
        cr.execute('ALTER TABLE ir_model_fields ADD field_data TEXT;')

#eof
