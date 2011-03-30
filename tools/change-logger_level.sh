#!/bin/bash

ADMIN_PASSWD='admin'
method_1() {
	cat '-' << EOF
<xml>
<methodCall>
	<methodName>set_logger_level</methodName>
	<params>
		<param><value><string>$ADMIN_PASSWD</string></value>
		</param>
		<param>
		<value><string>$1</string></value>
		<value><string>$2</string></value>
		</param>
	</params>
</methodCall>
</xml>
EOF
}
LEVEL=10

if [ -z "$1" ] ; then
	echo "Usage: $0 <logger> [<level>]"
	echo
	echo "Where <logger> is the specific one, like 'osv', 'db.connection' etc."
	echo "      and <level> a numeric val, 10= debug "
	echo
	exit 1
fi

if [ -n "$2" ] ; then LEVEL=$2 ; fi

method_1 $1 $LEVEL | POST -c 'text/xml' http://localhost:8069/xmlrpc/common
#eof
