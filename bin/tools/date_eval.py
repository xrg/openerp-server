#!/usr/bin/python
# -*- coding: utf-8 -*-

#.apidoc title: Date Evaluation from string
import re
import time
import datetime
from dateutil.relativedelta import relativedelta

re_abstimes = { 'now': datetime.datetime.now,
                'today': datetime.date.today,
                'tomorrow': lambda: (datetime.date.today() + datetime.timedelta(1)),
                'yesterday': lambda: (datetime.date.today() - datetime.timedelta(1)),
                }

rel_units = { 'yr' : 'Y',
            'year': 'Y',
            'years': 'Y',
            'month': 'M',
            'months': 'M',
            'mo': 'M',
            'd': 'D',
            'day': 'D',
            'days': 'D',
            'h': 3600,
            'hr': 3600,
            'hour': 3600,
            'hours': 3600,
            'm': 60,
            'min': 60,
            'minute': 60,
            'minutes': 60,
            's': 1,
            'sec' : 1,
            'second': 1,
            'seconds': 1,
            }

re_dateeval = re.compile(r"(?P<abs>" + '|'.join(re_abstimes) +")"
        r"|(?:(?P<rel>(?:\+|-)[0-9]+)(?P<rel_unit>" + '|'.join(rel_units)+ "))"
        r"|(?: ?\bon (?P<date_last>last))"
        r"|(?: ?\bon ?(?P<date>[0-9]{1,2}(?:/[0-9]{1,2}(?:/[0-9]{2,4})?)?))"
        r"|(?: ?\bat ?(?P<time>[0-9]{1,2}(?::[0-9]{2}(?::[0-9]{2})?)?))"
        r"| +", re.I)

__out_fmt_fns = {'datetime': lambda dt: dt,
        'date': lambda dt: dt.date(),
        'time': lambda dt: dt.time(),
        'datetime_str': lambda dt: dt.strftime('%Y-%m-%d %H:%M:%S'),
        'date_str': lambda dt: dt.strftime('%Y-%m-%d'),
        'time_str': lambda dt: dt.strftime('%H:%M:%S'),
        }

def date_eval(rstr, cur_time=None):
    """ Evaluate an textual representation of date/time into a datetime structure
    
        @param rstr the string representation
        @return a datetime.datetime object

        The representation is *strictly* in English.

        The parser is a loop that parses the expression left-to-right, manipulating
        the computed timestamp at each step. So the order of sub-expressions is
        important!

        Possible sub-expressions:
        
        :Absolute:
            Currenct clock:
              - now : Current timestamp of the computer clock
              - today : Midnight at current date: 02/03/2011 00:00:00
              - tomorrow : Midnight at next date
              - yesterday : Midnight of previous date
        :Relative (to previous sub-expression):
            Like: ``+/-Num<unit>`` where Num is a number
            and unit can be one of:
              - year(s)
              - month(s)
              - day(s)
              - h[our(s)]
              - m[inute(s)]
              - s[ec[ond(s)]]
        :Date/time:
            A partial or full date or time can be applied to specify
            date and/or time:
              - on ``DD[/MM[/YY]]`` : specify DD=day, MM=month or even YY=year
              - at ``HH[:mm[:ss]]`` : specify HH=hour, mm=minute or even ss=seconds

        Examples::

            now +3days -2min
            today at 13:45:00
            on 15-04 at 13:45
            today +1year at 12:30
    """
    if cur_time is None:
        cur_time = datetime.datetime.now()
    else:
        if not isinstance(cur_time, datetime.datetime):
            raise TypeError("current date must be given in datetime.datetime")

    for m in re_dateeval.finditer(rstr):
        if m.group('abs'):
            cur_time = re_abstimes[m.group('abs')]()
            if not isinstance(cur_time, datetime.datetime):
                cur_time = datetime.datetime.fromordinal(cur_time.toordinal())
            
        elif m.group('rel'):
            mrel = int(m.group('rel')[1:])
            if m.group('rel')[0] == '-':
                mrel = 0 - mrel
            mun = rel_units[m.group('rel_unit')]
            if mun == 'Y':
                drel = relativedelta(years=mrel)
            elif mun == 'M':
                drel = relativedelta(months=mrel)
            elif mun == 'D':
                drel = datetime.timedelta(days=mrel)
            else:
                drel = mrel * datetime.timedelta(seconds=mun)

            cur_time = cur_time + drel
        elif m.group('date_last'):
            cur_time = cur_time + relativedelta(day=31)
        elif m.group('date'):
            dli = map(int, m.group('date').split('/'))
            if len(dli) == 2:
                dli += [cur_time.year,]
            elif len(dli) == 1:
                dli += [cur_time.month, cur_time.year]
            cur_time = cur_time + relativedelta(day=dli[0], month=dli[1], year=dli[2])
        elif m.group('time'):
            dli = map(int, m.group('time').split(':'))
            if len(dli) == 2:
                dli += [cur_time.second,]
            elif len(dli) == 1:
                dli += [cur_time.minute, cur_time.second]
            cur_time = datetime.datetime.combine(cur_time.date(), datetime.time(dli[0],dli[1],dli[2]))
        else:
            pass

    return cur_time

def lazy_date_eval(rstr, out_fmt='datetime'):
    """Lazy version of date_eval(), returning a callable function
    
        This one will parse and compute the expression, but at the end produce a *callable*
        that will yield datetimes based on the callable execution's current timestamp
    """
    
    steps = []
    for m in re_dateeval.finditer(rstr):
        if m.group('abs'):
            steps.append(('abs',re_abstimes[m.group('abs')]))
        elif m.group('rel'):
            mrel = int(m.group('rel')[1:])
            if m.group('rel')[0] == '-':
                mrel = 0 - mrel
            mun = rel_units[m.group('rel_unit')]
            if mun == 'Y':
                drel = relativedelta(years=mrel)
            elif mun == 'M':
                drel = relativedelta(months=mrel)
            elif mun == 'D':
                drel = datetime.timedelta(days=mrel)
            else:
                drel = mrel * datetime.timedelta(seconds=mun)
            steps.append(('rel', drel))
        elif m.group('date_last'):
            steps.append(('date_last', ()))
        elif m.group('date'):
            dli = map(int, m.group('date').split('/'))
            steps.append(('date', dli))
        elif m.group('time'):
            dli = map(int, m.group('time').split(':'))
            steps.append(('time', dli))
        else:
            pass

    out_fn = __out_fmt_fns[out_fmt]

    def __lazy(*args, **kwargs):
        cur_time = kwargs.get('cur_time', datetime.datetime.now())
        for r1, r2 in steps:
            if r1 == 'abs':
                cur_time = r2()
                if not isinstance(cur_time, datetime.datetime):
                    cur_time = datetime.datetime.fromordinal(cur_time.toordinal())
            elif r1 == 'rel':
                cur_time = cur_time + r2
            elif r1 == 'date_last':
                cur_time = cur_time + relativedelta(day=31)
            elif r1 == 'date':
                dli = list(r2) # copy it!
                if len(dli) == 2:
                    dli += [cur_time.year,]
                elif len(dli) == 1:
                    dli += [cur_time.month, cur_time.year]
                cur_time = cur_time + relativedelta(day=dli[0], month=dli[1], year=dli[2])
            elif  r1 == 'time':
                dli = list(r2)
                if len(dli) == 2:
                    dli += [cur_time.second,]
                elif len(dli) == 1:
                    dli += [cur_time.minute, cur_time.second]
                cur_time = datetime.datetime.combine(cur_time.date(), datetime.time(dli[0],dli[1],dli[2]))
            else:
                raise RuntimeError("Incorrect step: %s" % r1)
        
        return out_fn(cur_time)

    return __lazy

#eof
