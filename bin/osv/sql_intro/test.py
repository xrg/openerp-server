#!/usr/bin/python

import psycopg2
import psycopg2.extras

conn = psycopg2.connect("dbname=test_bqi")
#conn.set_client_encoding("xxx")
cr = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

import model

ret = model.load_from_db(cr, ['res_users', 'ir_model_data'])

print "ret:", type(ret)

print model.pretty_print(ret)

#eof

