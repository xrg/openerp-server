# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP-F3, Open Source Management Solution
#    Copyright (C) 2013 P. Christeas <xrg@hellug.gr>
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
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

import pooler
import tools

#.apidoc title: Authentication helpers

client_pit = None

class ExceptionNoTb(Exception):
    """ When rejecting a password, hide the traceback
    """
    def __init__(self, msg):
        super(ExceptionNoTb, self).__init__(msg)
        self.traceback = ('','','')

def login(db, login, password, client_address=None):
    wkr = client_pit.get(client_address)
    try:
        if db is False:
            raise Exception("Cannot authenticate against False db!")
        pool = pooler.get_pool(db)
        user_obj = pool.get('res.users')
        ret = user_obj.login(db, login, password)
        if ret:
            wkr.good()
        else:
            wkr.bad()
        return ret
    except Exception:
        wkr.bad()
        raise

def check_super(passwd, client_address=None):
    wkr = client_pit.get(client_address)
    try:
        if passwd == tools.config['admin_passwd']:
            wkr.good()
            return True
        else:
            wkr.bad()
            raise ExceptionNoTb('AccessDenied: Invalid super administrator password.')
    except Exception:
        wkr.bad()
        raise

def check(db, uid, passwd, client_address=None):
    pool = pooler.get_pool(db)
    user_obj = pool.get('res.users')
    return user_obj.check(db, uid, passwd)

class _dummy_Client_wkr(object):
    """ Minimal object than Client_pit.get() can return

        API spec is that it must support `.good()` and `.bad()`
    """
    def good(self):
        pass

    def bad(self):
        pass

class _dummy_Client_pit(object):
    """API, dummy version of "Client pit" service.

        Purpose of a client pit is to throttle calls to login() or any
        similar "gateway" function of authentication, in order to foil
        dictionary or brute-force attacks. Calls shall be groupped per
        client address.

        This implementation does nothing but simply expose the API for
        other parts of the code

        Usage must be trivial:
        *before* calling login(), the protocol handler shall `get()` a
        worker for the address. After the result of `login()` protocol
        shall feed back with `good()` or `bad()` ::

            def login_handler(params, client_addr):
                wkr = security.client_pit.get(client_addr) # new code

                if security.login(params...):
                    wkr.good() # new
                    return True
                else:
                    wkr.bad() # new
                    return False

    """
    def __init__(self):
        pass

    def get(self, client_addr):
        return _dummy_Client_wkr()

client_pit = _dummy_Client_pit()

__all__ = [ ExceptionNoTb, login, check_super, client_pit ]

#eof