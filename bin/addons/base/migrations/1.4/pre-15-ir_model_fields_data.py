# -*- coding: utf8 -*-
# Copyright P. Christeas, 2011

__name__ = "ir.model.fields: add field_data column"

from tools.sql import column_exists

def migrate(cr, version):
    if not column_exists(cr, 'ir_model_fields', 'field_data'):
        cr.execute('ALTER TABLE ir_model_fields ADD field_data TEXT;')

#eof
