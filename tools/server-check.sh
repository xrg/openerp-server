#!/bin/bash

# This script should be autonomous, not require any external
# conf files. However, it will try to read the server's conf

get_ini ()
{
    eval $( cat "$1" | grep  "^db_\(user\|password\|name\|host\|port\) *=" | \
	sed 's/False//;s/\([^ ]*\) *= *\(.*\)$/\U\1\E=\2/' )
}

DEVEL_MODE=
SUCMD=
DB_HOST=
DB_PORT=
DB_USER=openerp
DB_PASSWORD=
IN_SU=

PG_ROOT=postgres

while [ -n "$1" ] ; do
    case "$1" in
    -d)
	DEVEL_MODE=y
	echo "Devel mode!"
	;;
    -s)
	SUCMD="su $PG_ROOT - "
	;;
    -h)
	DB_HOST=$2
	shift 1
	;;
    -p)
	DB_PORT=$2
	shift 1
	;;
    -U)
	DB_USER=$2
	shift 1
	;;
    -W)
	DB_PASSWORD=$2
	shift 1
	;;
    --in-su)
	IN_SU=y
	;;
    esac
    shift 1
done

if [ -f "/var/run/openerp-server-checked" ] && [ -z "$DEVEL_MODE" ] ; then
    # only run this if the magic file is there
    exit 0
fi

if [ -n "$DEVEL_MODE" ] && [ -r ~/openerp-server.conf ] ; then
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

if [ -n "$SUCMD"  ] ; then
    CMD="$0 --in-su"
    if [ -n "$DB_HOST" ] ; then 
	CMD="$CMD -h $DB_HOST"
    fi
    if [ -n "$DB_PORT" ] ; then
	CMD="$CMD -p $DB_PORT"
    fi
    if [ -n "$DB_PASSWORD" ] ; then
	CMD="$CMD -W $DB_PASSWORD"
    fi
    su $PG_ROOT -c "$CMD" || exit "$?"
    
    if [ -z "$DEVEL_MODE" ] ; then
	touch "/var/run/openerp-server-checked"
    fi
    exit 0
fi

if ! (psql -qt -U $PG_ROOT $DB_CONNS -c "SELECT usename FROM pg_user WHERE usename = '$DB_USER';" | \
	grep $DB_USER > /dev/null) ; then
	if ! $SUCMD createuser -U $PG_ROOT $DB_CONNS -S -d -R -l $DB_USER < /dev/null ; then
		echo "Failed to create user $DB_USER"
		exit 1
	fi
else
	echo "User $DB_USER already exists."
fi

echo "OK"
if [ -z "$DEVEL_MODE" ] && [ -z "$IN_SU" ] ; then
    touch "/var/run/openerp-server-checked"
fi

#eof
