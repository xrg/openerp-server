# -*- encoding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-TODAY OpenERP S.A. <http://www.openerp.com>
#    Copyright (C) 2008-2009, 2011-2013 P. Christeas <xrg@hellug.gr>
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

name = 'openerp-server'
version = '6.9.2'
major_version = '6.9'
description = 'OpenERP-F3 Server'
long_desc = '''OpenERP is a complete ERP and CRM. The main features are accounting (analytic
and financial), stock management, sales and purchases management, tasks
automation, marketing campaigns, help desk, POS, etc. Technical features include
a distributed server, flexible workflows, an object database, a dynamic GUI,
customizable reports, and XML-RPC interfaces.
'''
classifiers = """Development Status :: 5 - Production/Stable
License :: OSI Approved :: GNU Affero General Public License v3
Programming Language :: Python
"""
url = 'http://www.openerp.com'
author = 'OpenERP S.A.'
author_email = 'info@openerp.com'
support_email = 'xrg@hellug.gr' # please bug me for your bugs
license = 'AGPL-3'

# Keywords of features that this server supports
# Please, respect line breaks, so that VCS can merge lines with
# different options, from branches.
server_options = [ 'base6.0',
        'static-http', 'http-options', 'multi-http',
        'xmlrpc-gzip',
        'engine-pg84', 'engine-f3', # imply several features
        'exec_dict',
        'search-browse', 'virtual-fns',
        'search-read',
        'fallback-search',
        'root-obj_list',
        'report-job',
        'relational-words',
        'create_or_update',
        ]

# In addition to server_options, some details about the API used
# internally (by the addons). This list is not useful to the RPC
# clients, but may define which addons are compatible with this
# server.
api_options = [
        'fields_only',
        'browse-browse', 'function_field_browse',
        'fields-inherit',
        'many2many-auto',
        'date_eval',
        'cdatetime', 'ndatetime',
        'struct-field',
        'auth-in-cr',
        'loaders-service',
        'db-post-commit',
        ]

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

