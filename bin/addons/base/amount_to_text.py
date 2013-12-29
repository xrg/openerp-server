# -*- coding: utf-8 -*-
##############################################################################
#
#    F3 - Open source ERP
#    Copyright (C) 2013 P. Christeas <xrg@hellug.gr>
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

from tools.service_meta import _ServiceMeta, abstractmethod

#.apidoc title: API for amount-to-text subsystem

class AmountToText(object):
    """ Convert a numeric amount to a text representation, multi-lingual

        This is an abstract class. A real instance will have a _name set
        to the desired language (ISO codes like 'en_US').

        Usage instructions:

        Minimal API::

            a2t = AmountToText['en_FR']()
            result = a2t.amount_to_text(123.45)

            # or (for currency)
            curr = pool.get('res.currency').browse(cr, uid, [('name', '=', 'EUR'), ('company_id', '=', 1)] )
            result2 = a2t.monetary_to_text(-345.67, curr[0])
    """
    __metaclass__ = _ServiceMeta

    @abstractmethod
    def amount_to_text(self, number):
        """ Convert a standalone number to text
        """
        pass

    @abstractmethod
    def monetary_to_text(self, number, currency):
        """ Convert money at `currency_id` to text

            @param currency browse() object for res.currency

            Note: currency may be/may not be the same as local language
        """

    @abstractmethod
    def unit_to_text(self, number, uom):
        """ Convert measurement at uom_id (units) to text

            @param uom browse() object for `product.uom` unit-of-measure
        """

#eof