# -*- coding: utf8 -*-
# Copyright P. Christeas, 2011

__name__ = "ir.model.data: Add source column and update it"

from tools.sql import column_exists

def migrate(cr, version):
    if not column_exists(cr, 'ir_model_data', 'source'):
        cr.execute('ALTER TABLE ir_model_data ADD "source" VARCHAR(16) ')
        cr.execute('UPDATE ir_model_data SET "source" = \'orm\' '
                'WHERE "source" IS NULL AND noupdate IS NULL')
        cr.execute('UPDATE ir_model_data SET "source" = \'xml\' '
                'WHERE "source" IS NULL AND noupdate IS NOT NULL')
        cr.execute('ALTER TABLE ir_model_data ALTER "source" SET NOT NULL, '
                'ALTER "source" SET DEFAULT \'xml\' ;')
    cr.execute('UPDATE ir_model_data SET noupdate = false WHERE noupdate IS NULL;')

#eof
