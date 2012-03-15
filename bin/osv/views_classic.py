# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2012 P. Christeas <xrg@hellug.gr>
#    Parts Copyright (C) 2004-2011 OpenERP SA. (www.openerp.com)
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


#.apidoc title: Classic View declarations

from views import oo_view
from tools.translate import _
from lxml import etree
from orm import except_orm # which prohibits this module to load inside orm

class tree_view(oo_view):
    _view_type = 'tree'
    _view_name = _('Tree')

    def _default_view(self, cr, uid, obj, context=None):
        """ Generates a single-field tree view, using _rec_name if
        it's one of the columns or the first column it finds otherwise
        
        :returns: a tree view as an lxml document
        :rtype: etree._Element
        """
        _rec_name = obj._rec_name
        if _rec_name not in obj._columns:
            _rec_name = obj._columns.keys()[0]

        view = etree.Element('tree', string=obj._description)
        etree.SubElement(view, 'field', name=_rec_name)
        return view

tree_view()

class form_view(oo_view):
    _view_type = 'form'
    _view_name = _('Form')

    def _default_view(self, cr, uid, obj, context=None):
        """ Generates a default single-line form view using all fields
        of the current model except the m2m and o2m ones.

        :returns: a form view as an lxml document
        :rtype: etree._Element
        """
        view = etree.Element('form', string=obj._description)
        # TODO it seems fields_get can be replaced by _all_columns (no need for translation)
        for field, descriptor in obj.fields_get(cr, uid, context=context).iteritems():
            if descriptor['type'] in ('one2many', 'many2many'):
                continue
            etree.SubElement(view, 'field', name=field)
            if descriptor['type'] == 'text':
                etree.SubElement(view, 'newline')
        return view

form_view()

class search_view(oo_view):
    _view_type = 'search'
    _view_name = _('Search')

    def _default_view(self, cr, uid, obj, context=None):
        """
        :returns: an lxml document of the view
        :rtype: etree._Element
        """
        form_view = obj.fields_view_get(cr, uid, False, 'form', context=context)
        tree_view = obj.fields_view_get(cr, uid, False, 'tree', context=context)

        fields_to_search = set()
        fields = obj.fields_get(cr, uid, context=context)
        for field in fields:
            if fields[field].get('select'):
                fields_to_search.add(field)

        for view in (form_view, tree_view):
            view_root = etree.fromstring(view['arch'])
            # Only care about select=1 in xpath below, because select=2 is covered
            # by the custom advanced search in clients
            fields_to_search.update(view_root.xpath("//field[@select=1]/@name"))

        tree_view_root = view_root # as provided by loop above
        search_view = etree.Element("search", string=tree_view_root.get("string", ""))

        field_group = etree.SubElement(search_view, "group")
        for field_name in fields_to_search:
            etree.SubElement(field_group, "field", name=field_name)

        return search_view

search_view()

class graph_view(oo_view):
    _view_type = 'graph'
    _view_name = _('Graph')

graph_view()

class calendar_view(oo_view):
    _view_type = 'calendar'
    _view_name = _('Calendar')

    def _default_view(self, cr, uid, obj, context=None):
        """ Generates a default calendar view by trying to infer
        calendar fields from a number of pre-set attribute names
        
        :returns: a calendar view
        :rtype: etree._Element
        """
        def set_first_of(seq, in_, to):
            """Sets the first value of ``set`` also found in ``in`` to
            the ``to`` attribute of the view being closed over.

            Returns whether it's found a suitable value (and set it on
            the attribute) or not
            """
            for item in seq:
                if item in in_:
                    view.set(to, item)
                    return True
            return False

        view = etree.Element('calendar', string=obj._description)
        etree.SubElement(view, 'field', name=obj._rec_name)

        if (obj._date_name not in obj._columns):
            date_found = False
            for dt in ['date', 'date_start', 'x_date', 'x_date_start']:
                if dt in obj._columns:
                    obj._date_name = dt
                    date_found = True
                    break

            if not date_found:
                raise except_orm(_('Invalid Object Architecture!'),_("Insufficient fields for Calendar View!"))
        view.set('date_start', obj._date_name)

        set_first_of(["user_id", "partner_id", "x_user_id", "x_partner_id"],
                     obj._columns, 'color')

        if not set_first_of(["date_stop", "date_end", "x_date_stop", "x_date_end"],
                            obj._columns, 'date_stop'):
            if not set_first_of(["date_delay", "planned_hours", "x_date_delay", "x_planned_hours"],
                                obj._columns, 'date_delay'):
                raise except_orm(
                    _('Invalid Object Architecture!'),
                    _("Insufficient fields to generate a Calendar View for %s, missing a date_stop or a date_delay" % (obj._name)))

        return view

calendar_view()

class diagram_view(oo_view):
    _view_type = 'diagram'
    _view_name = _('Diagram')

diagram_view()

class gantt_view(oo_view):
    _view_type = 'gantt'
    _view_name = _('Gantt')

gantt_view()

# WARNING: the 'mdx' view type is obsoleted!

#eof
