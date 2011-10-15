#!/usr/bin/python

import psycopg2
import logging

from psycopg2.psycopg1 import cursor as psycopg1cursor

import sys, os

sys.path.append(os.path.join(os.path.abspath(os.path.dirname(__file__)),"..", "bin","tools"))

class myCursor(object):
    def __init__(self, conn):
        self.cr = conn.cursor(cursor_factory=psycopg1cursor)
    
    def execute(self, query, params=None, debug=False, log_exceptions=True, _fast=False):
        return self.cr.execute(query, params)

    def __getattr__(self, name):
        return getattr(self.cr, name)
    
    def __iter__(self):
        return iter(self.cr)

logging.basicConfig(level=logging.DEBUG)

conn = psycopg2.connect("dbname=test_bqi")
#conn.set_client_encoding("xxx")
cr = myCursor(conn)

import sql_model

sch = sql_model.Schema()
sch.hints['tables'] = ['res_users', 'ir_model_data']
sch.load_from_db(cr)

sch.tables.append(sql_model.Table('foo'))
sch.tables['foo'].columns.append(sql_model.Column('bar', 'INTEGER', not_null=True))
sch.tables['foo'].check_column('frol', 'TIMESTAMP', select=True, default=sql_model.Column.now())
sch.tables['foo'].check_column('r_id', 'INTEGER', not_null=True, 
        references=dict(table='brob'))
sch.tables['foo'].check_column('user_id', 'INTEGER', not_null=True, 
        references=dict(table='res_users'))
idx = sch.tables['foo'].indices.append(sql_model.Index('foo_frol_r_id_idx',colnames=('frol','r_id'),state='create'))
idx.set_depends(sch.tables['foo'], on_alter=True)
print sch.pretty_print()

print
print "TODO:"
print sch._dump_todo()
print
print "Dry run commit:"
sch.commit_to_db(cr, dry_run=True)

#eof

