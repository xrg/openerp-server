#!/bin/bash

# This script should be autonomous, not require any external
# conf files. However, it will try to read the server's conf

get_ini ()
{
    eval $( cat "$1" | grep  "^db_\(user\|password\|name\|host\|port\) *=" | \
	sed 's/False//;s/\([^ ]*\) *= *\(.*\)$/\U\1\E=\2/' )
}

DEVEL_MODE=
if [ "$1" == "-d" ] ; then
    DEVEL_MODE=y
    echo "Devel mode!"
fi

if [ ! -f "/var/run/openerp-server-check" ] && [ -z "$DEVEL_MODE" ] ; then
    # only run this if the magic file is there
    exit 0
fi

DB_HOST=
DB_PORT=
DB_USER=openerp
DB_PASSWORD=

PG_ROOT=postgres

if [ -n "$DEVEL_MODE" ] && [ -f ~/openerp-server.conf ] ; then
    echo "Parsing" ~/openerp-server.conf
    get_ini ~/openerp-server.conf
elif [ -f "/etc/openerp-server.conf" ] ; then
    get_ini "/etc/openerp-server.conf"
else
    echo "No config file, using defaults"
fi

if [ -n "$DEVEL_MODE" ] ; then
    echo "Using:"
    echo "DB_HOST=" $DB_HOST
    echo "DB_PORT=" $DB_PORT
    echo "DB_USER=" $DB_USER
fi

DB_CONNS= 
if [ -n "$DB_HOST" ] ; then
    DB_CONNS+=" --host $DB_HOST"
fi
if [ -n "$DB_PORT" ] ; then
    DB_CONNS+=" --port $DB_PORT"
fi


if ! (psql -qt -U $PG_ROOT $DB_CONNS -c "SELECT usename FROM pg_user WHERE usename = '$DB_USER';" | \
	grep $DB_USER > /dev/null) ; then
	if ! createuser -U $PG_ROOT $DB_CONNS -S -D -R -l $DB_USER < /dev/null ; then
		echo "Failed to create user $DB_USER"
		exit 1
	fi
else
	echo "User $DB_USER already exists."
fi

echo "OK"
if [ -z "$DEVEL_MODE" ] ; then
    rm -f "/var/run/openerp-server-check"
fi

#eof
