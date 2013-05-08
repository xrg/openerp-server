# -*- encoding: utf-8 -*-

import warnings
from config import config
import pooler
from data_loaders import DataLoader

def yaml_import(cr, module, yamlfile, idref=None, mode='init', noupdate=False, report=None, filename=None, fatal=False, context=None):
    if idref is None:
        idref = {}
    warnings.warn("You should no longer call tools.yaml_import(), but DataLoader instead",
                      DeprecationWarning, stacklevel=2)
    pool = pooler.get_pool(cr.dbname)
    dl = DataLoader['xml'](pool, 1, module, idref, mode, noupdate, context, report=report, fatal=fatal)
    dl.parse(cr, filename or yamlfile.name, yamlfile)
    if config.get('import_partial', False):
        cr.commit()


# keeps convention of convert.py
convert_yaml_import = yaml_import

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
