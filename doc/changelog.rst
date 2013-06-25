.. _changelog:

Changelog
=========

`trunk`
-------

- Almost removed ``LocalService()``. For reports,
  ``openerp.osv.orm.Model.print_report()`` can be used. For workflows, see
  :ref:`orm-workflows`.
- Removed support for the ``NET-RPC`` protocol.
- Added the :ref:`Long polling <longpolling-worker>` worker type.
- Added :ref:`orm-workflows` to the ORM.
- Added :ref:`routing-decorators` to the RPC and WSGI stack.
- Removed support for ``__terp__.py`` descriptor files.
- Removed support for ``<terp>`` root element in XML files.
- Removed support for the non-openerp namespace (e.g. importing ``tools``
  instead of ``openerp.tools`` in an addons).
- Add a new type of exception that allows redirections:
  ``openerp.exceptions.RedirectWarning``.
- Give a pair of new methods to ``res.config.settings`` and a helper to make
  them easier to use: ``get_config_warning()``.
- Path to webkit report files (field ``report_file``) must be writen with the
  Unix way (with ``/`` and not ``\``)