# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2010 OpenERP S.A. (<http://www.openerp.com>).
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
"""
Module to handle publisher warranty contracts as well as notifications from
OpenERP.
"""

import datetime
import logging
import sys
import urllib
import urllib2

import pooler
import release
from osv import osv, fields
from tools.translate import _
from tools.safe_eval import safe_eval
from tools.config import config
from tools import misc

_logger = logging.getLogger(__name__)

"""
Time interval that will be used to determine up to which date we will
check the logs to see if a message we just received was already logged.
@type: datetime.timedelta
"""
_PREVIOUS_LOG_CHECK = datetime.timedelta(days=365)

class publisher_warranty_contract(osv.osv):
    """
    Osv representing a publisher warranty contract.
    """
    _name = "publisher_warranty.contract"

    def _get_valid_contracts(self, cr, uid):
        """
        Return the list of the valid contracts encoded in the system.

        @return: A list of contracts
        @rtype: list of publisher_warranty.contract browse records
        """
        return [contract for contract in self.browse(cr, uid, self.search(cr, uid, []))
                if contract.state == 'valid']

    def status(self, cr, uid):
        """ Method called by the client to check availability of publisher warranty contract. """

        contracts = self._get_valid_contracts(cr, uid)
        return {
            'status': "full" if contracts else "none" ,
            'uncovered_modules': list(),
        }

    def send(self, cr, uid, tb, explanations, remarks=None, issue_name=None):
        """ Method called by the client to send a problem to the publisher warranty server. """

        raise osv.except_osv(_("Not Available"),
                                 _("Publisher warranty functionality has been removed from this version"))

    def check_validity(self, cr, uid, ids, context=None):
        """
        Check the validity of a publisher warranty contract. This method just call get_logs() but checks
        some more things, so it can be called from a user interface.
        """
        contract_id = ids[0]
        contract = self.browse(cr, uid, contract_id)
        state = contract.state
        validated = state != "unvalidated"

        if not validated:
            raise osv.except_osv(_("Contract validation error"),
                                 _("Please verify your publisher warranty serial number and validity."))
        return True

    def get_logs(self, cr, uid, ids, cron_mode=True, context=None):
        """
        Send a message to OpenERP's publisher warranty server to check the validity of
        the contracts, get notifications, etc...

        @param cron_mode: If true, catch all exceptions (appropriate for usage in a cron).
        @type cron_mode: boolean
        """
        raise osv.except_osv(_("Not Available"),
                                 _("Publisher warranty functionality has been removed from this version"))

    def get_last_user_messages(self, cr, uid, limit, context=None):
        """
        Get the messages to be written in the web client.
        @return: A list of html messages with ids, can be False or empty.
        @rtype: list of tuples(int,string)
        """
        ids = self.pool.get('res.log').search(cr, uid, [("res_model", "=", "publisher_warranty.contract")]
                                        , order="create_date desc", limit=limit)
        if not ids:
            return []
        messages = [(x.id, x.name) for x in self.pool.get('res.log').browse(cr, uid, ids)]

        return messages

    def del_user_message(self, cr, uid, id, context=None):
        """
        Delete a message.
        """
        self.pool.get('res.log').unlink(cr, uid, [id])

        return True

    _columns = {
        'name' : fields.char('Serial Key', size=384, required=True, help="Your OpenERP Publisher's Warranty Contract unique key, also called serial number."),
        'date_start' : fields.date('Starting Date', readonly=True),
        'date_stop' : fields.date('Ending Date', readonly=True),
        'state' : fields.selection([('unvalidated', 'Unvalidated'), ('valid', 'Valid')
                            , ('terminated', 'Terminated'), ('canceled', 'Canceled')], string="State", readonly=True),
        'kind' : fields.char('Kind', size=64, readonly=True),
        "check_support": fields.boolean("Support Level 1", readonly=True),
        "check_opw": fields.boolean("OPW", readonly=True, help="Checked if this is an OpenERP Publisher's Warranty contract (versus older contract types"),
    }

    _defaults = {
        'state': 'unvalidated',
    }

    _sql_constraints = [
        ('uniq_name', 'unique(name)', "That contract is already registered in the system.")
    ]

publisher_warranty_contract()

class maintenance_contract(osv.osv_memory):
    """ Old osv we only keep for compatibility with the clients. """

    _name = "maintenance.contract"

    def status(self, cr, uid):
        return self.pool.get("publisher_warranty.contract").status(cr, uid)

    def send(self, cr, uid, tb, explanations, remarks=None, issue_name=None):
        return self.pool.get("publisher_warranty.contract").send(cr, uid, tb,
                        explanations, remarks, issue_name)

maintenance_contract()

class publisher_warranty_contract_wizard(osv.osv_memory):
    """
    A wizard osv to help people entering a publisher warranty contract.
    """
    _name = 'publisher_warranty.contract.wizard'
    _inherit = "ir.wizard.screen"

    _columns = {
        'name' : fields.char('Serial Key', size=256, required=True, help="Your OpenERP Publisher's Warranty Contract unique key, also called serial number."),
        'state' : fields.selection([("draft", "Draft"), ("finished", "Finished")], 'State')
    }

    _defaults = {
        "state": "draft",
    }

    def action_validate(self, cr, uid, ids, context=None):
        raise osv.except_osv(_("Not Available"),
                                 _("Publisher warranty functionality has been removed from this version"))

publisher_warranty_contract_wizard()

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

