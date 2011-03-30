#!/bin/bash

ADMIN_PASSWD='admin'
method_1() {
	cat '-' << EOF
<xml>
<methodCall>
	<methodName>get_loglevel</methodName>
	<params>
		<param><value><string>$ADMIN_PASSWD</string></value>
		</param>
		<param>
		<value><string>$1</string></value>
		</param>
	</params>
</methodCall>
</xml>
EOF
}

if [ "$1" == '-h' ] ; then
	echo "Usage: $0 <logger> "
	echo
	echo "Where <logger> is the specific one, like 'osv', 'db.connection' etc."
	echo
	exit 1
fi

method_1 "$1" | POST -c 'text/xml' http://localhost:8069/xmlrpc/common
#eof
