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

from reportlab.pdfbase import ttfonts
from openerp.osv import fields, osv
from openerp.report.render.rml2pdf import customfonts

import logging

"""This module allows the mapping of some system-available TTF fonts to
the reportlab engine.

This file could be customized per distro (although most Linux/Unix ones)
should have the same filenames, only need the code below).

Due to an awful configuration that ships with reportlab at many Linux
and Ubuntu distros, we have to override the search path, too.
"""
_logger = logging.getLogger(__name__)


class res_font(osv.Model):
    _name = "res.font"
    _description = 'Fonts available'
    _order = 'name'

    _columns = {
        'name': fields.char("Name", required=True),
    }

    _sql_constraints = [
        ('name_font_uniq', 'unique(name)', 'You can not register two fonts with the same name'),
    ]

    def discover_fonts(self, cr, uid, ids, context=None):
        """Scan fonts on the file system, add them to the list of known fonts
        and create font object for the new ones"""
        customfonts.CustomTTFonts = customfonts.BaseCustomTTFonts

        found_fonts = {}
        for font_path in customfonts.list_all_sysfonts():
            try:
                font = ttfonts.TTFontFile(font_path)
                _logger.debug("Found font %s at %s", font.name, font_path)
                if not found_fonts.get(font.familyName):
                    found_fonts[font.familyName] = {'name': font.familyName}

                mode = font.styleName.lower().replace(" ", "")

                customfonts.CustomTTFonts.append((font.familyName, font.name, font_path, mode))
            except ttfonts.TTFError:
                _logger.warning("Could not register Font %s", font_path)

        # add default PDF fonts
        for family in customfonts.BasePDFFonts:
            if not found_fonts.get(family):
                found_fonts[family] = {'name': family}

        # remove deleted fonts
        existing_font_ids = self.search(cr, uid, [], context=context)
        existing_font_names = []
        for font in self.browse(cr, uid, existing_font_ids):
            existing_font_names.append(font.name)
            if font.name not in found_fonts.keys():
                self.unlink(cr, uid, font.id, context=context)

        # add unknown fonts
        for family, vals in found_fonts.items():
            if family not in existing_font_names:
                self.create(cr, uid, vals, context=context)
        return True

    def init_no_scan(self, cr, uid, context=None):
        """Add demo data for PDF fonts without scan (faster for db creation)"""
        for font in customfonts.BasePDFFonts:
            if not self.search(cr, uid, [('name', '=', font)], context=context):
                self.create(cr, uid, {'name':font}, context=context)
        return True