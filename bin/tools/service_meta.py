# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP-f3, Open Source Management Solution
#    Copyright (C) 2012 P. Christeas <xrg@hellug.gr>
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

from abc import ABCMeta, abstractmethod

#.apidoc title: Service Meta-class

""" This module defines a "forward-inheriting" kind of OO classes for python.

    These classes need only be declared in order to _override_ the functionality
    of their parent classes. A convention inverse from the classical OO inheritance.

    Example. Suppose we have these classes::

        class Base(object):
            # __metaclass__ = _ServiceMeta
            def ham(self): return "ham"

        class Derived(Base):
            def ham(self): return "spam"

        b = Base()
        assert b.ham() == "ham"

        d = Derived()
        assert d.ham() == "spam"

    Now, forward-inheritance suggests the opposite: as soon as we define `Base` to
    be a `_ServiceMeta` kind of class, `Derived` will override and mask the behavior
    of the baseclass::

        < ... same, with metaclass line uncommented ... >

        c = Base.new()
        assert c.ham() == "spam"
        assert isinstance(c, Derived)

    But, why?
    -----------

    The OpenERP ORM has been doing this ever since. Forward-inheritance is valuable for
    plugins, where a late-loaded module should modify/complement the functionality of
    base classes. Existing code will not know the existence of "Derived" classes, but
    only their "Bases".

    Before that, we used to do "register('some-feature', ImplementingClass)" to
    dynamically override the earlier implementation. _ServiceMeta will now do that
    transparently, automatically.

    Abstract methods
    =================

    A _ServiceMeta kind of class will implicitly support `abstract` methods. That is,
    some methods may use the `@abstractmethod` decorator to mandate that these methods
    need to be defined in the derived classes.

    Named services
    ===============

    Named services extends this idea to rich hierarchies of base/derived classes, all
    conforming to some abstract idea. (hence the `abstract methods`, above)

    So, in an extension to the original example::

        class Fruit(object):
            __metaclass__ = _ServiceMeta

            @abstractmethod
            def foo(self):
                pass # won't be called, anyway

        class Orange(Fruit):
            _name = 'orange'

        class Banana(Fruit):
            _name = 'banana'

            def foo(self): return "Banana!"

        o = Orange() # will fail, because 'foo' is not defined
        b = Banana() # will work

    We can still make `Orange` useful, by complementing the missing function::

        class FixOrange(Fruit): #note, NOT 'Orange' as base!
            name = _'orange'
            def foo(self): return "Orange!"

        o = Orange() # caller doesn't need to know about "FixOrange"

    and we can even call these calls by their name! ::

        fruit1 = Base['orange']()
        fruit2 = Base['banana']()

        assert fruit1.foo() == 'Orange!'
        assert fruit2.foo() == 'Banana!'
        assert Fruit.get_class('orange') == FixOrange
"""

class _ServiceMeta(ABCMeta):
    def __new__(mcls, name, bases, namespace):
        svc_base = None
        for b in bases:
            if b is object:
                continue
            svc_classes = getattr(b, '__service_classes', None)
            if svc_classes is not None:
                if svc_base:
                    raise TypeError("Class %s cannot inherit from two _ServiceMeta parents: %s and %s" % \
                            (name, svc_base.__name__, b.__name__))

                svc_base = b
        svc_name = namespace.get('_name', '*')
        if svc_base:
            svc_classes = getattr(svc_base, '__service_classes')
            # we try to find the last known service class of the same name
            known_parent = svc_classes.get(svc_name, None)
            if known_parent is None and namespace.get('_inherit', None):
                known_parent = svc_classes.get(namespace['_inherit'], None)
                if known_parent is None:
                    # We have to bail out, because the new class can not have the
                    # intended parent yet. Trying with '*' would be wrong
                    raise TypeError("Class %s must inherit service %s[%s], which is still unknown" %\
                                    (name, svc_base.__name__, namespace['_inherit']))
            if known_parent is None and svc_name != '*':
                # try again with the '*' baseclass
                known_parent = svc_classes.get('*', None)
            if known_parent is not None:
                # replace the base with the known_parent
                nps = []
                for b in bases:
                    if b is svc_base:
                        nps.append(known_parent)
                    else:
                        nps.append(b)
                bases = tuple(nps)

        else:
            svc_classes = namespace['__service_classes'] = {}
        newcls = super(_ServiceMeta, mcls).__new__(mcls, name, bases, namespace)
        svc_classes[svc_name] = newcls
        return newcls

    def get_class(cls, name=None):
        """Obtain the class for some (named) service

            In a shortened example::

                class Foo(Base):
                    _name = 'foo'

                assert Base.get_class('foo') == Foo
        """
        if name is None:
            name = '*'
        svc_classes = getattr(cls, '__service_classes', None)
        if svc_classes is None:
            for b in cls.__mro__:
                svc_classes = getattr(b, '__service_classes', None)
                if svc_classes is not None:
                    break
        if svc_classes is None:
            # it shall never reach here, because _ServiceMeta ensures inheritance
            # from one base class...
            raise RuntimeError("No base service class for %s" % cls.__name__)
        base_class = svc_classes.get(name, None)
        if base_class is None:
            raise TypeError("No service class with name: %s" % name)
        return base_class

    def list_classes(cls, prefix=None):
        """List the names of registered named services.

            @param prefix       if given, limit the output to services named like `prefix%`
                or, if set to True, all services excluding the '*' one.
        """
        svc_classes = getattr(cls, '__service_classes', None)
        if svc_classes is None:
            for b in cls.__mro__:
                svc_classes = getattr(b, '__service_classes', None)
                if svc_classes is not None:
                    break
        if svc_classes is None:
            # it shall never reach here, because _ServiceMeta ensures inheritance
            # from one base class...
            raise RuntimeError("No base service class for %s" % cls.__name__)
        if prefix is True:
            return [ k for k in svc_classes.keys() if k != '*']
        elif prefix:
            return [ k for k in svc_classes.keys() if k.startswith(prefix)]
        else:
            return svc_classes.keys()

    def __getitem__(cls, name):
        """ Access some class, through the name

            ONLY works for classes, not class objects.
            An alias for get_class()
        """
        return cls.get_class(name)

    def new(cls, *args, **kwargs):
        """Construct a new object of this "logical" class

            Much like calling the ThisClass(*args, **kwargs)  constructor, but
            the class will transparently be the most overriding one for our `_name`
        """
        name = getattr(cls, '_name', '*')
        newcls = cls.get_class(name)
        return newcls(*args, **kwargs)

    def _class_dump(cls):
        """ Returns the hierarchy explanation of known classes

            Only designed for debugging purposes.
            It will return a dict of lists of tuples like::

                { '*': [ (module, name), (module, name), (module, name) ] }

            where '*' is the `_name` of the service,  `name` the name of the class 
            and `module` the name of the module the class is defined at.
            The order of the list is parent-to-baseclass.

            If called against the service baseclass, the dict will contain all known
            _names of services. Otherwise, it will only return the service of the
            class it was called against.
        """

        svc_name = None
        svc_classes = getattr(cls, '__service_classes', None)
        if svc_classes is None:
            svc_name = cls.getattr('_name', '*')
            for b in cls.__mro__:
                svc_classes = getattr(b, '__service_classes', None)
                if svc_classes is not None:
                    break
        if svc_classes is None:
            # it shall never reach here ...
            raise RuntimeError("No base service class for %s.%s" % (cls.__module__, cls.__name__))
        ret = {}
        for sname, klass in svc_classes.items():
            if svc_name is not None and sname != svc_name:
                continue
            kret = ret[sname] = []
            for sm in klass.__mro__:
                if sm is object:
                    break
                kret.append((sm.__module__, sm.__name__))
        return ret

def pretty_class_dump(cls, indent=4, line_sep='\n', delim=':', name_delim='= '):
    """Formats the output of _class_dump() as a string
    """
    ret = []
    sindent = ' ' * indent
    for sname, slist in cls._class_dump().items():
        if ret: ret += line_sep
        ret += [sindent, sname, name_delim]
        for m, c in slist:
            ret += [m,'.', c, delim]
        ret.pop() # the trailing delim
    return ''.join(ret)

__all__ = [ '_ServiceMeta', 'pretty_class_dump', 'abstractmethod']

#eof
