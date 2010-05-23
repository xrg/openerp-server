# -*- coding: utf8 -*-

__name__ = "res.partner.address: change type of 'function' field many2one to char"

def migrate(cr, version):
    change_column_type(cr,'res_partner_address')

def get_column_type(cr, table, col):
    cr.execute('SELECT pg_type.typname FROM pg_class, pg_attribute, pg_type '
        'WHERE pg_class.relname = %s AND pg_class.oid = pg_attribute.attrelid '
        ' AND pg_class.relkind = \'r\' AND pg_type.oid = pg_attribute.atttypid '
        ' AND pg_attribute.attname = %s ' ,
        (table, col), debug=False)
    return cr.fetchone()[0]

def change_column_type(cr,table):
    if get_column_type(cr, table, 'function') == 'varchar':
        return
    cr.execute('ALTER TABLE %s ADD COLUMN temp_function VARCHAR(64)' % table)
    cr.execute("UPDATE %s SET temp_function = rf.name "
            "FROM res_partner_function AS rf "
            "WHERE function = rf.id" % (table,))
    cr.execute("ALTER TABLE %s DROP COLUMN function CASCADE" % table)
    cr.execute("ALTER TABLE %s RENAME COLUMN temp_function TO function" % table)

