#!/bin/env python
# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009, Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2008-2014, P. Christeas <xrg@hellug.gr>
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

import sys
import StringIO
# import copy
from lxml import etree
# import base64
import logging
import threading
import re

import utils

Font_size= 8.0

ws_re = re.compile(r'\s+')


class textbox(object):
    """A box containing plain text.
    It can have an offset, in chars.
    Lines can be either text strings, or textbox'es, recursively.
    """
    def __init__(self,x=0, y=0, width=False, height=False):
        self.posx = x
        self.posy = y
        self.width = width
        self.height = height
        self.lines = []
        self.curline = ''
        self.endspace = False
        # size!
        self.wordwrap = False
        if self.width:
            self.wordwrap = min(int(self.width / 10), 5)

    def __copy__(self):
        n = self.__class__(self.posx, self.posy, self.width, self.height)
        n.lines = self.lines[:]
        n.curline = self.curline
        n.endspace = self.endspace

    @property
    def full(self):
        return bool(self.height and len(self.lines) >= self.height)

    def newline(self):
        if isinstance(self.curline, textbox):
            self.lines.extend(self.curline.renderlines())
        else:
            self.lines.append(self.curline)
        self.curline = ''

    def fline(self):
        if isinstance(self.curline, textbox):
            self.lines.extend(self.curline.renderlines())
        elif len(self.curline):
            self.lines.append(self.curline)
        self.curline = ''

    def appendtxt(self,txt):
        """Append some text to the current line.
           Mimic the HTML behaviour, where all whitespace evaluates to
           a single space 

           @return remaining text, that does not fit this textbox
        """
        if not txt:
            return
        remainder = ws_re.sub(' ', txt)

        if self.endspace and remainder[0] == ' ':
            # already had space from previous op
            remainder = remainder[1:]

        while remainder:
            self.tick()
            if self.height and len(self.lines) >= self.height:
                break
            endpos = False
            if not self.width:
                self.curline += remainder
                remainder = ''
                continue
            if len(self.curline) >= self.width:
                # safeguard, should not happen
                self.newline()

            if (len(remainder) + len(self.curline)) > self.width:
                # text-wrapping algorithm:
                endpos = int(self.width - len(self.curline))

            if self.wordwrap and (endpos is not False):
                # word wrapping
                ep2 = 0
                while ep2 < self.wordwrap:
                    if remainder[endpos - ep2] == ' ':
                        endpos -= ep2
                        break
                    else:
                        ep2 += 1
            if endpos is False or endpos > len(remainder):
                self.curline += remainder
                remainder = ''
            else:
                self.curline += remainder[:endpos]
                self.newline()
                if remainder[endpos] == ' ':
                    endpos += 1
                remainder = remainder[endpos:]

        if remainder.isspace():
            return False
        else:
            return remainder

    def rendertxt(self,xoffset=0):
        result = ''
        lineoff = ""
        if self.posy:
            result += "\n"  * int(self.posy)
        if (self.posx+xoffset):
            lineoff += " " * int(self.posx+xoffset)
        for l in self.lines:
            result += lineoff+ l +"\n"
        return result

    def renderlines(self,pad=0):
        """Returns a list of lines, from the current object
        pad: all lines must be at least pad characters.
        """
        result = []
        lineoff = ""
        if (self.posx):
            lineoff += " " * int(self.posx)
        for l in self.lines:
            lpad = ""
            if pad and len(l) < pad :
                lpad += " " * int(pad - len(l))
            #elif pad and len(l) > pad ?
            result.append(lineoff+ l+lpad)
        return result

    def haplines(self,arr,offset,cc= ''):
        """ Horizontaly append lines
        """
        while (len(self.lines) < len(arr)):
            self.lines.append("")

        for i in range(len(self.lines)):
            if (len(self.lines[i]) < offset):
                self.lines[i] += " " * int(offset - len(self.lines[i]))
        for i in range(len(arr)):
            self.lines[i] += cc +arr[i]

    def tick(self):
        """Inform upstream that the process is running, cancel it if needed
        """
        ct = threading.currentThread()
        if ct and getattr(ct, 'must_stop', False):
            raise KeyboardInterrupt


class _flowable(object):
    """ A (main?) flowable, where free text is rendered into
    """
    _log = logging.getLogger('render.rml2txt')

    def __init__(self, parent_doc):
        self._tags = {
            'title': self._tag_para,
            'spacer': self._tag_spacer,
            'para': self._tag_para,
            'font': self._tag_text,
            'section': self._tag_para,
            'nextFrame': self._tag_next_frame,
            'blockTable': self._tag_table,
            'pageBreak': self._tag_page_break,
            'setNextTemplate': self._tag_next_template,
        }
        self.parent_doc = parent_doc
        self.localcontext = parent_doc.localcontext
        self.template = parent_doc.templates[0]
        self.nitags = []

    def warn_nitag(self,tag):
        if tag not in self.nitags:
            self._log.warning("Unknown tag \"%s\", please implement it.", tag)
            self.nitags.append(tag)

    def _tag_page_break(self, node=False):
        self.template.page_stop()
        self.tb = self.template.frame_start()
        assert self.tb, "No textbox for template!"

    def _tag_next_template(self, node=False):
        self.template.set_next_template()
        self.tb = self.template.frame_start()
        assert self.tb, "No textbox for template!"

    def _tag_next_frame(self, node=False):
        self.template.frame_stop()
        self.tb = self.template.frame_start()
        assert self.tb, "No textbox for template!"

    def _tag_spacer(self, node):
        length = 1+int(utils.unit_get(node.get('length')))/35
        for n in range(length):
            self.tb.newline()

    def _tag_table(self, node):
        self.tb.fline()
        saved_tb = self.tb
        self.tb = None
        sizes = None
        if node.get('colWidths'):
            sizes = map(lambda x: utils.unit_get(x), node.get('colWidths').split(','))
        trs = []
        for n in utils._child_get(node,self):
            if n.tag == 'tr':
                tds = []
                for m in utils._child_get(n,self):
                    if m.tag == 'td':
                        self.tb = textbox()
                        self.rec_render_cnodes(m)
                        tds.append(self.tb)
                        self.tb = None
                if len(tds):
                    trs.append(tds)

        if not sizes:
            self._log.debug("computing table sizes..")
        for tds in trs:
            trt = textbox()
            off=0
            for i in range(len(tds)):
                p = int(sizes[i]/Font_size)
                trl = tds[i].renderlines(pad=p)
                trt.haplines(trl,off)
                off += sizes[i]/Font_size + 1
            saved_tb.curline = trt
            saved_tb.fline()

        self.tb = saved_tb
        return

    def _tag_para(self, node):
        #TODO: styles
        self.rec_render_cnodes(node)
        self.tb.newline()

    def _tag_text(self, node):
        """We do ignore fonts.."""
        self.rec_render_cnodes(node)

    def render_text(self, text):
        while text:
            text = self.tb.appendtxt(text)
            if self.tb.full:
                self._tag_next_frame()

    def rec_render_cnodes(self, node):
        self.render_text(utils._process_text(self, node.text or ''))
        for n in utils._child_get(node,self):
            self.rec_render(n)
        self.render_text(utils._process_text(self, node.tail or ''))

    def rec_render(self,node):
        """ Recursive render: fill outarr with text of current node
        """
        if node.tag != None:
            if node.tag == etree.Comment:
                pass
            elif node.tag in self._tags:
                self._tags[node.tag](node)
            else:
                self.warn_nitag(node.tag)

    def render(self, node):
        self._tag_next_frame(None)
        self.rec_render_cnodes(node)
        self.template.page_stop()

class _rml_tmpl_tag(object):
    _log = logging.getLogger('render.rml2txt')
    def __init__(self, parent, node):
        self.posx = False
        self.posy = False

    def get_tb(self, parent):
        """ Returns a textbox for this tag, that is either full or can be
            fed with more story text

            Shall only be called once per page!
        """
        raise NotImplementedError(self.__class__.__name__)

class _rml_tmpl_frame(_rml_tmpl_tag):
    def __init__(self, parent, node):
        """ sizes are in points
        """
        self.posx, self.posy = parent._conv_unit_pos(node.get('x1'), node.get('y1'))
        self.width, self.height = parent._conv_unit_size(node.get('width'), node.get('height'))

    def get_tb(self, parent):
        return textbox(self.posx, self.posy, self.width, self.height)
    
    def __repr__(self):
        return "Frame <%f, %f, %f, %f>" % (self.posx, self.posy, self.width, self.height)

class _rml_tmpl_draw_string(_rml_tmpl_tag):

    def __init__(self, parent, node):
        posx, posy = parent._conv_unit_pos(node.get('x'), node.get('y'))
        text = utils.text_get(node).strip()
        if not text:
            self.tb = False
            return
        if node.tag == 'drawString':
            # left-aligned
            pass
        elif node.tag == 'drawRightString':
            # right-aligned
            posx -= len(text)
        elif node.tag == 'drawCentredString':
            # centered
            posx -= len(text) / 2.0
        else:
            raise ValueError("Invelid draw string tag: %s" % node.tag)
        
        self.tb = textbox(posx, posy, width=len(text), height=1)
        self.tb.appendtxt(text)
        self.tb.newline()

    def get_tb(self, parent):
        return self.tb

    def __repr__(self):
        if self.tb:
            return "DrawString < %d,%d, \"%s\">" % (self.tb.posx, self.tb.posy, self.tb.lines)
        else:
            return "DrawString <empty>"

class _rml_no_op(_rml_tmpl_tag):
    def get_tb(self, parent):
        return False

    def __repr__(self):
        return "No-Op"

class _rml_template(object):
    _tags = {
            'drawString': _rml_tmpl_draw_string,
            'drawRightString': _rml_tmpl_draw_string,
            'drawCentredString': _rml_tmpl_draw_string,
            'lines': _rml_no_op,
            'fill': _rml_no_op,
            'stroke': _rml_no_op,
            'setFont': _rml_no_op, # TODO check size
        }
    _log = logging.getLogger('render.rml2txt')
    _page_size_re = re.compile(r'\(([^,]+),([^,]+)\)')

    def __init__(self, localcontext, node, out_fp, images=None, path='.', title=None):
        self.localcontext = localcontext
        self.out_fp = out_fp
        self.frame_pos = -1
        self.template_order = []
        self.page_template = {}
        self.loop = 0
        self.page_size = (595.0,842.0)
        self._font_aspect = 0.53
        self._font_size = None
        self._set_font_size(10.0)

        if node.get('pageSize'):
            m = self._page_size_re.match(node.get('pageSize'))
            if m:
                self.page_size = (utils.unit_get(m.group(1)), utils.unit_get(m.group(2)))
            else:
                self._log.warning("Page size \"%s\" cannot be parsed", node.get('pageSize'))

        for pt in node.findall('pageTemplate'):
            frames = {}
            tid = pt.get('id') or True
            self.template_order.append(tid)
            frames = self.page_template[tid] = []
            for n in pt.findall('frame'):
                frames.append(_rml_tmpl_frame(self, n))
            for tmpl in pt.findall('pageGraphics'):
                for n in tmpl.getchildren():
                    if n.tag == etree.Comment:
                        continue
                    elif n.tag in self._tags:
                        frames.append(self._tags[n.tag](self, n))
                    else:
                        self._log.debug("Not handled in pageTemplate: %s", node.tag)
        self.template = self.template_order[0]
        self.page_no = 0
        self.cur_page = None

    def _set_font_size(self, points):
        self._font_size = (points * self._font_aspect, points)

    def _conv_point_pos(self, x, y):
        return x / self._font_size[0], (self.page_size[1] - y) / self._font_size[1]

    def _conv_point_size(self, x, y):
        return x / self._font_size[0], y / self._font_size[1]

    def _conv_unit_pos(self, x, y):
        return utils.unit_get(x) / self._font_size[0], \
                    (self.page_size[1] - utils.unit_get(y)) / self._font_size[1]

    def _conv_unit_size(self, x, y):
        return utils.unit_get(x) / self._font_size[0], utils.unit_get(y) / self._font_size[1]

    def set_next_template(self):
        self.template = self.template_order[(self.template_order.index(self.template)+1) % self.template_order]
        self.frame_pos = -1
        if self.cur_page:
            self.page_stop()
        self.frame_start()

    def set_template(self, name):
        self.template = name
        self.frame_pos = -1
        if self.cur_page:
            self.page_stop()
        self.frame_start()

    def frame_start(self):
        if not self.cur_page:
            self.cur_page = []
            self.frame_pos = -1
            self.page_no += 1
            for frame in self.page_template[self.template]:
                new_tb = frame.get_tb(self)
                if not new_tb:
                    continue
                if (self.frame_pos < 0 ) and not new_tb.full:
                    self.frame_pos = len(self.cur_page)
                self.cur_page.append(new_tb)

        if self.frame_pos < 0:
            # There must be at least one frame to write story into
            raise ValueError("No writable frame found in page template!")

        return self.cur_page[self.frame_pos]

    def frame_stop(self):
        if not self.cur_page:
            return
            
        self.frame_pos += 1
        
        while self.frame_pos < len(self.cur_page) \
                    and self.cur_page[self.frame_pos].full:
            self.frame_pos += 1
        
        if self.frame_pos >= len(self.cur_page):
            self.page_stop()

    def page_stop(self):
        self.cur_page.sort(key=lambda t: (t.posy, t.posx))
        for tb in self.cur_page:
            for line in tb.renderlines():
                self.out_fp.write(line+'\n')
        self.cur_page = None
        self.frame_pos = -1

class _rml_doc(object):
    _log = logging.getLogger('render.rml2txt')
    def __init__(self, node, localcontext, images=None, path='.', title=None):
        self.localcontext = localcontext
        self.etree = node
        self.filename = self.etree.get('filename')
        self.templates = []

    def render(self, out_fp):
        for tmpl in self.etree.findall('template'):
            self.templates.append( _rml_template(self.localcontext, tmpl, out_fp))

        for story in utils._child_get(self.etree, self, 'story'):
            self.tick()
            fable = _flowable(self)
            fable.render(story)

    def tick(self):
        """Inform upstream that the process is running, cancel it if needed
        """
        ct = threading.currentThread()
        if ct and getattr(ct, 'must_stop', False):
            raise KeyboardInterrupt

def parseNode(rml, localcontext = {},fout=None, images=None, path='.',title=None):
    if images is None:
        images = {}
    node = etree.XML(rml)
    r = _rml_doc(node, localcontext, images, path, title=title)
    fp = StringIO.StringIO()
    r.render(fp)
    return fp.getvalue()

def parseString(rml, localcontext = {},fout=None, images=None, path='.',title=None):
    if images is None:
        images = {}
    node = etree.XML(rml)
    r = _rml_doc(node, localcontext, images, path, title=title)
    if fout:
        fp = file(fout,'wb')
        r.render(fp)
        fp.close()
        return fout
    else:
        fp = StringIO.StringIO()
        r.render(fp)
        return fp.getvalue()

def trml2pdf_help():
    print 'Usage: rml2txt input.rml > output.txt'
    print 'Render the standard input (RML) and output an TXT file'
    sys.exit(0)

if __name__=="__main__":
    if len(sys.argv)>1:
        if sys.argv[1]=='--help':
            trml2pdf_help()
        logging.basicConfig(level=logging.DEBUG)
        print parseString(file(sys.argv[1], 'r').read()).encode('iso8859-7')
    else:
        print 'Usage: trml2txt input.rml >output.txt'
        print 'Try \'trml2txt --help\' for more information.'

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
