#!/usr/bin/python

import psycopg2

from psycopg2.psycopg1 import cursor as psycopg1cursor

class myCursor(object):
    def __init__(self, conn):
        self.cr = conn.cursor(cursor_factory=psycopg1cursor)
    
    def execute(self, query, params=None, debug=False, log_exceptions=True, _fast=False):
        return self.cr.execute(query, params)

    def __getattr__(self, name):
        return getattr(self.cr, name)
    
    def __iter__(self):
        return iter(self.cr)

conn = psycopg2.connect("dbname=test_bqi")
#conn.set_client_encoding("xxx")
cr = myCursor(conn)

import model

ret = model.load_from_db(cr, ['res_users', 'ir_model_data'])

print "ret:", type(ret)

print model.pretty_print(ret)

#eof

