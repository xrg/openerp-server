# -*- coding: utf-8 -*-
##############################################################################
#    
#    OpenERP, Open Source Management Solution
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

from amount_to_text import AmountToText
from decimal import Decimal

#.apidoc title: Amount to Text (English)

class AmountToText_en(AmountToText):
    """ English Amount-to-text engine
    """
    _name = 'en'

    to_19 = ( 'zero',  'one',   'two',  'three', 'four',   'five',   'six',
            'seven', 'eight', 'nine', 'ten',   'eleven', 'twelve', 'thirteen',
            'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen' )
    tens  = ( None, None, 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety')
    denom = ( '',
            'thousand',     'million',         'billion',       'trillion',       'quadrillion',
            'quintillion',  'sextillion',      'septillion',    'octillion',      'nonillion',
            'decillion',    'undecillion',     'duodecillion',  'tredecillion',   'quattuordecillion',
            'sexdecillion', 'septendecillion', 'octodecillion', 'novemdecillion', 'vigintillion' )

    @classmethod
    def _convert_nn(cls, val):
        """convert a value < 100 to English.
        """
        if val < 20:
            return cls.to_19[val]
        
        dec = int(val // 10)
        ret = cls.tens[dec]
        if val % 10:
            ret = ret + '-' + cls.to_19[val % 10]
        return ret

    @classmethod
    def _convert_nnn(cls, val):
        """
            convert a value < 1000 to english, special cased because it is the level that kicks 
            off the < 100 special case.  The rest are more general.  This also allows you to
            get strings in the form of 'forty-five hundred' if called directly.
        """
        word = ''
        (mod, rem) = (val % 100, val // 100)
        if rem > 0:
            word = cls.to_19[rem] + ' hundred'
            if mod > 0:
                word = word + ' '
        if mod > 0:
            word = word + cls._convert_nn(mod)
        return word

    def amount_to_text(self, number):
        """ Convert a standalone number to text
        """
        val = int(number) # FIXME!
        if val < 100:
            return self._convert_nn(val)
        if val < 1000:
            return self._convert_nnn(val)
        for (didx, dval) in ((v - 1, 1000 ** v) for v in range(len(self.denom))):
            if dval > val:
                mod = 1000 ** didx
                l = val // mod
                r = val - (l * mod)
                ret = self._convert_nnn(l) + ' ' + self.denom[didx]
                if r > 0:
                    ret = ret + ', ' + self.amount_to_text(r)
                return ret

    def monetary_to_text(self, number, currency):
        """ Convert money at `currency_id` to text

            @param currency_id Id or browse() object for res.currency

            Note: currency may be/may not be the same as local language
        """
        if isinstance(number, (float, Decimal)):
            # raise NotImplementedError # FIXME!
            number = int(number)
        ret = self.amount_to_text(number)
        if currency:
            ret += ' ' + currency.name
        return 

    def unit_to_text(number, uom_id):
        """ Convert measurement at uom_id (units) to text

            @param uom_id Id or browse() object for `product.uom` unit-of-measure
        """
        
        raise NotImplementedError

class AmountToText_enUS(AmountToText):
    """ US American English Amount-to-text engine
    """
    _name = 'en_US'
    _inherit = 'en'

class AmountToText_enGB(AmountToText):
    """ English (British) Amount-to-text engine
    """
    _name = 'en_GB'
    _inherit = 'en'

"""
def _amount_to_text_en(number, currency):
    number = '%.2f' % number
    units_name = currency
    list = str(number).split('.')
    start_word = english_number(int(list[0]))
    end_word = english_number(int(list[1]))
    cents_number = int(list[1])
    cents_name = (cents_number > 1) and 'Cents' or 'Cent'
    final_result = start_word +' '+units_name+' and ' + end_word +' '+cents_name
    return final_result
"""
# eof
